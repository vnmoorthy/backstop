"""Deterministic denial parser (SIM) — genuine offline X12/EOB extraction.

This is the ``sim`` binding of :class:`~backstop.ports.denial_parser_port.\
DenialParserPort`. It performs **real** local work, never an echo, and is the
universal fallback: it ``supports`` every :class:`ArtifactKind`, so EDI always
parses deterministically and an Unsiloed outage never blocks a denial.

Two genuine engines sit behind the one port:

* **EDI X12** (``X12_835`` / ``X12_837`` / ``X12_277``): a real segment walker
  that splits on the X12 element/segment separators and reads ``CLP`` (claim
  payment), ``CAS`` (claim adjustment → the CARC denial codes + amounts),
  ``NM1`` (entity names + NPI in NM109), ``DTM`` (dates, DOS qualifier 472),
  ``MIA`` / ``MOA`` (RARC remark codes) and ``SVC`` (service lines). Cleanly
  parsed segments get ``confidence == 1.0`` (deterministic ⇒ certain) and the
  literal segment string as provenance for the evidence timeline.
* **Text/layout heuristics** (``EOB`` / ``CMS1500`` / ``UB04`` / ``PDF_IMAGE``
  supplied as text or pre-OCR'd bytes): anchored regexes over the canonical
  paper-form geometry plus a CARC/RARC table lookup that expands a bare code
  like ``CO-197`` into its canonical reason. Confidence is heuristic
  (``0.6`` to ``0.9``) so the human-review gate trips appropriately.

It depends only on the in-repo :mod:`backstop.domain.carc_table` — fully offline
and deterministic. No vendor SDK, no network, no filesystem read of user input
(the artifact arrives as in-memory bytes on :class:`ParseRequest`).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from backstop.adapters.unsiloed import _denial_schema as fields
from backstop.adapters.unsiloed.errors import UnsupportedArtifact
from backstop.domain.carc_table import CarcTable, load_carc_table
from backstop.domain.enums import ArtifactKind
from backstop.ports.denial_parser_port import (
    DenialExtraction,
    ExtractedField,
    ParseRequest,
)

__all__ = ["DeterministicDenialParserAdapter"]

# Default confidence floor; below it ``needs_human_review`` trips. Mirrors the
# ``UNSILOED_CONFIDENCE_FLOOR`` config default so sim and real share one gate.
_DEFAULT_CONFIDENCE_FLOOR = 0.85

# Artifact kinds the EDI engine handles.
_EDI_KINDS = frozenset(
    {ArtifactKind.X12_835, ArtifactKind.X12_837, ArtifactKind.X12_277}
)

# Deterministic-parse confidence for a cleanly read EDI segment.
_EDI_CONFIDENCE = 1.0

# DOS date qualifier in a DTM segment (472 == "service date").
_DOS_QUALIFIER = "472"
# Composite-element separator inside a single X12 element (e.g. HC:99285).
_COMPOSITE_SEP = ":"
# NPI reference qualifier in NM108.
_NPI_QUALIFIER = "XX"
# Entity-identifier codes in NM101 for billing vs rendering provider.
_BILLING_ENTITY = "85"
_RENDERING_ENTITY = "82"

# Text heuristics: a CARC code as written on an EOB, e.g. "CO-197" / "PR 96".
_EOB_CARC_RE = re.compile(r"\b([A-Z]{2})[\s\-]?(\d{1,3})\b")
# An explicit RARC remark, e.g. "N130" / "M127".
_EOB_RARC_RE = re.compile(r"\b([MN]\d{1,3})\b")
_AMOUNT_RE = re.compile(r"\$?\s*([\d,]+\.\d{2})")
_NPI_RE = re.compile(r"\b(\d{10})\b")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
# A loose claim-number candidate (used only when no labelled claim is found).
_CLAIM_RE = re.compile(r"\b((?:CLM[-\s]?)?[A-Z]{0,3}[-\s]?\d{2,}[-\d]*)\b")
# A claim number behind an explicit "Claim #/No./Number" label.
_LABELLED_CLAIM_RE = re.compile(
    r"[Cc]laim(?:\s*(?:#|No\.?|Number))?\s*[:#]?\s*([A-Z0-9][\w-]{4,})"
)
_MEMBER_RE = re.compile(r"[Mm]ember(?:\s*ID)?\s*[:#]?\s*([A-Z]?\d{6,})")
_PAYER_RE = re.compile(
    r"^([A-Z][A-Za-z&. ]+?)\s+(?:EXPLANATION|EOB|Health|Insurance)", re.MULTILINE
)


def _decode(content: bytes) -> str:
    """Decode artifact bytes to text, tolerating non-UTF-8 OCR output."""
    return content.decode("utf-8", errors="replace")


class DeterministicDenialParserAdapter:
    """Offline, deterministic :class:`DenialParserPort` implementation.

    Args:
        carc_table: Optional pre-loaded CARC/RARC lexicon; loaded from the
            packaged ``data/carc_rarc.json`` when omitted.
        confidence_floor: Overall-confidence threshold below which
            ``needs_human_review`` is ``True`` (defaults to ``0.85``).
    """

    def __init__(
        self,
        carc_table: Optional[CarcTable] = None,
        confidence_floor: float = _DEFAULT_CONFIDENCE_FLOOR,
    ) -> None:
        """Bind the CARC lexicon and human-review confidence floor."""
        self._carc = carc_table if carc_table is not None else load_carc_table()
        self._floor = confidence_floor

    # ----------------------------------------------------------------- #
    # Port surface.
    # ----------------------------------------------------------------- #
    def supports(self, kind: ArtifactKind) -> bool:
        """Return ``True`` for every kind — the sim is the universal fallback."""
        return isinstance(kind, ArtifactKind)

    async def parse(self, req: ParseRequest) -> DenialExtraction:
        """Parse the in-memory artifact bytes into a structured extraction.

        Routes EDI kinds to the X12 engine and document kinds to the text/layout
        engine. Never opens a server-side path: ``req.content`` is the artifact.
        """
        text = _decode(req.content)
        if req.kind in _EDI_KINDS:
            extracted = self._parse_edi(text)
        elif isinstance(req.kind, ArtifactKind):
            extracted = self._parse_text(text)
        else:  # pragma: no cover - defensive; supports() guards callers
            raise UnsupportedArtifact(req.kind)

        fields_tuple = tuple(extracted)
        overall = self._aggregate_confidence(fields_tuple)
        return DenialExtraction(
            kind=req.kind,
            fields=fields_tuple,
            overall_confidence=overall,
            needs_human_review=overall < self._floor,
        )

    # ----------------------------------------------------------------- #
    # EDI X12 engine.
    # ----------------------------------------------------------------- #
    def _parse_edi(self, text: str) -> List[ExtractedField]:
        """Walk X12 segments and emit canonical fields with literal provenance."""
        segments = self._split_segments(text)
        out: List[ExtractedField] = []
        seen: Dict[str, bool] = {}

        def emit(
            name: str,
            value: str,
            segment: str,
            tag: str,
            confidence: float = _EDI_CONFIDENCE,
        ) -> None:
            if not value or seen.get(name):
                return
            seen[name] = True
            out.append(
                ExtractedField(
                    name=name,
                    value=value,
                    confidence=confidence,
                    provenance={"segment": tag, "source_text": segment, "engine": "x12"},
                )
            )

        carc_codes: List[str] = []
        rarc_codes: List[str] = []
        service_lines: List[str] = []
        carc_segments: List[str] = []

        for raw, elements in segments:
            tag = elements[0] if elements else ""
            if tag == "CLP":
                # CLP01 claim#, CLP03 billed, CLP04 paid, CLP05 patient resp.
                emit(fields.CLAIM_NUMBER, _el(elements, 1), raw, "CLP01")
                emit(fields.BILLED_AMOUNT, _el(elements, 3), raw, "CLP03")
                emit(fields.PAID_AMOUNT, _el(elements, 4), raw, "CLP04")
                emit(fields.PATIENT_RESPONSIBILITY, _el(elements, 5), raw, "CLP05")
            elif tag == "CAS":
                # CAS*<group>*<carc>*<amt>[*<carc>*<amt>...] — repeating triplets
                # of (reason code, amount, [quantity]) after the group code.
                for i in range(2, len(elements), 3):
                    code = _el(elements, i)
                    if code:
                        carc_codes.append(code)
                        carc_segments.append(raw)
            elif tag == "NM1":
                entity = _el(elements, 1)
                qualifier = _el(elements, 8)
                npi = _el(elements, 9)
                if qualifier == _NPI_QUALIFIER and npi:
                    if entity == _BILLING_ENTITY:
                        emit(fields.BILLING_NPI, npi, raw, "NM109")
                    elif entity == _RENDERING_ENTITY:
                        emit(fields.RENDERING_NPI, npi, raw, "NM109")
                # IL == subscriber/member, QC == patient.
                if entity in ("IL", "QC"):
                    member = _el(elements, 9) or _el(elements, 4)
                    if member:
                        emit(fields.MEMBER_ID, member, raw, "NM109")
            elif tag == "N1":
                # N1*PR*<payer name> — payer identification in the 835 header.
                if _el(elements, 1) == "PR":
                    emit(fields.PAYER_NAME, _el(elements, 2), raw, "N102")
                    payer_id = _el(elements, 4)
                    if payer_id:
                        emit(fields.PAYER_ID, payer_id, raw, "N104")
            elif tag == "DTM":
                if _el(elements, 1) == _DOS_QUALIFIER:
                    emit(fields.DATE_OF_SERVICE, _el(elements, 2), raw, "DTM02")
            elif tag in ("MIA", "MOA"):
                rarc_codes.extend(self._remark_codes(elements))
            elif tag in ("SVC", "SV1", "SV2"):
                line = self._service_line(elements)
                if line:
                    service_lines.append(line)

        self._emit_codes(out, carc_codes, carc_segments, rarc_codes)
        self._emit_service_lines(out, service_lines)
        self._emit_denial_reason(out, carc_codes)
        self._backfill_required(out, engine="x12")
        return out

    def _emit_codes(
        self,
        out: List[ExtractedField],
        carc_codes: List[str],
        carc_segments: List[str],
        rarc_codes: List[str],
    ) -> None:
        """Append the CARC and RARC code list fields if any were found."""
        if carc_codes:
            out.append(
                ExtractedField(
                    name=fields.CARC_CODES,
                    value=",".join(carc_codes),
                    confidence=_EDI_CONFIDENCE,
                    provenance={
                        "segment": "CAS",
                        "source_text": " | ".join(dict.fromkeys(carc_segments)),
                        "engine": "x12",
                    },
                )
            )
        if rarc_codes:
            out.append(
                ExtractedField(
                    name=fields.RARC_CODES,
                    value=",".join(dict.fromkeys(rarc_codes)),
                    confidence=_EDI_CONFIDENCE,
                    provenance={"segment": "MIA/MOA", "engine": "x12"},
                )
            )

    def _emit_service_lines(
        self, out: List[ExtractedField], service_lines: List[str]
    ) -> None:
        """Append the service-line summary field if any lines were parsed."""
        if service_lines:
            out.append(
                ExtractedField(
                    name=fields.SERVICE_LINES,
                    value=";".join(service_lines),
                    confidence=_EDI_CONFIDENCE,
                    provenance={"segment": "SVC", "engine": "x12"},
                )
            )

    # ----------------------------------------------------------------- #
    # Text / layout engine.
    # ----------------------------------------------------------------- #
    def _parse_text(self, text: str) -> List[ExtractedField]:
        """Heuristic regex/layout parse for EOB / CMS-1500 / UB-04 text."""
        out: List[ExtractedField] = []

        carc_codes: List[str] = []
        carc_provenance = ""
        for match in _EOB_CARC_RE.finditer(text):
            group, code = match.group(1), match.group(2)
            # Only accept group codes that are real X12 adjustment groups.
            if group in ("CO", "PR", "OA", "PI", "CR"):
                carc_codes.append(code)
                if not carc_provenance:
                    carc_provenance = match.group(0)
        carc_codes = list(dict.fromkeys(carc_codes))
        if carc_codes:
            out.append(
                ExtractedField(
                    name=fields.CARC_CODES,
                    value=",".join(carc_codes),
                    confidence=0.82,
                    provenance={"regex": "carc", "source_text": carc_provenance, "engine": "text"},
                )
            )

        rarc = [m.group(1) for m in _EOB_RARC_RE.finditer(text)]
        rarc = list(dict.fromkeys(rarc))
        if rarc:
            out.append(
                ExtractedField(
                    name=fields.RARC_CODES,
                    value=",".join(rarc),
                    confidence=0.78,
                    provenance={"regex": "rarc", "engine": "text"},
                )
            )

        claim = _CLAIM_RE.search(text)
        member = _MEMBER_RE.search(text)
        claim_m = _LABELLED_CLAIM_RE.search(text)
        dos = _DATE_RE.search(text)
        amounts = _AMOUNT_RE.findall(text)
        npis = _NPI_RE.findall(text)
        payer = _PAYER_RE.search(text)

        if payer:
            out.append(
                self._text_field(fields.PAYER_NAME, payer.group(1).strip(), 0.7, "payer")
            )
        if claim_m:
            out.append(
                self._text_field(fields.CLAIM_NUMBER, claim_m.group(1).strip(), 0.8, "claim")
            )
        elif claim:
            out.append(
                self._text_field(fields.CLAIM_NUMBER, claim.group(1).strip(), 0.65, "claim")
            )
        if member:
            out.append(self._text_field(fields.MEMBER_ID, member.group(1).strip(), 0.8, "member"))
        if dos:
            out.append(self._text_field(fields.DATE_OF_SERVICE, dos.group(1), 0.75, "dos"))
        if amounts:
            out.append(
                self._text_field(
                    fields.BILLED_AMOUNT, amounts[0].replace(",", ""), 0.7, "amount"
                )
            )
        if len(npis) >= 1:
            out.append(self._text_field(fields.BILLING_NPI, npis[0], 0.7, "npi"))
        if len(npis) >= 2:
            out.append(self._text_field(fields.RENDERING_NPI, npis[1], 0.65, "npi"))

        self._emit_denial_reason(out, carc_codes, base_confidence=0.75)
        self._backfill_required(out, engine="text")
        return out

    def _text_field(self, name: str, value: str, confidence: float, tag: str) -> ExtractedField:
        """Build a text-engine :class:`ExtractedField` with regex provenance."""
        return ExtractedField(
            name=name,
            value=value,
            confidence=confidence,
            provenance={"regex": tag, "engine": "text"},
        )

    # ----------------------------------------------------------------- #
    # Shared helpers.
    # ----------------------------------------------------------------- #
    def _emit_denial_reason(
        self,
        out: List[ExtractedField],
        carc_codes: List[str],
        base_confidence: float = _EDI_CONFIDENCE,
    ) -> None:
        """Expand the primary CARC code into its canonical reason via the table.

        Unknown codes degrade gracefully to ``"Unmapped code <code>"`` with a
        lowered confidence so the human-review gate trips for unrecognised
        denials.
        """
        if any(f.name == fields.DENIAL_REASON for f in out):
            return
        if not carc_codes:
            return
        primary = carc_codes[0]
        entry = self._carc.get(primary)
        if entry is not None:
            reason = entry.canonical_reason
            confidence = base_confidence
            provenance = {"carc": primary, "lookup": "carc_table"}
        else:
            reason = f"Unmapped code {primary}"
            confidence = min(base_confidence, 0.5)
            provenance = {"carc": primary, "lookup": "unmapped"}
        out.append(
            ExtractedField(
                name=fields.DENIAL_REASON,
                value=reason,
                confidence=confidence,
                provenance=provenance,
            )
        )

    def _backfill_required(self, out: List[ExtractedField], engine: str) -> None:
        """Guarantee the required field set is present (empty value if absent).

        A fully-populated :class:`DenialExtraction` is part of the port contract;
        when a heuristic parse cannot find a required field we still emit it with
        an empty value, a low confidence, and a ``"missing"`` provenance tag so
        the review gate and the evidence timeline see the gap explicitly.
        """
        present = {f.name for f in out}
        for name in fields.REQUIRED_FIELDS:
            if name not in present:
                out.append(
                    ExtractedField(
                        name=name,
                        value="",
                        confidence=0.0,
                        provenance={"engine": engine, "status": "missing"},
                    )
                )

    def _aggregate_confidence(self, extracted: Tuple[ExtractedField, ...]) -> float:
        """Mean confidence over non-empty fields, clamped to ``[0, 1]``."""
        scored = [f.confidence for f in extracted if f.value]
        if not scored:
            return 0.0
        mean = sum(scored) / len(scored)
        return max(0.0, min(1.0, mean))

    @staticmethod
    def _split_segments(text: str) -> List[Tuple[str, List[str]]]:
        """Split an X12 interchange into ``(raw_segment, elements)`` pairs.

        Uses the literal X12 separators (``~`` segment terminator, ``*`` element
        separator). Whitespace and any trailing newline padding between segments
        is stripped so hand-authored fixtures parse identically to wire data.
        """
        out: List[Tuple[str, List[str]]] = []
        for chunk in text.replace("\n", "").split("~"):
            raw = chunk.strip()
            if not raw:
                continue
            out.append((raw, raw.split("*")))
        return out

    @staticmethod
    def _remark_codes(elements: List[str]) -> List[str]:
        """Pull RARC remark codes (``N###`` / ``M###``) out of a MIA/MOA seg."""
        codes: List[str] = []
        for el in elements[1:]:
            if el and (el[0] in ("N", "M")) and el[1:].isdigit():
                codes.append(el)
        return codes

    @staticmethod
    def _service_line(elements: List[str]) -> str:
        """Render a service line from an SVC/SV1/SV2 composite procedure code."""
        proc = _el(elements, 1)
        if not proc:
            return ""
        if _COMPOSITE_SEP in proc:
            proc = proc.split(_COMPOSITE_SEP, 1)[1]
        billed = _el(elements, 2)
        return f"{proc}@{billed}" if billed else proc


def _el(elements: List[str], idx: int) -> str:
    """Return element *idx* of a split segment, or ``""`` if out of range."""
    if 0 <= idx < len(elements):
        return elements[idx].strip()
    return ""

"""Appeal-letter writer.

Drafts a verbatim, audit-grade appeal letter from the swarm's transcripts, the
overturning contradiction, and the Moss rebuttal. The letter QUOTES the reps
verbatim with turn references (compliance: never paraphrase the record), cites the
rebuttal source, and is rendered to a PDF. A human appeals nurse signs it; the
swarm never files.
"""
from __future__ import annotations

import os
from pathlib import Path

from .models import AppealLetter, AppealSpec, Contradiction, Rebuttal

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT = _REPO_ROOT / "data" / "out"


def _body_md(spec: AppealSpec, contradiction: Contradiction, rebuttal: Rebuttal | None) -> str:
    d = spec.denial
    rb_line = (
        f'Per runbook {rebuttal.source_id}, the corrective action ("{rebuttal.magic_words}") '
        f"overturns this denial pattern in {round(rebuttal.win_rate * 100)}% of comparable cases."
        if rebuttal
        else ""
    )
    # the basis paragraph + requested action depend on the denial code
    _BASIS = {
        "CO-197": (
            "no prior authorization was on file",
            "The authorization exists and covers the date of service; the denial reflects a lookup performed against the billing NPI rather than the rendering NPI under which the authorization is filed.",
            "The precertification on file satisfies the plan's authorization requirement.",
        ),
        "CO-16": (
            "the claim was missing required information",
            "The required information was present on the originally submitted claim and was dropped at the payer's intake, as documented in the call record above.",
            "The claim contains all required elements and should be adjudicated on the merits.",
        ),
        "CO-50": (
            "the service was not medically necessary",
            "The clinical record documents that the plan's medical-necessity criteria were met, as confirmed on the call above.",
            "The documented clinical criteria satisfy the plan's medical-necessity requirement.",
        ),
    }
    _generic = (
        "the stated denial reason does not hold",
        "The payer's own record, quoted above, contradicts the stated basis for the denial.",
        "The claim should be reprocessed and paid on the merits.",
    )
    _basis0, _basis1, _basis2 = _BASIS.get((d.denial_code or "").upper(), _generic)
    return f"""# Appeal of Claim Denial — {d.denial_code}

**Payer:** {d.payer}  ·  **Plan:** {d.plan}  ·  **State:** {d.state}
**Claim:** {d.claim_id}  ·  **DOS:** {d.date_of_service}  ·  **CPT:** {", ".join(d.cpt)}  ·  **Billed:** ${d.billed_amount:,.2f}
**Member:** {d.member_id}  ·  **Rendering NPI:** {d.rendering_npi}  ·  **Billing NPI:** {d.billing_npi}
**Appeal filing deadline:** {spec.sol_deadline}

## Basis for appeal

This claim was denied **{d.denial_code}** on the stated basis that {_basis0}. The
record obtained on the payer's own provider line contradicts that basis.

**Provider-services representative stated (verbatim, turn {contradiction.rep_turn_id}):**
> "{contradiction.claim}"

**However, the payer's own records confirm (verbatim, turn {contradiction.evidence_turn_id}):**
> "{contradiction.evidence}"

{_basis1} {rb_line}

## Requested action

Reprocess and pay claim {d.claim_id} for date of service {d.date_of_service}. {_basis2}

---
*Prepared by Backstop from the verbatim call record. Requires licensed appeals-nurse
review and signature before filing. Citations: {", ".join([c for c in ([rebuttal.source_id] if rebuttal else []) ] + ["call-record"]) }.*
"""


def _render_pdf(body_md: str, out_path: Path) -> bool:
    """Render the letter to PDF via reportlab. Returns True on success."""
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except Exception:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # render to a unique temp file then atomically replace, so a /files reader
    # can never be served a half-written PDF mid-build
    tmp_path = out_path.with_name(out_path.stem + f".{os.getpid()}.tmp.pdf")
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(tmp_path), pagesize=LETTER, topMargin=0.8 * inch, bottomMargin=0.8 * inch)
    flow = []
    for raw in body_md.splitlines():
        line = raw.rstrip()
        if not line:
            flow.append(Spacer(1, 6))
            continue
        if line.startswith("# "):
            flow.append(Paragraph(line[2:], styles["Title"]))
        elif line.startswith("## "):
            flow.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("> "):
            flow.append(Paragraph(f'<i>{line[2:]}</i>', styles["Italic"]))
        else:
            safe = line.replace("**", "")
            flow.append(Paragraph(safe, styles["BodyText"]))
    doc.build(flow)
    os.replace(tmp_path, out_path)  # atomic
    return out_path.exists()


def draft_appeal(
    spec: AppealSpec,
    contradiction: Contradiction,
    transcripts_by_agent: dict | None = None,
    rebuttal: Rebuttal | None = None,
    appeal_id: str | None = None,
) -> AppealLetter:
    body = _body_md(spec, contradiction, rebuttal)
    # key the PDF on the unique server appeal_id so concurrent appeals (even with
    # the same claim_id) never collide; fall back to claim_id for direct callers
    appeal_id = appeal_id or f"AP-{spec.denial.claim_id}"
    safe_id = "".join(ch for ch in appeal_id if ch.isalnum() or ch in "-_")
    out_path = _OUT / f"appeal_{safe_id}.pdf"
    rendered = _render_pdf(body, out_path)
    citations = ([rebuttal.source_id] if rebuttal else []) + ["call-record"]
    return AppealLetter(
        appeal_id=appeal_id,
        body_md=body,
        citations=citations,
        pdf_path=str(out_path) if rendered else "",
        signed_by_nurse=False,
    )


if __name__ == "__main__":
    from .ivr_sim import sample_denial
    from .models import Contradiction as C
    from .swarm import Concierge

    spec = Concierge.intake(sample_denial())
    contra = C(
        claim="That claim was denied CO-197 — there is no prior authorization on file.",
        evidence="The chart shows pre-auth A4471 on file, approved before the visit.",
        rep_turn_id=10,
        evidence_turn_id=3,
    )
    reb = Rebuttal("CO-197", "Aetna", "Please run the auth-on-file lookup by the RENDERING NPI, not the billing NPI.", 0.73, "RB-AETNA-CO197")
    letter = draft_appeal(spec, contra, {}, reb)
    assert "A4471" in letter.body_md and "RENDERING NPI" in letter.body_md, "verbatim quote + magic words missing"
    print("appeal_id:", letter.appeal_id)
    print("pdf:", letter.pdf_path or "(reportlab not installed — md only)")
    print("citations:", letter.citations)
    print("\nletter drafted OK (verbatim quote + magic words present)")

"""Unit + fuzz tests for the local PHI redaction adapter.

The redaction adapter is the SOLE producer of ``RedactedText`` and the system's
PHI egress gatekeeper. These tests assert two things hold even under adversarial,
hypothesis-generated inputs:

* every PHI category the scrubber knows (member IDs, NPIs, SSNs, DOBs, claim
  numbers, names, phones, emails) is masked — no raw identifier survives;
* the masked output never re-trips the adapter's own ``contains_phi`` detector,
  i.e. redaction is idempotent and leaves no surviving PII.

``redact_text`` is the only sanctioned mint of ``RedactedText``, so a passing
suite is the lock test for the whole PHI boundary.
"""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from backstop.adapters.truefoundry.local_redaction_adapter import LocalRedactionAdapter
from backstop.domain.redacted import RedactedText
from backstop.ports.redaction_port import Message, RedactionPort

# Reusable synthetic PHI generators (no real identifiers anywhere).
_member_ids = st.from_regex(r"\A[A-Z]{1,3}[0-9]{8,12}\Z", fullmatch=True)
_npis = st.from_regex(r"\A[0-9]{10}\Z", fullmatch=True)
_ssns = st.from_regex(r"\A[0-9]{3}-[0-9]{2}-[0-9]{4}\Z", fullmatch=True)
_dobs = st.from_regex(r"\A(0[1-9]|1[0-2])/(0[1-9]|1[0-9]|2[0-8])/(19|20)[0-9]{2}\Z", fullmatch=True)
_claims = st.from_regex(r"\A[A-Z]{2,4}[0-9]{6,10}\Z", fullmatch=True)
_phones = st.from_regex(r"\A\([0-9]{3}\) [0-9]{3}-[0-9]{4}\Z", fullmatch=True)
_names = st.sampled_from(
    ["Jane Doe", "John Smith", "Maria Garcia", "Robert Brown", "Emily Davis"]
)


def _adapter() -> LocalRedactionAdapter:
    """Return a fresh stateless redaction adapter."""
    return LocalRedactionAdapter()


# Patterns that would indicate surviving raw PHI in masked output. These are the
# canonical shapes; a masked string must match NONE of them.
_SURVIVING_PHI = (
    re.compile(r"\b[A-Z]{1,3}\d{8,12}\b"),          # member id
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),            # SSN
    re.compile(r"(?<!\d)\d{10}(?!\d)"),              # bare 10-digit / NPI
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),      # US date / DOB
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),  # email
)


def _has_surviving_phi(text: str) -> bool:
    """Return whether any canonical raw-PHI pattern survives in *text*."""
    return any(pat.search(text) for pat in _SURVIVING_PHI)


# --------------------------------------------------------------------------- #
# Type contract.
# --------------------------------------------------------------------------- #
def test_adapter_satisfies_redaction_port() -> None:
    """The adapter is a structural ``RedactionPort``."""
    assert isinstance(_adapter(), RedactionPort)


def test_redact_text_mints_redacted_text() -> None:
    """``redact_text`` returns the sanctioned ``RedactedText`` newtype."""
    out = _adapter().redact_text("no phi here")
    assert isinstance(out, RedactedText)
    assert out.text == "no phi here"


def test_redact_messages_preserves_roles_and_order() -> None:
    """Message redaction keeps role and order, masking only content."""
    adapter = _adapter()
    msgs = [
        Message(role="system", content="You help with appeals."),
        Message(role="user", content="Member W123456789 was denied."),
    ]
    out = adapter.redact_messages(msgs)
    assert [m.role for m in out] == ["system", "user"]
    assert isinstance(out[1].content, RedactedText)
    assert "W123456789" not in out[1].content.text
    assert "[MEMBER_ID]" in out[1].content.text


# --------------------------------------------------------------------------- #
# Each category is masked.
# --------------------------------------------------------------------------- #
@given(member=_member_ids)
def test_member_id_masked(member: str) -> None:
    """A bare alpha-prefixed member id is masked."""
    out = _adapter().redact_text(f"The member id is {member} on file.")
    assert member not in out.text
    assert not _has_surviving_phi(out.text)


@given(npi=_npis)
def test_npi_masked(npi: str) -> None:
    """A cued NPI is masked regardless of checksum validity."""
    out = _adapter().redact_text(f"Provider NPI {npi} submitted the claim.")
    assert npi not in out.text


@given(ssn=_ssns)
def test_ssn_masked(ssn: str) -> None:
    """A dashed SSN is masked."""
    out = _adapter().redact_text(f"SSN {ssn} for verification.")
    assert ssn not in out.text
    assert not _has_surviving_phi(out.text)


@given(dob=_dobs)
def test_dob_masked(dob: str) -> None:
    """A date of birth is masked."""
    out = _adapter().redact_text(f"DOB {dob} per the record.")
    assert dob not in out.text
    assert not _has_surviving_phi(out.text)


@given(claim=_claims)
def test_claim_number_masked(claim: str) -> None:
    """A cued claim number is masked."""
    out = _adapter().redact_text(f"Claim number {claim} was denied.")
    assert claim not in out.text


@given(phone=_phones)
def test_phone_masked(phone: str) -> None:
    """A US phone number is masked."""
    out = _adapter().redact_text(f"Call back at {phone} after noon.")
    assert phone not in out.text


@given(name=_names)
def test_name_masked(name: str) -> None:
    """A cued person name is masked."""
    out = _adapter().redact_text(f"Patient {name} called the provider line.")
    assert name not in out.text


def test_email_masked() -> None:
    """An email address is masked."""
    out = _adapter().redact_text("Reach me at jane.doe@example.org tomorrow.")
    assert "jane.doe@example.org" not in out.text
    assert "[EMAIL]" in out.text


# --------------------------------------------------------------------------- #
# Fuzz: a sentence assembled from several PHI fields leaves NO survivor.
# --------------------------------------------------------------------------- #
@given(
    member=_member_ids,
    npi=_npis,
    ssn=_ssns,
    dob=_dobs,
    claim=_claims,
    phone=_phones,
    name=_names,
    filler=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Zs")), max_size=40),
)
def test_fuzz_no_surviving_pii(
    member: str,
    npi: str,
    ssn: str,
    dob: str,
    claim: str,
    phone: str,
    name: str,
    filler: str,
) -> None:
    """A blob of mixed synthetic PHI is fully scrubbed and stays scrubbed.

    Asserts (1) no individual raw identifier survives, (2) no canonical raw-PHI
    pattern survives, and (3) re-running the detector on the masked output finds
    nothing (idempotent redaction).
    """
    adapter = _adapter()
    raw = (
        f"Patient {name} {filler} member {member} NPI {npi} "
        f"SSN {ssn} DOB {dob} claim {claim} phone {phone}"
    )
    out = adapter.redact_text(raw)
    for secret in (member, npi, ssn, dob, claim, phone, name):
        assert secret not in out.text
    assert not _has_surviving_phi(out.text)
    # Idempotent: the adapter's own predicate must report the masked text clean.
    assert adapter.contains_phi(out.text) is False


@given(
    member=_member_ids,
    npi=_npis,
    ssn=_ssns,
)
def test_redaction_is_idempotent(member: str, npi: str, ssn: str) -> None:
    """Redacting already-redacted text is a fixed point (no further masking)."""
    adapter = _adapter()
    raw = f"member {member} NPI {npi} SSN {ssn}"
    once = adapter.redact_text(raw).text
    twice = adapter.redact_text(once).text
    assert once == twice


def test_contains_phi_predicate() -> None:
    """``contains_phi`` is True on raw PHI and False on clean / masked text."""
    adapter = _adapter()
    assert adapter.contains_phi("member W123456789") is True
    assert adapter.contains_phi("the appeal was approved") is False
    masked = adapter.redact_text("member W123456789").text
    assert adapter.contains_phi(masked) is False

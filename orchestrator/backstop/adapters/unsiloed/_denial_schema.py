"""Canonical Denial field set shared by both Unsiloed adapters.

Both the real HTTP adapter and the deterministic sim adapter speak the *same*
``DenialExtraction`` shape — that shared field vocabulary is the contract the
contract tests assert. Centralising the field names (and the JSON Schema the
real adapter pins the vendor extraction to) in one module keeps the two
adapters from drifting apart and gives the schema exactly one owner.

This module is pure: constants and a stdlib JSON Schema builder, no I/O.
"""

from __future__ import annotations

from typing import Dict, Tuple

__all__ = [
    "PAYER_NAME",
    "PAYER_ID",
    "MEMBER_ID",
    "SUBSCRIBER_ID",
    "CLAIM_NUMBER",
    "BILLING_NPI",
    "RENDERING_NPI",
    "DATE_OF_SERVICE",
    "BILLED_AMOUNT",
    "ALLOWED_AMOUNT",
    "PAID_AMOUNT",
    "PATIENT_RESPONSIBILITY",
    "CARC_CODES",
    "RARC_CODES",
    "DENIAL_REASON",
    "SERVICE_LINES",
    "CANONICAL_FIELDS",
    "build_denial_schema",
]

# Canonical field names. Every emitted ``ExtractedField.name`` is one of these,
# so downstream stages (Moss/MiniMax/letter) read a stable vocabulary.
PAYER_NAME = "payer_name"
PAYER_ID = "payer_id"
MEMBER_ID = "member_id"
SUBSCRIBER_ID = "subscriber_id"
CLAIM_NUMBER = "claim_number"
BILLING_NPI = "billing_npi"
RENDERING_NPI = "rendering_npi"
DATE_OF_SERVICE = "date_of_service"
BILLED_AMOUNT = "billed_amount"
ALLOWED_AMOUNT = "allowed_amount"
PAID_AMOUNT = "paid_amount"
PATIENT_RESPONSIBILITY = "patient_responsibility"
CARC_CODES = "carc_codes"
RARC_CODES = "rarc_codes"
DENIAL_REASON = "denial_reason"
SERVICE_LINES = "service_lines"

# Ordered tuple of the canonical fields (stable, deterministic iteration order).
CANONICAL_FIELDS: Tuple[str, ...] = (
    PAYER_NAME,
    PAYER_ID,
    MEMBER_ID,
    SUBSCRIBER_ID,
    CLAIM_NUMBER,
    BILLING_NPI,
    RENDERING_NPI,
    DATE_OF_SERVICE,
    BILLED_AMOUNT,
    ALLOWED_AMOUNT,
    PAID_AMOUNT,
    PATIENT_RESPONSIBILITY,
    CARC_CODES,
    RARC_CODES,
    DENIAL_REASON,
    SERVICE_LINES,
)

# The subset that must be present (non-empty) in every well-formed extraction.
# Used by the sim adapter to guarantee a fully-populated result and by the tests
# as the required field set.
REQUIRED_FIELDS: Tuple[str, ...] = (
    PAYER_NAME,
    CLAIM_NUMBER,
    DATE_OF_SERVICE,
    BILLED_AMOUNT,
    CARC_CODES,
    DENIAL_REASON,
)

# JSON-Schema primitive type per canonical field, used to build ``schema_data``.
_STRING_FIELDS: Tuple[str, ...] = (
    PAYER_NAME,
    PAYER_ID,
    MEMBER_ID,
    SUBSCRIBER_ID,
    CLAIM_NUMBER,
    BILLING_NPI,
    RENDERING_NPI,
    DATE_OF_SERVICE,
    DENIAL_REASON,
)
_NUMBER_FIELDS: Tuple[str, ...] = (
    BILLED_AMOUNT,
    ALLOWED_AMOUNT,
    PAID_AMOUNT,
    PATIENT_RESPONSIBILITY,
)
_STRING_ARRAY_FIELDS: Tuple[str, ...] = (
    CARC_CODES,
    RARC_CODES,
    SERVICE_LINES,
)


def build_denial_schema() -> Dict[str, object]:
    """Return the JSON Schema that pins Unsiloed's extraction to Denial fields.

    The real adapter ``json.dumps`` this into the ``schema_data`` multipart
    field so the vendor returns exactly the canonical field vocabulary rather
    than a free-form layout.
    """
    properties: Dict[str, object] = {}
    for name in _STRING_FIELDS:
        properties[name] = {"type": "string"}
    for name in _NUMBER_FIELDS:
        properties[name] = {"type": "number"}
    for name in _STRING_ARRAY_FIELDS:
        properties[name] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "title": "BackstopDenial",
        "properties": properties,
        "required": list(REQUIRED_FIELDS),
    }

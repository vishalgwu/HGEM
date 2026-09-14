"""Observability.  ARCHITECTURE.md 0: "the audit log is the product."

`audit` (S5.5). Structured logging, tracing and the metric surfaces are S13.1.
"""

from guardmem_core.observability.audit import (
    GENESIS,
    ChainVerification,
    canonical_json,
    digest_for,
    next_link,
    verify_chain,
)

__all__ = [
    "GENESIS",
    "ChainVerification",
    "canonical_json",
    "digest_for",
    "next_link",
    "verify_chain",
]

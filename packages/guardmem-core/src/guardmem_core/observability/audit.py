"""The hash-chained audit log.  BUILD_NOTEBOOK.md S5.5

`RULES.md` invariant I5: `digest_n == sha256(payload_n || digest_{n-1})`.
`ARCHITECTURE.md` §0 is why it matters - "the audit log is the product. Every
other component is instrumented to feed it. If a decision isn't reconstructable,
it's a bug of the same severity as a wrong decision."

A hash chain does not stop tampering; it makes tampering *visible*. Editing one
payload breaks that link's own digest, and editing the digest to match breaks
the next link's `prev_digest`. Either way `verify_chain` names the first `seq`
that does not add up, which is what S5.5's DONE WHEN asks for. The remaining
attack is rewriting every link from the tamper point forward - which is why the
migration revokes UPDATE and DELETE on `audit_event` at the role level rather
than trusting this module.

**The chain logic is pure and the storage is separate.** `verify_chain` takes
links and returns a verdict; nothing here opens a connection. That is what lets
the whole of I5 be unit-tested without Docker, and it is the same seam
`outbox.py` draws - this module knows the table's shape and the statements, and
the caller knows connections and transactions.

**Canonical JSON is the load-bearing detail, and it has to survive Postgres.**
The digest is taken over the payload's canonical form, and `verify_chain` reads
payloads back out of a `JSONB` column to recompute it. `JSONB` does not preserve
key order, whitespace, or duplicate keys, so the canonical form has to be
something a round trip through it reproduces exactly: sorted keys, no
whitespace, ASCII-escaped. What `JSONB` does *not* guarantee is number
formatting, which is why `canonical_json` refuses anything but `int`, `float`,
`str`, `bool` and `None` inside - see its docstring.

**Genesis is thirty-two zero bytes.** The column is `BYTEA NOT NULL`, so the
first link needs something, and a fixed-width zero digest keeps every row the
same shape and makes "is this the head of a chain?" a value test rather than a
null test.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final

from pydantic import Field

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.receipt import AuditEvent

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from guardmem_core.types import TenantId, TraceId

__all__ = [
    "GENESIS",
    "ChainVerification",
    "canonical_json",
    "digest_for",
    "next_link",
    "verify_chain",
]

# The `prev_digest` of the first link in a tenant's chain. Thirty-two zero
# bytes, hex-encoded: the same width as a real sha256 digest, so every row has
# the same shape and `BYTEA NOT NULL` is satisfied without a sentinel type.
GENESIS: Final = "00" * 32

# What `canonical_json` will serialise. A `datetime` or a `UUID` nested in a
# payload is the failure this exists to catch: `json.dumps` refuses it, but a
# caller that reached for `default=str` would get a digest over a string that
# `JSONB` then hands back as the same string - verifying fine, while the audit
# record quietly disagreed with the object it was taken from.
_JSON_SCALARS: Final = (str, int, float, bool, type(None))


class ChainVerification(GMModel):
    """Whether a tenant's audit chain adds up, and where it stops if not.

    Attributes:
        verified: True when every link's digest and linkage check out.
        broken_at: The `seq` of the first link that does not - `None` when
            `verified`. S5.5's DONE WHEN: tampering "makes `verify_chain`
            report the exact break point", so this is a position rather than a
            count.
        checked: How many links were examined. An empty chain verifies
            vacuously, and without this a caller cannot tell that from a chain
            that was actually walked.
    """

    verified: bool
    broken_at: int | None = None
    checked: int = Field(ge=0)


def canonical_json(payload: dict[str, object]) -> str:
    """Serialise a payload to the one form its digest is taken over.

    Args:
        payload: The event body.

    Returns:
        JSON with sorted keys, no insignificant whitespace, and non-ASCII
        escaped.

    Raises:
        TypeError: the payload holds something that is not a JSON scalar, list
            or dict. Raised here rather than at the driver, because the message
            a caller needs is about the *digest*, not about serialisation.
        ValueError: a float is NaN or an infinity. Neither is valid JSON;
            `json.dumps` emits them as bare tokens that no other parser accepts,
            so a chain containing one could never be verified by anything but
            this process.

    Three choices, each of which a round trip through `JSONB` would otherwise
    undo:

    - `sort_keys=True`, because `JSONB` stores an object as a sorted map and
      hands it back in its own order, not the insertion order.
    - `separators=(",", ":")`, because whitespace is not preserved either.
    - `ensure_ascii=True`, so the output is pure ASCII and the digest does not
      depend on the encoding used to get bytes out of it.

    Number formatting is the one thing `JSONB` can change that this cannot fix -
    it stores numbers as `numeric` - so what goes in has to be a value that
    survives. Integers and IEEE doubles do; `Decimal` and `datetime` are refused
    above rather than coerced.
    """
    _reject_unserialisable(payload)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def digest_for(payload: dict[str, object], prev_digest: str) -> str:
    """`sha256(canonical_json(payload) || prev_digest)` as hex.  I5

    Args:
        payload: The event body.
        prev_digest: The previous link's digest, hex. `GENESIS` for the first.

    Returns:
        The digest, lowercase hex.

    Raises:
        ValueError: `prev_digest` is not 64 hex characters. A truncated or
            empty predecessor would still hash to *something*, and the chain
            would verify against its own mistake.

    The previous digest is appended as its raw bytes rather than as its hex
    text. Either is a valid reading of `||` and neither is more secure; the
    bytes are what `ARCHITECTURE.md` §5's `BYTEA` column holds, so hashing them
    keeps the stored value and the hashed value the same object.
    """
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8") + _digest_bytes(prev_digest)
    ).hexdigest()


def next_link(
    *,
    tenant_id: TenantId,
    trace_id: TraceId,
    kind: str,
    payload: dict[str, object],
    prev_digest: str,
    created_at: datetime,
) -> AuditEvent:
    """Build the link that follows `prev_digest`.

    Args:
        tenant_id: Whose chain this extends. Chains are per tenant, which is
            what makes `verify_chain(tenant_id)` a meaningful question.
        trace_id: The proposal that produced the event.
        kind: One of `AuditEvent`'s six; validated there rather than here.
        payload: The event body.
        prev_digest: The current head of the chain, or `GENESIS`.
        created_at: System time. Passed in rather than read from a clock, so
            the caller owns the one impure input and this stays replayable.

    Returns:
        The new link, with `seq` unset - Postgres assigns it from a `BIGSERIAL`.

    Raises:
        TypeError: the payload is not JSON-safe.
        ValueError: `prev_digest` is malformed, or `kind` is not one of the six.
    """
    return AuditEvent(
        tenant_id=tenant_id,
        trace_id=trace_id,
        kind=kind,  # type: ignore[arg-type]  # `AuditEvent` validates the Literal.
        payload=payload,
        prev_digest=prev_digest,
        digest=digest_for(payload, prev_digest),
        created_at=created_at,
    )


def verify_chain(events: Sequence[AuditEvent]) -> ChainVerification:
    """Walk a tenant's chain and report the first link that does not add up.

    Args:
        events: The tenant's links **in `seq` order**, oldest first. Ordering is
            the caller's job because it is the database's `ORDER BY`; a
            shuffled sequence reports a break at the first out-of-place link,
            which is the right answer to the wrong question.

    Returns:
        A `ChainVerification`. `broken_at` is the `seq` of the first bad link,
        or its index when `seq` is unset - an in-memory chain that has not been
        inserted still has positions.

    Two checks per link, and both are needed. The digest check catches an edited
    payload; the linkage check catches an edited payload whose digest was
    recomputed to match, because the *next* link's `prev_digest` still names the
    old one. Together they mean the only undetectable tamper is a rewrite of
    every link from the break to the head - which is what the migration's
    revoked UPDATE and DELETE grants are for.

    An empty chain verifies with `checked = 0`. That is the honest answer: a
    tenant that has never been written to has nothing to disagree with.
    """
    expected_prev = GENESIS
    for index, event in enumerate(events):
        position = event.seq if event.seq is not None else index
        if event.prev_digest != expected_prev:
            return ChainVerification(verified=False, broken_at=position, checked=index)
        if event.digest != digest_for(event.payload, event.prev_digest):
            return ChainVerification(verified=False, broken_at=position, checked=index)
        expected_prev = event.digest
    return ChainVerification(verified=True, broken_at=None, checked=len(events))


def _digest_bytes(digest: str) -> bytes:
    """Decode a hex digest, refusing anything that is not one.

    Raises:
        ValueError: not 64 lowercase hex characters.
    """
    if len(digest) != 64:
        raise ValueError(
            f"digest must be 64 hex characters, got {len(digest)}; a truncated "
            "predecessor still hashes, so the chain would verify against its "
            "own mistake"
        )
    try:
        return bytes.fromhex(digest)
    except ValueError as exc:
        raise ValueError(f"digest is not hexadecimal: {digest!r}") from exc


def _reject_unserialisable(value: object, path: str = "payload") -> None:
    """Refuse anything `JSONB` would not hand back unchanged.

    Args:
        value: The subtree to check.
        path: Where it sits, for the message.

    Raises:
        TypeError: at the first value that is not a JSON scalar, list or dict,
            naming its path. `json.dumps` would raise too, but its message says
            only the type - and with thirty fields in a `DecisionRecord`
            payload, the path is the half a caller needs.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"{path}: JSON object keys must be strings, got {type(key).__name__}; "
                    "JSONB would stringify it and the digest would stop matching"
                )
            _reject_unserialisable(item, f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_unserialisable(item, f"{path}[{index}]")
        return
    if not isinstance(value, _JSON_SCALARS):
        raise TypeError(
            f"{path}: {type(value).__name__} is not JSON-safe. The digest is "
            "taken over canonical JSON and verified against what JSONB hands "
            "back, so a value that does not round-trip breaks every later link."
        )

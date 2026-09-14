"""The hash-chained audit log.  BUILD_NOTEBOOK.md S5.5

Invariant I5 is `digest_n == sha256(payload_n || digest_{n-1})`, and the half of
S5.5's DONE WHEN that needs a database - "tampering with one payload row makes
`verify_chain` report the exact break point" - is
`tests/integration/test_audit_chain.py`. Everything here is the arithmetic,
which is deliberately separable: a chain that only verifies against Postgres is
a chain nobody can reason about.

The tamper cases are the point of the file. A chain that verifies its own
happy path proves nothing; what has to be shown is that each *specific* way of
editing history is caught, and named at the right position.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Final

import pytest

from guardmem_core.observability import (
    GENESIS,
    canonical_json,
    digest_for,
    next_link,
    verify_chain,
)
from guardmem_core.schemas.receipt import AuditEvent
from guardmem_core.types import TenantId, TraceId

TENANT: Final = TenantId("11111111-1111-1111-1111-111111111111")
TRACE: Final = TraceId("tr_audit")
WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)


def chain(*payloads: dict[str, object]) -> list[AuditEvent]:
    """A well-formed chain over these payloads, `seq` numbered from 1."""
    links: list[AuditEvent] = []
    previous = GENESIS
    for index, payload in enumerate(payloads, start=1):
        link = next_link(
            tenant_id=TENANT,
            trace_id=TRACE,
            kind="DECISION",
            payload=payload,
            prev_digest=previous,
            created_at=WHEN,
        )
        links.append(link.model_copy(update={"seq": index}))
        previous = link.digest
    return links


def three() -> list[AuditEvent]:
    """Three links, which is the shortest chain with a middle to tamper with."""
    return chain({"n": 1}, {"n": 2}, {"n": 3})


class TestTheDigestIsInvariantI5:
    def test_it_is_sha256_of_canonical_json_and_the_previous_digest(self) -> None:
        """I5 stated directly, computed by hand rather than by the module.

        A test that called `digest_for` on both sides would agree with any
        implementation, including a wrong one.
        """
        payload: dict[str, object] = {"b": 2, "a": 1}
        expected = hashlib.sha256(b'{"a":1,"b":2}' + bytes.fromhex(GENESIS)).hexdigest()

        assert digest_for(payload, GENESIS) == expected

    def test_the_previous_digest_enters_as_bytes_not_as_text(self) -> None:
        """Either reading of `||` is defensible; this pins which one shipped,
        because the stored column is `BYTEA` and the two produce different
        chains."""
        payload: dict[str, object] = {"a": 1}
        as_text = hashlib.sha256(b'{"a":1}' + GENESIS.encode()).hexdigest()

        assert digest_for(payload, GENESIS) != as_text

    def test_a_different_payload_gives_a_different_digest(self) -> None:
        assert digest_for({"a": 1}, GENESIS) != digest_for({"a": 2}, GENESIS)

    def test_the_same_payload_after_a_different_predecessor_differs(self) -> None:
        """What makes it a *chain* rather than a list of hashes: a link's digest
        depends on everything before it."""
        first = digest_for({"a": 1}, GENESIS)

        assert digest_for({"a": 1}, first) != first

    def test_a_malformed_predecessor_is_refused(self) -> None:
        """A truncated digest still hashes to something, and the chain would
        then verify against its own mistake."""
        with pytest.raises(ValueError, match="64 hex characters"):
            digest_for({"a": 1}, "abc")

    def test_a_non_hex_predecessor_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not hexadecimal"):
            digest_for({"a": 1}, "z" * 64)


class TestCanonicalJson:
    """The form the digest is taken over, and every way `JSONB` could undo it."""

    def test_key_order_does_not_change_the_output(self) -> None:
        """`JSONB` stores an object as a sorted map and hands it back in its own
        order, so a digest over insertion order would stop matching the moment
        it made the round trip."""
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_nested_keys_are_sorted_too(self) -> None:
        assert canonical_json({"x": {"b": 1, "a": 2}}) == '{"x":{"a":2,"b":1}}'

    def test_there_is_no_insignificant_whitespace(self) -> None:
        assert canonical_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'

    def test_non_ascii_is_escaped(self) -> None:
        """So the digest does not depend on which encoding got bytes out of the
        string."""
        rendered = canonical_json({"name": "Alvaréz"})

        assert rendered.isascii()
        assert json.loads(rendered) == {"name": "Alvaréz"}

    def test_it_round_trips(self) -> None:
        payload: dict[str, object] = {"a": 1, "b": [1, {"c": True}], "d": None, "e": 1.5}

        assert json.loads(canonical_json(payload)) == payload

    def test_a_datetime_is_refused_rather_than_stringified(self) -> None:
        """The failure this exists to catch.

        A caller reaching for `default=str` would get a digest over the string
        form, which `JSONB` then returns unchanged - verifying fine, while the
        audit record quietly disagreed with the object it was taken from.
        """
        with pytest.raises(TypeError, match="not JSON-safe"):
            canonical_json({"at": WHEN})

    def test_the_error_names_the_path_not_just_the_type(self) -> None:
        """A `DecisionRecord` payload has thirty-odd fields; the type alone
        does not say which one."""
        with pytest.raises(TypeError, match=r"payload\.risk\.features"):
            canonical_json({"risk": {"features": WHEN}})

    def test_it_names_a_path_inside_a_list(self) -> None:
        with pytest.raises(TypeError, match=r"payload\.codes\[1\]"):
            canonical_json({"codes": ["ok", WHEN]})

    def test_a_non_string_key_is_refused(self) -> None:
        """`JSONB` would stringify it and the digest would stop matching."""
        with pytest.raises(TypeError, match="keys must be strings"):
            canonical_json({1: "a"})  # type: ignore[dict-item]

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_nan_and_infinity_are_refused(self, value: float) -> None:
        """`json.dumps` emits these as bare tokens no other parser accepts, so
        a chain containing one could never be verified by anything else."""
        with pytest.raises(ValueError, match="Out of range"):
            canonical_json({"x": value})


class TestVerifyAWellFormedChain:
    def test_a_chain_verifies(self) -> None:
        result = verify_chain(three())

        assert result.verified
        assert result.broken_at is None
        assert result.checked == 3

    def test_an_empty_chain_verifies_vacuously(self) -> None:
        """The honest answer for a tenant that has never been written to - and
        `checked` is what tells a caller that is what happened."""
        result = verify_chain([])

        assert result.verified
        assert result.checked == 0

    def test_the_first_link_follows_genesis(self) -> None:
        assert three()[0].prev_digest == GENESIS

    def test_each_link_names_its_predecessor(self) -> None:
        links = three()

        assert [link.prev_digest for link in links[1:]] == [link.digest for link in links[:-1]]


class TestTamperingIsCaught:
    """Each specific way of editing history, and where it surfaces."""

    def test_an_edited_payload_breaks_its_own_link(self) -> None:
        """The plain case: the digest no longer matches what it covers."""
        links = three()
        links[1] = links[1].model_copy(update={"payload": {"n": 99}})

        result = verify_chain(links)

        assert not result.verified
        assert result.broken_at == 2

    def test_an_edited_payload_with_a_recomputed_digest_breaks_the_next_link(self) -> None:
        """The interesting case, and the reason `verify_chain` runs two checks.

        A tamperer who knows the scheme will recompute the digest to cover the
        new payload - so that link is now self-consistent. What they cannot fix
        without rewriting the rest is the *next* link's `prev_digest`, which
        still names the digest the old payload had.
        """
        links = three()
        forged: dict[str, object] = {"n": 99}
        links[1] = links[1].model_copy(
            update={"payload": forged, "digest": digest_for(forged, links[1].prev_digest)}
        )

        result = verify_chain(links)

        assert not result.verified
        assert result.broken_at == 3, "the break surfaces at the link that points back"

    def test_a_deleted_link_breaks_the_chain(self) -> None:
        """Append-only means no DELETE, and the migration revokes it - but a
        chain that could not *detect* a deletion would make that grant the only
        defence."""
        links = three()
        del links[1]

        result = verify_chain(links)

        assert not result.verified
        assert result.broken_at == 3

    def test_a_reordered_pair_breaks_the_chain(self) -> None:
        links = three()
        links[0], links[1] = links[1], links[0]

        assert not verify_chain(links).verified

    def test_an_inserted_link_breaks_the_chain(self) -> None:
        """A forged link spliced into the middle, correctly chained to its own
        predecessor - the successor still names the original digest."""
        links = three()
        spliced = next_link(
            tenant_id=TENANT,
            trace_id=TRACE,
            kind="DECISION",
            payload={"n": 1.5},
            prev_digest=links[0].digest,
            created_at=WHEN,
        )
        links.insert(1, spliced.model_copy(update={"seq": 99}))

        result = verify_chain(links)

        assert not result.verified
        assert result.broken_at == 2, "the original second link no longer follows"

    def test_a_truncated_chain_still_verifies(self) -> None:
        """Worth knowing, and not a defect this structure can fix.

        Dropping links from the *head* leaves a shorter, internally consistent
        chain. Only an external witness - a published head, a countersignature -
        detects that, and neither is specified. `checked` is what a caller has
        to compare against its own expectation.
        """
        result = verify_chain(three()[:2])

        assert result.verified
        assert result.checked == 2

    def test_the_break_is_reported_by_seq_not_by_position(self) -> None:
        """`broken_at` has to be findable in the table. Postgres assigns `seq`
        from a global `BIGSERIAL`, so a tenant's links are not 1, 2, 3."""
        numbered = chain({"n": 1}, {"n": 2})
        links = [
            numbered[0].model_copy(update={"seq": 400}),
            numbered[1].model_copy(update={"seq": 517, "payload": {"n": 9}}),
        ]

        assert verify_chain(links).broken_at == 517

    def test_an_unsequenced_chain_reports_a_position(self) -> None:
        """An in-memory chain that has not been inserted still has an order."""
        links = chain({"n": 1}, {"n": 2})
        unsequenced = [link.model_copy(update={"seq": None}) for link in links]
        unsequenced[1] = unsequenced[1].model_copy(update={"payload": {"n": 9}})

        assert verify_chain(unsequenced).broken_at == 1


class TestNextLink:
    def test_it_leaves_seq_for_postgres(self) -> None:
        """`BIGSERIAL` assigns it, so the model cannot know it at construction."""
        link = next_link(
            tenant_id=TENANT,
            trace_id=TRACE,
            kind="DECISION",
            payload={"a": 1},
            prev_digest=GENESIS,
            created_at=WHEN,
        )

        assert link.seq is None
        assert link.digest == digest_for({"a": 1}, GENESIS)

    def test_an_unknown_kind_is_refused(self) -> None:
        """`AuditEvent` declares six; a seventh would be a row the `CHECK`
        constraint rejects at insert, one round trip later."""
        with pytest.raises(ValueError, match="kind"):
            next_link(
                tenant_id=TENANT,
                trace_id=TRACE,
                kind="MADE_UP",
                payload={"a": 1},
                prev_digest=GENESIS,
                created_at=WHEN,
            )

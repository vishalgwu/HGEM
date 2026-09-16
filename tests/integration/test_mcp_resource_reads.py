"""S6.4's resources, over the protocol, against a tenant that has facts.

`tests/unit/test_mcp_resources.py` drives the same readers against a fake store,
which proves the rendering. What it cannot prove is the half that only exists
here: that the URI survives the wire, that `context_for` resolves the configured
tenant, and that the RLS-scoped read returns *this* tenant's rows rather than
none - which is the failure mode that looks exactly like an empty namespace.

Split from `test_mcp_memory_tools.py` when this class pushed it past `RULES.md`
§2.4's 400-line cap. The seam is real rather than arithmetic: the tools and the
resources are different subjects asked of one session, and the session is now
`fixtures/mcp_session.py`, shared rather than copied.

**Not named `test_mcp_resources.py`.** `tests/` has no `__init__.py`, so pytest
imports modules by bare basename and two files with one name abort collection for
the whole run - and the unit module already has that name.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.shared.exceptions import MCPError

from fixtures.mcp_session import SEEDED_PREDICATE, TIMEOUT_S, connected, text_block
from guardmem_core.settings import get_settings


@pytest.mark.usefixtures("demo_server_env")
class TestTheResourceSurfaceAgainstSeededRows:
    """S6.4's three resources, over the protocol, against a tenant with facts.

    The unit suite drives these readers against a fake store, which proves the
    rendering. What it cannot prove is the half that only exists here: that the
    URI survives the wire, that `context_for` resolves the configured tenant,
    and that the RLS-scoped read returns this tenant's rows rather than none -
    which is the failure mode that looks exactly like an empty namespace.
    """

    async def test_the_namespace_snapshot_shows_the_believed_state(
        self, seeded_namespace: str
    ) -> None:
        """S6.4's DONE WHEN: "attaching the namespace resource shows the current
        believed state."

        Asserted on a *seeded* predicate rather than on a count, because the seed
        may grow: what must hold is that a fact the tenant believes appears with
        the two things that make it governed - a confidence and the span it came
        from.
        """
        async with connected() as session:
            result = await asyncio.wait_for(
                session.read_resource(f"guardmem://memory/{seeded_namespace}"), timeout=TIMEOUT_S
            )

        block = text_block(result)
        assert block.mime_type == "text/markdown"
        assert f"# Believed memory - {seeded_namespace}" in block.text
        assert f"## {SEEDED_PREDICATE}" in block.text
        assert "confidence" in block.text
        assert "source (" in block.text

    async def test_the_snapshot_is_scoped_to_the_configured_tenant(
        self, seeded_namespace: str
    ) -> None:
        """An RLS-scoped read on the wrong tenant returns zero rows rather than
        raising, so "it answered" is not evidence that it answered correctly.

        The seeded subject's name appearing is what distinguishes a real read
        from a well-formed empty one.
        """
        async with connected() as session:
            result = await asyncio.wait_for(
                session.read_resource(f"guardmem://memory/{seeded_namespace}"), timeout=TIMEOUT_S
            )

        assert "believes nothing about this namespace yet" not in text_block(result).text

    async def test_the_ontology_resource_serves_the_installed_pack(
        self, seeded_namespace: str
    ) -> None:
        """The cheapest end-to-end proof that `resources/read` is wired: URI
        parsing, tenant resolution and the reader, with no rows involved."""
        async with connected() as session:
            result = await asyncio.wait_for(
                session.read_resource(f"guardmem://ontology/{seeded_namespace}"), timeout=TIMEOUT_S
            )

        block = text_block(result)
        assert block.mime_type == "text/yaml"
        assert "pack: clinical" in block.text
        assert f"namespace {seeded_namespace}" in block.text

    async def test_an_audit_resource_for_an_unknown_trace_fails(
        self, seeded_namespace: str
    ) -> None:
        """ "Was this decision recorded?" and "did I paste the right id?" have
        opposite answers, and an empty array answers neither.

        The seeded tenant writes through the store rather than the applier, so
        its chain is empty - which makes this the honest shape of the test here:
        what is pinned is the refusal, not the reading. `test_audit_chain.py`
        owns a chain with links in it.
        """
        async with connected() as session:
            with pytest.raises(MCPError, match="no audit events"):
                await asyncio.wait_for(
                    session.read_resource("guardmem://audit/tr_not_a_real_trace"),
                    timeout=TIMEOUT_S,
                )

    async def test_the_concrete_listing_is_empty_without_a_default_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_server_env` sets a tenant and no default namespace, so a URI built
        from configuration would carry an empty segment - which reads as a real
        attachment in a picker and fails on click. Templates cover the rest."""
        monkeypatch.setenv("GM_MCP_DEFAULT_NAMESPACE", "")
        get_settings.cache_clear()

        async with connected() as session:
            listed = await asyncio.wait_for(session.list_resources(), timeout=TIMEOUT_S)
            templates = await asyncio.wait_for(session.list_resource_templates(), timeout=TIMEOUT_S)

        assert listed.resources == []
        assert len(templates.resource_templates) == 3

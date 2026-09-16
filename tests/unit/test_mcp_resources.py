"""The resource surface.  MCP_INTEGRATION.md §3, BUILD_NOTEBOOK.md S6.4

Three things are worth pinning here, and only the first is obvious.

- **A template that lists must have a reader.** A resource advertised and not
  wired fails when a person clicks it in a picker, which is the worst possible
  moment to discover it and the hardest to attribute.
- **A bad URI must fail, not return an empty body.** `resources/__init__.py`
  argues it at length: a person attaching a resource has no model in the loop to
  interpret a refusal, so "could not load" and "nothing is known" must not
  render the same way.
- **The snapshot must say whether it is complete.** A truncated panel that reads
  like the whole believed state is the failure this resource exists to avoid,
  and it is invisible unless something counts.

`visible=True` on every assertion below, for the reason `store_with` gives: the
relay is what sets that flag and no relay runs here, so a store of invisible
rows would make every read return nothing and every test pass for the wrong
reason.
"""

from __future__ import annotations

import pytest

from fixtures.assertions import NS, TENANT, WHEN, stored_assertion
from fixtures.mcp import context, settings, state, store_with
from mcp_server.resources import (
    _READERS,
    RESOURCE_TEMPLATES,
    _split,
    list_resources,
    read_resource,
)
from mcp_server.resources.memory import read_memory
from mcp_server.resources.ontology import read_ontology
from mcp_server.resources.uris import uri_for
from mcp_server.tools.context import ToolRefusedError, context_for

# A world time after `WHEN`, so an assertion given this `valid_to` is retired
# rather than live. Named rather than inlined because "the year after" is the
# only property that matters and a literal date would obscure it.
WHEN_LATER = WHEN.replace(year=WHEN.year + 1)


def _kind_of(uri_template: str) -> str:
    """The authority segment, which is what `_READERS` is keyed on."""
    return uri_template.split("://", 1)[1].split("/", 1)[0]


class TestTheAdvertisedSurface:
    def test_every_template_has_a_reader(self) -> None:
        """A template listed with nothing behind it fails on click.

        Keyed off the published tuple rather than a second hand-written list, so
        adding a template without wiring it fails here rather than in a client.
        """
        assert {_kind_of(t.uri_template) for t in RESOURCE_TEMPLATES} == set(_READERS)

    def test_every_template_declares_a_mime_type(self) -> None:
        """§3's table gives one per row and a client renders by it.

        A resource with no MIME type is shown as an opaque blob, which for the
        Markdown snapshot means a person reads the asterisks.
        """
        assert all(template.mime_type for template in RESOURCE_TEMPLATES)

    def test_the_concrete_list_follows_the_configured_namespace(self) -> None:
        """`resources/list` answers "what can I attach right now"."""
        listed = [str(resource.uri) for resource in list_resources(state())]

        assert listed == [f"guardmem://memory/{NS}", f"guardmem://ontology/{NS}"]

    def test_nothing_is_listed_without_a_configured_namespace(self) -> None:
        """A URI with an empty segment reads as a real attachment and is not.

        `context_for` refuses that case with a message naming the variable, but
        a picker is not a place a message can be read - so the entry must not be
        offered at all.
        """
        blank = settings(mcp_tenant_id=str(TENANT), mcp_default_namespace="")

        assert list_resources(state(settings=blank)) == []

    def test_the_audit_resource_is_a_template_and_never_a_listing(self) -> None:
        """A trace id is minted per proposal, so there is no "the" trace."""
        listed = {str(resource.uri) for resource in list_resources(state())}

        assert not any(uri.startswith("guardmem://audit/") for uri in listed)
        assert "audit" in {_kind_of(t.uri_template) for t in RESOURCE_TEMPLATES}


class TestBadUrisFailRatherThanAnswer:
    async def test_an_unknown_kind_is_refused(self) -> None:
        """§3 publishes seven and S6.4 serves three; the other four must say so
        rather than return nothing."""
        with pytest.raises(ToolRefusedError, match="no resource at"):
            await read_resource(state(), "guardmem://queue/pending")

    async def test_a_foreign_scheme_is_refused(self) -> None:
        """A client that sent another server's URI is told which scheme."""
        with pytest.raises(ToolRefusedError, match="guardmem:// scheme"):
            await read_resource(state(), "file:///etc/passwd")

    async def test_a_kind_with_no_argument_is_refused(self) -> None:
        with pytest.raises(ToolRefusedError, match="no argument"):
            await read_resource(state(), "guardmem://memory/")

    def test_audit_needs_a_tenant_but_not_a_namespace(self) -> None:
        """A trace is keyed by tenant and trace alone, so demanding a namespace
        refuses a read for want of something it never uses.

        That was a real bug: with a tenant configured and no
        `GM_MCP_DEFAULT_NAMESPACE`, `guardmem://audit/...` failed with "no
        namespace was given" before it ever looked for the trace.

        Asserted against `context_for` rather than through `read_resource`,
        because the reader's next move is to open a pool and the unit fixture
        has none - the seam is what changed, and the seam is what this pins.
        `test_mcp_resource_reads.py` drives the whole path against Postgres.
        """
        no_default = state(settings=settings(mcp_tenant_id=str(TENANT), mcp_default_namespace=""))

        context = context_for(no_default, {}, require_namespace=False)

        assert context.tenant_id == TENANT

    def test_a_namespace_scoped_read_still_requires_one(self) -> None:
        """The relaxation must not leak to `memory` or `ontology`: an empty
        namespace selects nothing, which reads as an empty namespace rather than
        as a misconfiguration."""
        no_default = state(settings=settings(mcp_tenant_id=str(TENANT), mcp_default_namespace=""))

        with pytest.raises(ToolRefusedError, match="no namespace was given"):
            context_for(no_default, {})

    async def test_an_unconfigured_tenant_refuses_by_variable_name(self) -> None:
        """A resource read is a read of governed memory and gets `context_for`'s
        refusal, not a quieter one - a server with no tenant must not answer
        about somebody else's."""
        untenanted = state(settings=settings(mcp_default_namespace=str(NS)))

        with pytest.raises(ToolRefusedError, match="GM_MCP_TENANT_ID"):
            await read_resource(untenanted, f"guardmem://memory/{NS}")


class TestTheNamespaceSnapshot:
    """S6.4's DONE WHEN: attaching it "shows the current believed state"."""

    async def test_a_believed_fact_appears_with_its_span_and_confidence(self) -> None:
        """Those two fields are what make the panel governed memory rather than
        a notes file. A reader who cannot see where a fact came from has no way
        to challenge it."""
        store = await store_with(
            stored_assertion(predicate="allergy", obj="penicillin", visible=True)
        )

        text = (await read_memory(context(store), str(NS))).text

        assert "## allergy" in text
        assert "penicillin" in text
        assert "confidence" in text
        assert "source (" in text

    async def test_an_empty_namespace_says_so_in_words(self) -> None:
        """A blank panel is read as a broken attachment, and then the next one
        is not trusted either."""
        text = (await read_memory(context(), str(NS))).text

        assert "believes nothing about this namespace yet" in text

    async def test_a_complete_snapshot_says_it_is_complete(self) -> None:
        """The reader's whole question is "is this all of it?", and a snapshot
        that cannot answer it will be read as complete either way."""
        store = await store_with(stored_assertion(predicate="allergy", obj="latex", visible=True))

        assert "the whole believed state" in (await read_memory(context(store), str(NS))).text

    async def test_facts_are_grouped_by_predicate_once_each(self) -> None:
        """A namespace is read as "what do we know about X", and a predicate is
        the question each answer belongs to."""
        store = await store_with(
            stored_assertion(predicate="allergy", obj="latex", visible=True),
            stored_assertion(predicate="allergy", obj="penicillin", visible=True),
            stored_assertion(predicate="blood_type", obj="O negative", visible=True),
        )

        text = (await read_memory(context(store), str(NS))).text

        assert text.count("## allergy") == 1
        assert text.count("## blood_type") == 1
        assert text.index("## allergy") < text.index("## blood_type")

    async def test_a_retired_fact_is_shown_and_not_counted_as_believed(self) -> None:
        """§2.1's reasoning for `excluded` applies to a human panel at least as
        strongly: a snapshot that showed only live facts would answer a question
        about a missing one with silence."""
        store = await store_with(
            stored_assertion(
                predicate="home_address", obj="old", visible=True, valid_to=WHEN_LATER
            ),
            stored_assertion(predicate="home_address", obj="new", visible=True),
        )

        text = (await read_memory(context(store), str(NS))).text

        assert "## Retired" in text
        assert "1 live assertion(s)" in text

    async def test_the_declared_mime_type_is_markdown(self) -> None:
        """§3's table says `text/markdown`, and the body is written for a person
        rather than a parser."""
        assert (await read_memory(context(), str(NS))).mime_type == "text/markdown"

    async def test_the_echoed_uri_is_the_one_that_was_asked_for(self) -> None:
        """A client correlates the response to the attachment by URI; a
        normalised one silently breaks a namespace with a colon in it."""
        contents = await read_memory(context(), str(NS))

        assert str(contents.uri) == f"guardmem://memory/{NS}"


class TestTheOntologyResource:
    async def test_it_serves_the_bytes_the_extraction_prompt_sees(self) -> None:
        """The point of the resource: an agent formatting against this is
        formatting against the description the gate enforces. A second rendering
        would be free to drift, and the drift would surface as quarantines
        nobody could explain."""
        ctx = context()

        text = (await read_ontology(ctx, str(NS))).text

        assert ctx.state.ontology.as_prompt_yaml() in text

    async def test_it_names_the_pack_it_actually_served(self) -> None:
        """The URI is keyed by namespace and this server has one pack. A reader
        who expected `legal` must see `clinical` at the top rather than infer it
        from the predicates."""
        assert "pack: clinical" in (await read_ontology(context(), str(NS))).text

    async def test_the_declared_mime_type_is_yaml(self) -> None:
        assert (await read_ontology(context(), str(NS))).mime_type == "text/yaml"


class TestTheUriRoundTrip:
    """`Namespace` is an unconstrained `NewType(str)`.

    Nothing rejects a slash or a space in one, and both break a naive parse: a
    slash splits into two segments so `guardmem://memory/a/b` would silently
    answer about `a`, and a space makes the URI invalid outright. `uri_for`
    encodes and `_split` decodes, and this is what keeps the pair honest.
    """

    @pytest.mark.parametrize(
        "namespace",
        ["patient:7781", "org:acme", "quarantine:t-1", "a/b", "with space", "naïve:1"],
    )
    def test_any_namespace_survives_being_put_in_a_uri(self, namespace: str) -> None:
        assert _split(uri_for("memory", namespace)) == ("memory", namespace)

    def test_a_colon_is_left_readable(self) -> None:
        """Every documented namespace carries one, and `patient%3A7781` in a
        client's picker would be correct and unreadable."""
        assert uri_for("memory", "patient:7781") == "guardmem://memory/patient:7781"

    def test_a_slash_is_encoded_rather_than_splitting_the_path(self) -> None:
        """The one that would answer about the wrong namespace rather than
        failing, which is the failure mode worth a test of its own."""
        assert uri_for("memory", "a/b") == "guardmem://memory/a%2Fb"

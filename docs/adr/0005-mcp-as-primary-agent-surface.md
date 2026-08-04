# ADR-0005: MCP as the primary agent surface

**Status:** Accepted
**Date:** 2026-01-15

## Context

Every agent framework has a slightly different memory API. Building bespoke
adapters for LangGraph, CrewAI, LlamaIndex, Cursor, Claude Desktop, and the
next three frameworks is a treadmill.

## Decision

The primary interface for agent runtimes is **MCP** — Model Context Protocol.
`services/mcp_server` exposes `memory.search`, `memory.propose`,
`memory.commit`, `memory.forget`, `audit.*`, and `review.*` as MCP tools,
plus policy + provenance as MCP resources.

Framework-specific middleware (`packages/guardmem-sdk-python/middleware/*`)
is thin — it maps that framework's memory hook onto the MCP tool calls.

## Consequences

- Any MCP-capable client (Claude Desktop, Cursor, any framework that speaks
  MCP) works out of the box.
- The REST gateway is not the agent surface — it's the *dashboard's* API and
  the escape hatch for non-MCP clients.
- MCP contract tests are load-bearing: a break there breaks every agent
  runtime at once.

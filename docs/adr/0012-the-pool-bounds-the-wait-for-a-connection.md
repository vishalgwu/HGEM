# ADR-0012: The pool bounds the wait for a connection, and every timeout is chosen rather than inherited

**Status:** Accepted
**Date:** 2026-09-16
**Amends:** nothing. `RULES.md` §2.2's "every outbound call has an explicit
timeout. No timeout = CI failure (`GM002`)" is implemented rather than altered.

(ADR-0011 is reserved for `memory.commit`'s confidence — ADR-0010 and
`BUILD_NOTEBOOK.md` S6.2 both point at that number. The gap here is a
reservation, not a lost file.)

## Context

`RULES.md` §2.2 is unambiguous, and the codebase reads as though it complies.
`PgVectorStore`, `OutboxRelay` and `NamespaceEntityResolver` each take a
required, undefaulted `timeout_s`; every `fetch`, `fetchrow` and `execute`
passes `timeout=`; `AnthropicClient.__init__` spells out why the value is
required rather than defaulted — "a default is how a call ends up with one
nobody chose."

**Every statement is covered and the wait to run one is not.** Four facts.

**1. `Pool.acquire()` with no timeout waits forever.** Read off the installed
asyncpg 0.31.0, not recalled:

```python
async def _acquire(self, timeout):
    async def _acquire_impl():
        ch = await self._queue.get()          # <- unbounded when timeout is None
        ...
    if timeout is None:
        return await _acquire_impl()
    else:
        return await compat.wait_for(_acquire_impl(), timeout=timeout)
```

`self._queue` holds `max_size` connection holders. Once all ten are checked out,
the eleventh caller awaits `queue.get()` with nothing bounding it. The server
does not slow down and it does not fail — it stops, and it keeps its transport
open while it does, so a client sees a request that never returns rather than an
error it can retry. For a system whose stated posture is "fail closed, not
quiet" (`README.md`), an unbounded wait is the one failure mode that is neither.

**2. The two helpers disagree, and the one that disagrees is the one with no
tenant.** `tenant_transaction` already requires `timeout_s` and spends it on the
`set_config` round trip. `transaction` takes no timeout at all — and
`tenant_transaction` is implemented *on top of* `transaction`, so the bounded
helper delegates its connection acquisition to the unbounded one. Every store
and every relay pass therefore acquires without a bound today.

**3. The timeout that `create_pool` does take is not the one that was missing.**
`asyncpg.create_pool` has no `timeout` parameter of its own; `Pool.__init__`
accepts `min_size`, `max_size`, `max_queries`,
`max_inactive_connection_lifetime`, `connect`, `setup`, `init`, `reset` and
`**connect_kwargs`. A `timeout=` therefore lands in `connect_kwargs` and becomes
`connect()`'s **connection-establishment** timeout, whose default is 60 seconds.
That is a third, distinct clock, and conflating it with the acquire timeout is
the easy way to write this ADR wrong:

| Clock | Knob | Default | Covered before this ADR |
|---|---|---|---|
| Establish a connection (TCP, TLS, auth) | `connect(timeout=)` via `create_pool` | **60 s** | bounded, but by asyncpg rather than by us |
| Wait for a free connection | `Pool.acquire(timeout=)` | **none — forever** | **not at all** |
| Run one statement | `execute/fetch(timeout=)`, or `command_timeout` | none / none | yes, explicitly, at every call site |

**4. The translation is already right.** `builtins.TimeoutError` is a subclass of
`OSError`, and `transaction` already catches `(OSError,
asyncpg.PostgresConnectionError)` and re-raises `StoreUnavailable`. So passing a
timeout needs no new exception handling to reach the existing retryable error —
only a better message, because a bare `TimeoutError` stringifies to nothing and
"postgres connection failed: " with an empty tail is the wrong page at 3am.

## Decision

**`transaction` requires a `timeout_s` and spends it bounding
`pool.acquire()`. `create_pool` states the connection timeout it has been
relying on rather than inheriting it. Neither gets a default.**

```python
async def create_pool(dsn, *, min_size=1, max_size=10, connect_timeout_s=CONNECT_TIMEOUT_S) -> asyncpg.Pool
@asynccontextmanager
async def transaction(pool, *, timeout_s: float) -> AsyncIterator[Conn]
```

**Pool exhaustion is reported as itself, not as a connection failure.**
`TimeoutError` is caught ahead of the `OSError` clause that would otherwise
swallow it, and raises `StoreUnavailable` naming the wait, the pool's ceiling
and the two things an operator can actually do — raise `max_size`, or find what
is holding connections. "The database is unreachable" and "you are over
capacity" are different incidents with different fixes, and a message that
cannot tell them apart sends the first responder to the wrong system.

**The value is the caller's existing `store_timeout_s`**, not a new setting.
Every call site already holds one and every one of them means the same thing by
it: the ceiling on a single interaction with the store.

**`timeout_s` stays per-operation, not per-transaction, and that is a deliberate
limit.** `tenant_transaction` could already spend `timeout_s` twice — once on
`set_config`, once on the caller's statement — and bounding the acquire makes a
worst case of three. That is the existing convention applied consistently rather
than a regression, and it is honest about what these helpers promise: no single
wait exceeds the ceiling. A true end-to-end request budget is a different
mechanism (one deadline, threaded through and decremented) and belongs with the
gateway at S8.2, where a request first has a deadline to thread.

**The connect timeout keeps asyncpg's 60 seconds, written down.** Sixty seconds
is long for a same-VPC Postgres and there is no measurement in this repository
that would justify replacing it with a better number, so this ADR does not
invent one. What it does change is that the number is now chosen, named and in
one place — `RULES.md` §2.2's objection is to a call with no explicit timeout,
and a value silently inherited from a library default is exactly the timeout
nobody chose. Tuning it is a later step's job, with data.

### What this does not decide

- **`command_timeout` on the pool.** Deliberately still unset — see below.
- **What `max_size=10` should be.** The ceiling is what makes exhaustion
  reachable at all, and picking it properly needs a load test this repository
  does not have. This ADR makes exhaustion *visible*, which is the prerequisite
  for measuring it.
- **Retry or backpressure on exhaustion.** `StoreUnavailable` is already marked
  retryable and `ARCHITECTURE.md` §4 degrades toward human review. Who retries,
  and how many times, is the gateway's decision at S8.2.

## Alternatives rejected

- **Set `command_timeout` on the pool as a safety net.** It would default every
  statement, including ones that never asked, and `AnthropicClient.__init__`
  already states this repository's position: "a default is how a call ends up
  with one nobody chose." It would also silently cover a future call site that
  forgot `timeout=`, which is the review signal disappearing rather than the bug
  being fixed. Every statement passes its own timeout today; that is the
  property worth keeping.
- **Give `transaction` a default of five seconds.** The cheapest edit. It is
  also how `tenant_transaction`'s required argument came to be required: a
  defaulted timeout is invisible at the call site, so nobody ever revisits it,
  and the one call site that needed a different number gets the wrong one
  silently. Required arguments are how this codebase makes a timeout a decision.
- **Add `GM_POOL_ACQUIRE_TIMEOUT_S` to `Settings`.** A ninth required variable,
  a `.env.example` entry and a notebook row, to express something every call
  site already knows. `store_timeout_s` is documented as the store's per-call
  ceiling and acquiring a connection is part of a store call. A second knob
  would mostly be set to the same value and drift from it.
- **Bound the wait in each store instead.** `PgVectorStore`, `OutboxRelay`,
  `NamespaceEntityResolver`, the applier and two scripts all acquire
  connections. Five copies of a `wait_for` is five places for one to be
  forgotten, and the reason `pool.py` exists at all is that `SET LOCAL
  app.tenant_id` must have exactly one implementation.
- **Leave it, because the pool has never been exhausted.** It has never been
  exhausted because nothing has ever put load on it — the MCP server is a
  single stdio session. S8.1's gateway is the step that makes concurrency real,
  and discovering this then means discovering it as a hang in production rather
  than as a line in a module nobody had to page in.

## Consequences

- **A saturated process fails instead of hanging.** The visible change is a
  `StoreUnavailable` after `store_timeout_s` where there would have been
  silence. It is retryable, it is in the error contract already, and it degrades
  toward human review like every other store fault.
- **`transaction`'s signature changes, and there is exactly one bare call
  site** — `OutboxRelay._claim`. Everything else already reaches the pool
  through `tenant_transaction`, which has always had the value to pass.
- **The worst-case wall clock for one governed write rises to roughly three
  times `store_timeout_s`** under a pool that is both exhausted and slow. It was
  previously unbounded, so this is a ceiling where there was none — but it is
  not the request budget an SLO wants, and S8.2 is where that arrives.
- **Exhaustion becomes greppable.** The message names `max_size`, so the first
  question after an alert — "is this the database or is this us?" — is answered
  by the log line rather than by a dashboard nobody has built yet.
- **`scripts/` and the test fixtures gain an explicit connect timeout** they
  were already subject to. No behaviour changes; the 60 seconds simply stops
  being invisible.

"""GuardMem AI's async worker.  BUILD_NOTEBOOK.md S8.4

`ADR-0003` is write-ahead accept, async eval: the gateway takes responsibility for a
proposal in under 80 ms and the governing happens here, off the request. `PRD.md`
§6.1 budgets full evaluation at p95 1.6 s and calls it "not user-blocking", which is
only true if it runs somewhere nobody is waiting.

One job ships at S8.4 - `evaluate`. `PROJECT_TREE.md` names `compact`, `reindex`,
`digest` and `sla_sweeper`, and each arrives with the step that needs it.

**The outbox relay binding was this step's, and it is missing.** S3.3 put the
relay's logic in `guardmem_core.memory.relay` and gave "the arq task that calls it
on a schedule" to the step that builds the worker. S8.4 shipped without it, so no
process runs the relay: a written row stays `visible=false`, and only
`scripts/seed_demo_tenant.py` drains the outbox. The binding is an arq cron calling
`run_once()`, not a second implementation - but on the default in-process graph
its graph writes would live only in the worker's memory, so it wants
`GM_GRAPH_BACKEND=neo4j` decided first.

`main.py` owns the process and its resources; `composition.py` owns the pipeline's
thirteen dependencies. They are separate because they fail differently - a bad
composition is a job that raises on every attempt, and a bad startup is a worker that
never takes one.
"""

from worker.composition import ONTOLOGY, POLICY_VERSION, build_deps
from worker.main import QUEUE, WorkerSettings, main
from worker.tasks.evaluate import evaluate

__all__ = [
    "ONTOLOGY",
    "POLICY_VERSION",
    "QUEUE",
    "WorkerSettings",
    "build_deps",
    "evaluate",
    "main",
]

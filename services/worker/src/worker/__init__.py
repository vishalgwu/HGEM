"""GuardMem AI's async worker.  BUILD_NOTEBOOK.md S8.4

`ADR-0003` is write-ahead accept, async eval: the gateway takes responsibility for a
proposal in under 80 ms and the governing happens here, off the request. `PRD.md`
§6.1 budgets full evaluation at p95 1.6 s and calls it "not user-blocking", which is
only true if it runs somewhere nobody is waiting.

One job ships at S8.4 - `evaluate`. `PROJECT_TREE.md` names `compact`, `reindex`,
`digest`, `sla_sweeper` and the outbox relay binding, and each arrives with the step
that needs it. The relay is worth a note: `S3.3` put its logic in
`guardmem_core.memory.relay`, so the task here will be the arq binding that calls
`run_once()` rather than a second implementation.

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

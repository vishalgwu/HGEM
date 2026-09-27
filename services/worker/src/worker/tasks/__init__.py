"""The jobs this worker can run.  BUILD_NOTEBOOK.md S8.4

One module per job, because a job is the unit a queue retries and an operator reads
in a log - and because `PROJECT_TREE.md` lists six of them, which is more than one
module should hold.

Nothing is re-exported here. `WorkerSettings.functions` names each job explicitly,
and arq registers it by that name: the name is published interface, since it is what
the gateway's `enqueue_job` sends over Redis. A package-level alias would be a second
name for one job, and a queue with two names for one function is a queue where a
rename silently stops draining.
"""

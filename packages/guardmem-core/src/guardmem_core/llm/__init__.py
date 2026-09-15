"""Model provider adapters and routing.  BUILD_NOTEBOOK.md S1.7

The `LLMClient` protocol and its response model (S1.7), the three provider
adapters behind it (S9.1), and `entailment.py` - `EntailFn`'s only producer,
which lives here rather than in `pipeline/l3_score/` because that package is
pure by design and this one makes a model call.

The tier router arrives at S9.2, circuit breakers and cross-provider fallback at
S9.3, caching at S9.4 and the budget ledger at S10.1.
"""

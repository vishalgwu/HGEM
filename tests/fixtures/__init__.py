"""Shared test material: in-memory fakes and hypothesis strategies.  S1.7

A package rather than a bare directory so that mypy resolves these modules to
one name. With `tests/` on sys.path and no `__init__.py` here, mypy sees
`strategies.py` as both `strategies` and `fixtures.strategies` and refuses to
check either. pytest imports them as `fixtures.fakes` / `fixtures.strategies`
either way.
"""

"""benchmarks.defc_autojunk — DEF-C autojunk measurement harness.

This directory's modules are importable as `benchmarks.defc_autojunk.X`
when `benchmarks` is on sys.path. The test suite (under tests/) sets
that up via `sys.path.insert(0, str(REPO_ROOT))` and then imports
`from benchmarks.defc_autojunk.similarity import ...`. The harness
scripts (harness.py, templated_analysis.py) likewise import their
sibling modules by absolute name and assume the repo root is on
sys.path (the operator runs them with `python3
benchmarks/defc_autojunk/<script>.py` from the repo root, where
`agent` is a top-level package and `benchmarks` is a directory on
the same path).

The `__init__.py` here is intentionally empty: the package is a
namespace for the harness's own modules and has no runtime state
to initialize. Importing this package will also import
`benchmarks.defc_autojunk.similarity`, which transitively imports
`agent.chunk_quality` — the only piece of vectr's own code the
harness needs at import time. The full vectr test stack is not
required (the harness does not load the searcher or any
embedder-backed module), so this stays importable in any
environment that has `agent.chunk_quality` available.
"""

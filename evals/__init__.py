"""Golden-set evaluation: measures a model, never gates a merge.

`evals/README.md` states the split in full: `pytest` proves the plumbing
against recorded response bodies and never calls a model; this package calls
real models, costs real money, and its results vary between runs. It is not
part of `pytest` and is not a gate before Stage 3 (ADR-0009, ADR-0014).
"""

# Regional memory poisoning experiment

## Persistence follow-up

`InheritanceStore` adds a SQLite-backed experimental inheritance boundary with
separate active, audit, and lineage namespaces. Complete records are validated
against closed schemas before insertion; IDs use UUID4 and timestamps are separate.
The host promotes an active candidate using an exact numeric evidence check.
The promotion decision and lineage record commit in one transaction. Rejections
remain audit-only. Historical records reject UPDATE and DELETE operations.

Run `python3 -m pytest -q tests/test_inheritance_store.py` for restart, namespace
isolation, rollback, schema rejection, and poisoning controls. Clean state survives;
poisoned state is withheld; corrupted backing evidence still defeats this rule.
The new store is separate from the existing orchestrator and MemoryStore. It does
not yet implement origin contracts, branch decisions, stability windows, or
node-level communication permissions. Run/region filters provide lookup scoping,
not authentication. The host API must not be exposed to untrusted workers.

## Original comparison

Run from the checkout after installing the project dependencies:

```sh
PYTHONPATH=src python3 -m neural_node_swarm.poisoning_experiment
```

This is a deterministic mechanism test, not a model benchmark or an integration
test of the production memory path. It implements an isolated comparison harness
alongside the existing runtime. It does not establish that the current runtime
enforces promotion, regional permissions, or process isolation.

Two regions receive hand=10 and leg=20 in the clean scenario. The poisoning
scenario substitutes a plausible, structurally valid hand=90. Both policies
receive the same candidates and evidence registry and execute the same immediate
and evidence checks. Shared memory persists any immediate pass; governed memory
requires the evidence check to pass before recording inheritance. Active state
can contain the wrong candidate in both policies.

Six fresh simulated successors per region read only durable memory. Missing
state causes abstention. This deliberate read policy isolates durable inheritance;
it does not test propagation among workers authorized to read unpromoted active
state. All decisions, candidate values, checks, and read provenance appear in the
JSON report. Logical IDs are deterministic and local to each isolated case.

| Scenario | Policy | Wrong reads | Correct reads | Abstentions | Contaminated regions |
| --- | --- | ---: | ---: | ---: | ---: |
| Clean | Shared | 0 | 12 | 0 | 0 |
| Clean | Promotion | 0 | 12 | 0 | 0 |
| Poison | Shared | 6 | 6 | 0 | 1 |
| Poison | Promotion | 0 | 6 | 6 | 0 |
| Poison plus corrupted evidence | Shared | 6 | 6 | 0 | 1 |
| Poison plus corrupted evidence | Promotion | 6 | 6 | 0 | 1 |

The result demonstrates the consequence of enforcing an evidence check at the
inheritance boundary. It does not show that promotion is superior to a shared
memory system that enforces that same check at write time. There is no statistical
uncertainty estimate: these are fixed synthetic cases, not sampled model trials.

Next experiments should exercise the real persistence and reference-authorization
paths, allow bounded active-state consumption, inject multi-hop contamination,
and compare promotion with evidence-gated shared memory at matched cost. Model
trials remain paused pending the user's decision to resume that work.

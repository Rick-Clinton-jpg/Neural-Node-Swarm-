# Five-node objective-retention benchmark: observed results

This lab contains deterministic fixtures for two paired checks. Each pair receives
the same input. The stronger check examines either consistency with supplied
evidence or the exact fixed-objective prefix; its negative control checks only
field shape or trace structure. `summarize_paired_cases()` counts the four
possible combinations of their Boolean verdicts. It reruns the fixture builders
and checks, so the counts do not depend on stored results or a model call.

| Fixture family | Cases | Both pass | Stronger check only | Control only | Both fail |
| --- | ---: | ---: | ---: | ---: | ---: |
| Grounded candidate and schema-only control | 2 | 1 | 0 | 1 | 0 |
| Five-handoff prefix check and structural control | 2 | 1 | 0 | 1 | 0 |

In the grounded pair, `observed-progress` passes both checks. The
`unsupported-completion` candidate claims completion while its supplied record
reports progress: the grounded check rejects it, and the schema-only control
accepts it. In the relay pair, `retained-prefix` passes both checks. The
`drifted-prefix` trace changes node 3's objective text while preserving its
five-handoff structure: the prefix check rejects it, and the structural control
accepts it. These are fixture outcomes, not measured error rates.

The ledger records four earlier runs with passing operator gate verdicts and
zero full-suite failures on their work commits. For this fifth worktree, the two
new summary tests first failed during collection because the summary module did
not exist; after implementation they pass. The full suite currently reports
**51 passed** (`python3 -m pytest -q`). The fifth operator gate has not been run
here, so there is no fifth gate verdict in this report.

The evidence records are supplied by the fixtures; the grounded check does not
establish their truth. The relay traces are synthetic; the prefix check can
miss semantic drift confined to the `Next` step and has not been evaluated on
real agent traces. The controls deliberately omit the corresponding semantic
checks, so their acceptance of the two adversarial fixtures shows a difference
between these checks on these inputs only. This experiment has no unprotected
control arm, live model measurements, or production observations. It cannot
establish a causal reduction in drift or any production-level guarantee.

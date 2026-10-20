# Neural Node on a loop

This branch runs the Neural Node Swarm architecture on its own development. The
architecture and the approach to catching failures are Rick Clinton's: disposable
nodes with a fixed lifetime, continuity carried only by schema-bound memory, and
deterministic external verification instead of self-report. This loop applies
them to the agent building the repository.

Each scheduled run is one node. It starts with no memory of earlier runs, fires
three rounds, and terminates. The successor inherits only what is committed here.
This file, `loop/gate.py` and `loop/backlog.json` are frozen at the commit that
added the gate, together with every test, schema, doc, workflow and the README.

## What a node may read

- `LOOP.md` (this file)
- `loop/backlog.json` (the fixed list of work, in order)
- `loop/handoff.json` (the predecessor's fixed-schema objective)
- `loop/ledger.jsonl` (the gate's measurements of every earlier run)
- the code: `src/`, `tests/`, `schemas/`, `docs/`

A node does not read commit messages, the pull request conversation, or
`neural-node-loop-attempt-*` branches. That is discipline, not enforcement: the
gate cannot see what a node reads. It can only measure what a node changes.

## The three rounds

**Round 1, dendrite: read memory.** Set up the checkout (see Commands), stop if
`loop/STOP` exists, then read the four files above. The run number is the number
of lines in `loop/ledger.jsonl` plus one. The item to work on is the
`backlog:item:<id>` reference in `loop/handoff.json`. If the last ledger entry
failed, read its `checks` to see which measurement failed.

**Round 2, soma: one bounded increment.** Make the smallest change that moves the
current backlog item forward. Write the new test first, in a new file under
`tests/`, and confirm it fails before changing `src/`. An item can take several
runs.

**Round 3, axon: emit the handoff and terminate.** Overwrite `loop/handoff.json`
for the successor, commit everything as one work commit, run the gate, push, and
end. The handoff must pass the repository's own `verify_node_output` as it was at
the anchor commit, so it carries no notes, reasoning or other side channel:

- `step_id` is `node-<run>:3`
- `objective` is the successor's next concrete step, in at most 1000 characters
- `required_memory_refs` contains `ledger:latest`, exactly one of
  `backlog:item:<id>` or `backlog:done`, and optionally `code:<path>` pointers
- the backlog reference is either the current item or the next one in order.
  Moving to the next item is your claim that the current item's acceptance text
  is met. The ledger records that claim as self-report.

## Rules

- Never modify, delete or rename a frozen path. New files may be added under
  `tests/`, `schemas/`, `docs/` and `outputs/`.
- Never add `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`,
  `sitecustomize.py` or `usercustomize.py`, and never change pytest configuration
  in `pyproject.toml`.
- Never write `loop/ledger.jsonl` or `loop/STOP`. Only the gate writes them.
- No live model calls, no network access in code or tests, no new runtime
  dependencies. Model trials stay paused until Rick resumes them.
- Do not install the project into the environment. The gate measures `src/`
  directly and refuses to run if an installed copy would shadow it.
- One work commit per run. Never force-push, rewrite history, merge, push to
  `main`, or edit the pull request.

## What the gate measures

`python loop/gate.py run` evaluates HEAD as the work commit against its parent:

- frozen paths unchanged since the anchor, and no forbidden files
- pytest configuration unchanged
- the full suite passes on the work commit, with no failures or skips
- every test that existed on the parent still exists
- at least one new test fails on the parent and passes on the work commit
- the handoff is valid and its backlog step is legal

A failed run is not deleted. The gate moves its commit to
`neural-node-loop-attempt-<run>`, resets the branch, and records the failure.
Two failures in a row, any frozen-path violation, a completed backlog, or run 12
writes `loop/STOP` and ends the loop.

`python loop/gate.py audit` re-measures every ledger entry from git and fails if
any recorded entry differs from the re-measurement, if the ledger was edited
rather than appended, or if a commit on the branch has no ledger entry. The
`loop-gate` workflow runs the audit on GitHub for every push.

What the gate cannot tell you: whether the tests are the right tests, whether an
item is really complete, or what a node read. Those need a human review. The
gate's own code is trusted from the anchor commit, so check that
`git diff <anchor> HEAD -- loop/gate.py .github LOOP.md loop/backlog.json` is
empty.

## Commands

```sh
git clone https://github.com/Rick-Clinton-jpg/neural-node-swarm- nns
cd nns
git checkout neural-node-loop
python -m pip install 'pytest>=8' 'jsonschema>=4.20'
test -f loop/STOP && cat loop/STOP   # stopped: do nothing

# ... rounds 1-3 ...

git add -A
git commit -m "node-<run>: <item>: <what changed>"
python loop/gate.py run --intent "<one line, at most 200 characters>" \
  --trailer "Co-Authored-By: <model> <noreply@anthropic.com>"
```

Then push according to the gate's exit code:

- `0` pass: `git push origin neural-node-loop`
- `1` fail: `git push origin neural-node-loop neural-node-loop-attempt-<run>`
- `3` environment or usage error: nothing was recorded. Fix the setup if you can
  and run the gate again; otherwise push nothing and end.
- `4` the loop is stopped: push nothing and end.

If a push is rejected, do not force it. End the run.

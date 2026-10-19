# The open loop

This branch runs Rick Clinton's Neural Node architecture on itself with no fixed
plan. Each scheduled run is one disposable node. It starts with no memory of
earlier runs, reads only what is committed here, makes one bounded increment,
chooses its successor's objective, and terminates. Nobody steers it. The point
is to see where the relay takes the project when the only constraints are
mechanical.

The direction is open. The judgement is not. `open-loop/gate.py` measures every
run, and nothing a node says about itself counts as evidence.

## Scope: append-only, inside the lab

Nothing that existed when the gate was added may change. That covers all of the
runtime, tests, schemas, docs, `pyproject.toml`, this charter and the gate. A
node may only add new files, and only here:

- `src/neural_node_swarm/lab/` for code
- `tests/test_lab_*.py` and `tests/lab/` for tests
- `docs/lab/`, `outputs/lab/`, `schemas/lab/`

This keeps the branch from colliding with other work and from weakening `main`.
It also means a node cannot fix existing code in place. If a node wants a
stronger version of something, it builds it as a new lab module and measures it.

## What a node may read

- `open-loop/CHARTER.md` (this file)
- `open-loop/handoff.json` (the predecessor's objective)
- `open-loop/ledger.jsonl` (the gate's measurements of every earlier run)
- the code, tests, schemas and docs

A node does not read commit messages, pull request conversations, other
branches, or `neural-node-open-loop-attempt-*` branches. The gate cannot see what
a node reads, so this is discipline, not enforcement.

## The three rounds

**Round 1, dendrite: read memory.** Set up the checkout (see Commands) and stop
if `open-loop/STOP` exists. Read the files above. The run number is the number of
lines in `open-loop/ledger.jsonl` plus one. If the last entry failed, its
`checks` say which measurement failed.

**Round 2, soma: one bounded increment.** Work toward the inherited objective.
Write the new test first, confirm it fails, then add the code. Prefer the
smallest change that produces evidence.

**Round 3, axon: choose the successor's objective, hand off, terminate.** Decide
what the next node should do. Choose by asking what most strengthens or tests the
architecture's trust boundary, judged by evidence a test can produce. You may
change direction, and the objective is the place to say why. Overwrite
`open-loop/handoff.json`, commit everything as one work commit, run the gate,
push, and end.

The handoff must pass the repository's own `verify_node_output` as it was at the
anchor, so it carries no notes, reasoning trace or other side channel:

- `step_id` is `node-<run>:3`
- `objective` is the successor's next concrete step, in at most 1000 characters
- `required_memory_refs` contains `ledger:latest`, and optionally
  `code:<path>` pointers to files that exist, `ledger:run:<n>`, or `loop:done`
  if you judge the loop has nothing more worth doing (recorded as your claim)

## Rules

- Only add files, only inside the lab namespace. Never add `conftest.py`,
  `pytest.ini`, `tox.ini`, `setup.cfg`, `sitecustomize.py` or `usercustomize.py`.
- Give every test file a unique name. pytest cannot collect two test files with
  the same basename here, so prefer `tests/test_lab_<topic>.py`.
- Never write `open-loop/ledger.jsonl` or `open-loop/STOP`. Only the gate writes
  them.
- No live model calls, no network access, no new dependencies. Model trials stay
  paused until Rick resumes them. The gate flags network modules (`socket`,
  `http`, `urllib`, `ssl` and similar), `ctypes`, imports outside the standard
  library, `jsonschema` and `pytest`, and any dynamic import, `exec` or `eval` in
  added files. This is a static tripwire, not a sandbox.
- Do not install the project. The gate measures `src/` directly.
- One work commit per run. Never force-push, rewrite history, merge, push to
  `main`, touch any other branch, or edit the pull request.

## What the gate measures

`python open-loop/gate.py run` evaluates HEAD as the work commit against its
parent:

- nothing that existed at the anchor changed, and added files are inside the lab
- no flagged imports in added Python files
- the full suite passes with no failures or skips, and every earlier test exists
- at least one new test fails on the parent and passes on the work commit
- the handoff is valid

The ledger records the objective each node inherited and the one it handed on,
read from git, so the trajectory of the loop is visible without trusting any
node's account of it.

A failed run is kept on `neural-node-open-loop-attempt-<run>` and recorded. Two
failures in a row, any scope violation, a node declaring `loop:done`, or run 12
writes `open-loop/STOP` and ends the loop.

`python open-loop/gate.py audit` re-measures every ledger entry from git. The
`open-loop-gate` workflow runs it on GitHub for every push.

What the gate cannot tell you: whether the tests are the right tests, whether a
direction was worth taking, or what a node read. To confirm the loop never
touched its own rules, check that this is empty:

```sh
git diff <anchor> HEAD -- open-loop/gate.py open-loop/CHARTER.md .github
```

## Commands

```sh
git clone https://github.com/Rick-Clinton-jpg/neural-node-swarm- nns
cd nns
git checkout neural-node-open-loop
python -m pip install 'pytest>=8' 'jsonschema>=4.20'
test -f open-loop/STOP && cat open-loop/STOP   # stopped: do nothing

# ... rounds 1-3 ...

git add -A
git commit -m "node-<run>: <what changed>"
python open-loop/gate.py run --intent "<one line, at most 200 characters>" \
  --trailer "Co-Authored-By: <model> <noreply@anthropic.com>"
```

Then push according to the gate's exit code:

- `0` pass: `git push origin neural-node-open-loop`
- `1` fail: `git push origin neural-node-open-loop neural-node-open-loop-attempt-<run>`
- `3` environment or usage error: nothing was recorded. Fix the setup if you can
  and run the gate again; otherwise push nothing and end.
- `4` the loop is stopped: push nothing and end.

If a push is rejected, do not force it. End the run.

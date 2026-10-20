# The Neural Node swarm

This branch tests the swarm half of Rick Clinton's Neural Node architecture. The
two earlier loops were relays: one disposable worker per run. Here each run is a
wave of several disposable agents with separate roles:

- **recruiter**: reads the record and posts up to three tasks to a shared blackboard
- **workers**: build those tasks in parallel, each in its own isolated worktree, unable to see each other
- **integrator**: `swarm/gate.py`, which is code and not a model. It applies the workers' commits one at a time in task order and re-measures everything after each one
- **handoff**: records which backlog items are now closed, for the next wave

Every agent starts with no memory and terminates when its part is done. Only what
is committed carries forward.

The backlog is the one PR #1's relay completed, with identical items and
acceptance text, starting from the same commit on `main`. The only change is
`depends_on`, so the integrator can refuse tasks whose prerequisites are still
open. The experiment compares swarm with relay on the same work, under the same
rules.

## Files

- `swarm/CHARTER.md` (this file), `swarm/gate.py`, `swarm/backlog.json`: frozen
- `swarm/blackboard.json`: written by the recruiter each wave
- `swarm/handoff.json`: written by the handoff agent each wave
- `swarm/ledger.jsonl`, `swarm/STOP`: written only by the gate

## Rules for every agent

- Never modify, delete or rename a frozen path. Frozen paths are this file, the gate,
  the backlog, every existing file under `tests/`, `schemas/`, `docs/`, `outputs/`
  and `.github/`, `README.md` and `LICENSE`. New files may be added under `tests/`,
  `schemas/`, `docs/` and `outputs/`.
- Never add `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `sitecustomize.py`
  or `usercustomize.py`, and never change pytest configuration in `pyproject.toml`.
- Workers never touch anything under `swarm/`.
- No live model calls, no network access in code or tests, no new runtime
  dependencies. Model trials stay paused until Rick resumes them.
- Do not install the project. The gate measures `src/` directly.
- Never push, force-push, rewrite history, merge, touch `main` or another branch,
  or edit a pull request. Only the host pushes, and only what the gate prints.
- Read only the files your role names. Do not read commit messages, pull request
  conversations, other branches, or another worker's worktree. The gate cannot see
  what an agent reads, so this is discipline, not enforcement.

## Host protocol

The host is the scheduled session. It runs the protocol and starts the other
agents. **It never writes code, tests, the blackboard or the handoff itself.**
Run every command from the repository root on branch `neural-node-swarm-loop`.

1. Set up the checkout. If `swarm/STOP` exists, stop here. The wave number `N` is
   the number of lines in `swarm/ledger.jsonl` plus one.
2. **Recruiter.** Start one agent with the recruiter prompt below. When it ends,
   run `git add swarm/blackboard.json && git commit -m "wave-N: blackboard" -m "Co-Authored-By: <model> <noreply@anthropic.com>"`.
3. Run `python swarm/gate.py check-blackboard`.
   - Exit 0: run `python swarm/gate.py prepare`. It prints one worktree path and
     branch per task. Start one worker agent per task **at the same time**, in
     parallel, each with the worker prompt below and its own path. Wait until
     every worker has ended.
   - Exit 1: the blackboard is invalid. Start no workers.
4. Run `python swarm/gate.py integrate`. It integrates whatever the workers
   committed and prints the result.
5. **Handoff.** Start one agent with the handoff prompt below. When it ends, run
   `git add swarm/handoff.json && git commit -m "wave-N: handoff" -m "Co-Authored-By: <model> <noreply@anthropic.com>"`.
6. Run `python swarm/gate.py finish --intent "<one line, at most 200 characters>"
   --trailer "Co-Authored-By: <model> <noreply@anthropic.com>"`.
7. Run the `push:` command the gate printed, exactly as printed, unless the gate
   exited with 3 or 4. Exit 3 is an environment or usage error and nothing was
   recorded: fix the setup if you can and resume from the failed step, otherwise
   push nothing and end. If a push is rejected, do not force it. End the run.

Agents get no tools for talking to each other. Each one sees only its prompt and
the repository.

### Recruiter prompt

> You are the recruiter for wave N of the Neural Node swarm, in the repository at
> `<repo>`. Read `swarm/CHARTER.md`, `swarm/backlog.json`, `swarm/handoff.json`,
> `swarm/ledger.jsonl` and the code under `src/` and `tests/`. Then write
> `swarm/blackboard.json`: an object with exactly two keys, `wave` (the integer N)
> and `tasks` (a list of 1 to 3 tasks). Each task must pass the repository's own
> node-output schema:
> `schema_version` `"1.0"`; `step_id` `"wave-N:task-K"` with K counting from 1;
> `objective` (at most 1000 characters) saying concretely what one worker should
> build and test; `success_criteria` (a list of strings); `required_memory_refs`
> containing exactly one `backlog:item:<id>` and optionally `code:<path>` and
> `ledger:latest`; optional `constraints`. No other keys.
> A task may only take an item listed as open in `swarm/handoff.json` whose
> `depends_on` items are all closed. Several tasks may share an item. Workers run
> at the same time from the same starting commit and cannot see each other, and
> the integrator applies their work in task order and rejects anything that
> conflicts or fails once combined. Plan for that. Each task must be small enough
> for one worker to finish with a test that fails before its change and passes
> after it. Do not write any other file. End with one line saying what you posted.

### Worker prompt

> You are worker K for wave N of the Neural Node swarm. Work only inside the
> worktree at `<path>`, which is on its own branch. Read `swarm/CHARTER.md` and
> task K in `swarm/blackboard.json` there, the backlog item it names in
> `swarm/backlog.json`, and the code under `src/` and `tests/`. Do the task: write
> the new test first in a new file under `tests/` with a name no other worker is
> likely to choose (include the item name), confirm it fails, then change `src/`
> until the suite the gate measures passes:
> `python -m pytest -q --ignore=tests/test_swarm_gate.py`. Make exactly one commit
> on your branch with `git add -A && git commit -m "wave-N task-K: <item>: <what
> changed>" -m "Co-Authored-By: <your model> <noreply@anthropic.com>"`. Do not
> push, do not touch `swarm/`, do not read other worktrees. End with one line
> saying what you committed.

### Handoff prompt

> You are the handoff agent for wave N of the Neural Node swarm, in the
> repository at `<repo>`. Run `python swarm/gate.py status` to see which tasks the
> integrator accepted. Read `swarm/CHARTER.md`, `swarm/backlog.json`,
> `swarm/handoff.json` and the code as it now stands. Decide which open items now
> fully meet their acceptance text. You may only close an item that had accepted
> work this wave. Then overwrite `swarm/handoff.json` with an object that passes
> the node-output schema: `schema_version` `"1.0"`; `step_id` `"wave-N:handoff"`;
> `objective` (at most 1000 characters) telling the next recruiter what remains and
> what to watch for; `success_criteria`; `required_memory_refs` containing
> `ledger:latest` and one `backlog:item:<id>` for every item still open, or
> `backlog:done` alone if none remain, plus optional `code:<path>` refs; optional
> `constraints`. No other keys. Do not write any other file. End with one line
> saying which items you closed.

## What the gate measures

`integrate` takes each task in order. For each worker's single commit it:

1. applies the worker's change on top of the current result with a three-way
   merge, and rejects it on any conflict
2. on the combined result, checks frozen paths, forbidden files, pytest
   configuration and that the worker left `swarm/` alone
3. requires the full suite to pass with no failures or skips, and every earlier
   test to still exist
4. requires at least one new test that fails on the current result before the
   change and passes after it

Accepted work becomes the base for the next task, so work that passes alone but
breaks when combined is rejected. `finish` then checks the handoff: it must pass
the anchor's own verifier, list every open item, never reopen an item, and close
only items with accepted work this wave.

A wave passes when its blackboard is valid, at least one task is accepted, no
frozen path changed, and the handoff is valid. A failed wave is kept on
`neural-node-swarm-loop-attempt-N` and the branch is reset. Every worker's
original commit is kept on `neural-node-swarm-loop-wN-tK`. Any frozen-path
violation, two failed waves in a row, a completed backlog, or wave 5 writes
`swarm/STOP`.

The gate leaves its own tests, `tests/test_swarm_gate.py`, out of its
measurements to keep waves fast. They are frozen, and the repository's `tests`
workflow runs them on every push.

`python swarm/gate.py audit` re-derives every ledger entry from git: it redoes
each merge, checks that every integrated commit holds exactly the merged result
of its worker's commit, and re-measures every check. The `swarm-gate` workflow
runs it on GitHub for every push.

What the gate cannot tell you: whether the tests are the right tests, whether a
closed item really meets its acceptance text, what any agent read, or whether the
host started separate agents rather than doing the work itself. To confirm the
loop never touched its own rules, check that this is empty:

```sh
git diff <anchor> HEAD -- swarm/gate.py swarm/CHARTER.md swarm/backlog.json .github
```

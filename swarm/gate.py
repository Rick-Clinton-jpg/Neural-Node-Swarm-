#!/usr/bin/env python3
"""Deterministic gate and integrator for the Neural Node swarm (see swarm/CHARTER.md).

A wave is one scheduled run. A recruiter agent posts up to three tasks to the
blackboard, worker agents build them in parallel in isolated worktrees, and this
script - code, not a model - integrates their work one task at a time in task
order, re-measuring everything after each step. A handoff agent then records
which backlog items are closed, and the gate writes one ledger line for the wave.

  python swarm/gate.py check-blackboard     validate the committed blackboard
  python swarm/gate.py prepare              create one worktree and branch per task
  python swarm/gate.py integrate            integrate worker commits in task order
  python swarm/gate.py status               print the integration result
  python swarm/gate.py finish --intent ...  validate the handoff and record the wave
  python swarm/gate.py audit                re-derive every ledger entry from git

Nothing a node says about itself is evidence. The only free text recorded,
``self_report``, is read by no check.

Exit codes: 0 pass, 1 fail, 3 environment/usage error (nothing recorded),
4 loop already stopped (nothing recorded).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

BRANCH = "neural-node-swarm-loop"
GATE = "swarm/gate.py"
CHARTER = "swarm/CHARTER.md"
BACKLOG = "swarm/backlog.json"
BLACKBOARD = "swarm/blackboard.json"
HANDOFF = "swarm/handoff.json"
LEDGER = "swarm/ledger.jsonl"
STOP = "swarm/STOP"
STATE_NAME = "swarm-wave.json"
LEDGER_VERSION = "1.0"
INTENT_MAX = 200

# Paths as they exist at the anchor commit may never change afterwards.
FROZEN = (CHARTER, "README.md", "LICENSE", ".github", "tests", "schemas", "docs", "outputs", GATE, BACKLOG)
# Under these roots brand-new files may be added (never modified or deleted).
ADDABLE = ("tests/", "schemas/", "docs/", "outputs/")
# Files that could reconfigure or bypass pytest; never allowed to be added anywhere.
FORBIDDEN_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "sitecustomize.py", "usercustomize.py"}
# Workers may not touch anything under swarm/. Only the gate writes these two.
GATE_OWNED = (LEDGER, STOP)
# This gate's own tests are frozen and run by the repository's test workflow on every
# push. They are left out of the gate's measurements only to keep each wave fast.
GATE_TEST = "tests/test_swarm_gate.py"
REF_PATTERNS = (
    r"ledger:latest",
    r"ledger:wave:[1-9][0-9]*",
    r"backlog:item:[a-z0-9-]+",
    r"backlog:done",
    r"code:[A-Za-z0-9._/-]+",
)


class GateError(Exception):
    """Environment or usage problem. Nothing is recorded."""


# --------------------------------------------------------------------------- git


def git(root: Path, *args: str, check: bool = True, env: dict | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})
    if check and proc.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def show(root: Path, sha: str, path: str) -> str | None:
    if subprocess.run(["git", "cat-file", "-e", f"{sha}:{path}"], cwd=root, capture_output=True).returncode != 0:
        return None
    return git(root, "show", f"{sha}:{path}")


def commit_exists(root: Path, sha) -> bool:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return False
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, capture_output=True).returncode == 0


def parents_of(root: Path, sha: str) -> list[str]:
    return git(root, "rev-list", "--parents", "-n", "1", sha).split()[1:]


def tree_of(root: Path, sha: str) -> str:
    return git(root, "rev-parse", f"{sha}^{{tree}}").strip()


def changed(root: Path, old: str, new: str, *paths: str) -> set[str]:
    return set(git(root, "diff", "--no-renames", "--name-only", old, new, "--", *paths).split())


def name_status(root: Path, old: str, new: str, *paths: str) -> list[tuple[str, str]]:
    out = git(root, "diff", "--no-renames", "--name-status", old, new, "--", *paths)
    return [tuple(line.split("\t", 1)) for line in out.splitlines()]


def anchors(root: Path, head: str) -> list[str]:
    return git(root, "log", "--format=%H", "--diff-filter=A", head, "--", GATE).split()


def last_ledger_commit(root: Path, sha: str) -> str:
    return git(root, "log", "-1", "--format=%H", sha, "--", LEDGER).strip()


def branch_tip(root: Path, name: str) -> str | None:
    proc = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{name}"], cwd=root,
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def worker_branch(wave: int, task: int) -> str:
    return f"{BRANCH}-w{wave}-t{task}"


def attempt_branch(wave: int) -> str:
    return f"{BRANCH}-attempt-{wave}"


def merge_candidate(root: Path, base: str, tip: str, work: str) -> str | None:
    """Apply the worker's change (base..work) on top of tip. Tree id, or None on conflict."""
    proc = subprocess.run(["git", "merge-tree", "--write-tree", f"--merge-base={base}", tip, work],
                          cwd=root, capture_output=True, text=True)
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise GateError(f"git merge-tree failed (git 2.40 or later is required): {proc.stderr.strip()}")
    return proc.stdout.split()[0]


def commit_tree(root: Path, tree: str, parent: str, message: str, author_of: str | None = None) -> str:
    env = {}
    if author_of:
        name, email, date = git(root, "log", "-1", "--format=%an%x00%ae%x00%aI", author_of).rstrip("\n").split("\x00")
        env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date}
    # The integrator commits as itself, so this works on machines with no git identity.
    env.update({"GIT_COMMITTER_NAME": "swarm-gate", "GIT_COMMITTER_EMAIL": "swarm-gate@localhost"})
    return git(root, "commit-tree", tree, "-p", parent, "-m", message, env=env).strip()


@contextmanager
def worktree(root: Path, sha: str):
    holder = Path(tempfile.mkdtemp(prefix="swarm-gate-wt-"))
    path = holder / "wt"
    git(root, "worktree", "add", "--detach", "--force", str(path), sha)
    try:
        yield path
    finally:
        git(root, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(holder, ignore_errors=True)
        git(root, "worktree", "prune", check=False)


# ------------------------------------------------------------------------ pytest


def _clean_env(tree: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST") and k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def probe_import(tree: Path) -> None:
    """Refuse to measure if the package under test would load from somewhere else."""
    code = ("import sys, os; sys.path.insert(0, 'src'); import neural_node_swarm; "
            "print(os.path.realpath(neural_node_swarm.__file__))")
    proc = subprocess.run([sys.executable, "-c", code], cwd=tree, env=_clean_env(tree), capture_output=True, text=True)
    if proc.returncode != 0:
        return  # the package itself is broken; the test results will show it
    location = Path(proc.stdout.strip())
    if not location.is_relative_to((tree / "src").resolve()):
        raise GateError(f"neural_node_swarm resolves to {location}, outside the tree under test")


_RANK = {"passed": 0, "skipped": 1, "failed": 2}
_SUITE_CACHE: dict[str, dict[str, str]] = {}


def run_pytest(tree: Path, targets: list[str], *, keep_going: bool = True) -> dict[str, str]:
    """Return {test_id: passed|failed|skipped}. Collection errors appear as collect:: ids."""
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tree),
               f"--junitxml={junit}", "-o", "junit_family=xunit2", f"--ignore={GATE_TEST}"]
        if keep_going:
            cmd.append("--continue-on-collection-errors")
        subprocess.run([*cmd, *targets], cwd=tree, env=_clean_env(tree), capture_output=True, text=True, timeout=3600)
        if not junit.exists():
            return {"session::no-report": "failed"}
        outcomes: dict[str, str] = {}
        for case in ET.parse(junit).getroot().iter("testcase"):
            cls, name = case.get("classname", ""), case.get("name", "")
            test_id = f"{cls}::{name}" if cls else f"collect::{name}"
            if case.find("failure") is not None or case.find("error") is not None:
                outcome = "failed"
            elif case.find("skipped") is not None:
                outcome = "skipped"
            else:
                outcome = "passed"
            outcomes[test_id] = max(outcomes.get(test_id, "passed"), outcome, key=_RANK.__getitem__)
        return outcomes


def suite(root: Path, sha: str) -> dict[str, str]:
    tree = tree_of(root, sha)
    if tree not in _SUITE_CACHE:
        with worktree(root, sha) as path:
            probe_import(path)
            _SUITE_CACHE[tree] = run_pytest(path, ["tests"])
    return _SUITE_CACHE[tree]


def summarize(outcomes: dict[str, str]) -> dict[str, int]:
    values = list(outcomes.values())
    return {"total": len(values), "passed": values.count("passed"),
            "failed": values.count("failed"), "skipped": values.count("skipped")}


# ------------------------------------------------------------ schema & backlog


def load_backlog(root: Path, anchor: str) -> dict:
    text = show(root, anchor, BACKLOG)
    if text is None:
        raise GateError("backlog missing at anchor")
    return json.loads(text)


def anchor_verify(root: Path, anchor: str, data) -> dict:
    """Validate with the verifier and schema exactly as they were at the anchor commit."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "src" / "neural_node_swarm").mkdir(parents=True)
        (base / "schemas").mkdir()
        module_path = base / "src" / "neural_node_swarm" / "verifier.py"
        module_path.write_text(show(root, anchor, "src/neural_node_swarm/verifier.py") or "")
        (base / "schemas" / "node_output.schema.json").write_text(show(root, anchor, "schemas/node_output.schema.json") or "")
        spec = importlib.util.spec_from_file_location("_swarm_anchor_verifier", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look the module up by name
        try:
            spec.loader.exec_module(module)
            return module.verify_node_output(data)
        finally:
            sys.modules.pop(spec.name, None)


def check_node_output(root: Path, anchor: str, data, step_id: str, label: str) -> list[str]:
    result = anchor_verify(root, anchor, data)
    errors = [f"{label}verifier:{c['name']}" for c in result["checks"] if not c["passed"]]
    if not isinstance(data, dict):
        return errors or [f"{label}must be an object"]
    if data.get("step_id") != step_id:
        errors.append(f"{label}step_id must be {step_id}")
    refs = data.get("required_memory_refs")
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, str) or not any(re.fullmatch(p, ref) for p in REF_PATTERNS):
            errors.append(f"{label}unsupported ref {ref!r}")
    return errors


def backlog_refs(data) -> list[str]:
    refs = data.get("required_memory_refs", []) if isinstance(data, dict) else []
    refs = refs if isinstance(refs, list) else []
    return [r for r in refs if isinstance(r, str) and (r.startswith("backlog:item:") or r == "backlog:done")]


def open_items(root: Path, sha: str) -> list[str]:
    """Items the handoff at sha leaves open. The anchor handoff lists every item."""
    try:
        data = json.loads(show(root, sha, HANDOFF) or "")
    except json.JSONDecodeError:
        return []
    refs = backlog_refs(data)
    if "backlog:done" in refs:
        return []
    return sorted({r.removeprefix("backlog:item:") for r in refs})


def check_blackboard(root: Path, anchor: str, sha: str, wave: int, open_before: list[str],
                     backlog: dict) -> tuple[list[str], list[str]]:
    """Return (errors, item per task). Items are only reported when the blackboard is valid."""
    text = show(root, sha, BLACKBOARD)
    if text is None:
        return ["blackboard missing"], []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"blackboard is not JSON: {error.msg}"], []
    if not isinstance(data, dict) or set(data) != {"wave", "tasks"}:
        return ["blackboard must be an object with exactly the keys wave and tasks"], []
    errors = []
    if data["wave"] != wave or type(data["wave"]) is not int:
        errors.append(f"blackboard wave must be {wave}")
    tasks = data["tasks"]
    limit = backlog["max_tasks_per_wave"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= limit:
        return errors + [f"blackboard must post 1-{limit} tasks"], []
    by_id = {entry["id"]: entry for entry in backlog["items"]}
    items = []
    for number, task in enumerate(tasks, start=1):
        label = f"task {number}: "
        errors += check_node_output(root, anchor, task, f"wave-{wave}:task-{number}", label)
        refs = backlog_refs(task)
        if len(refs) != 1 or refs[0] == "backlog:done":
            errors.append(f"{label}exactly one backlog:item ref required")
            items.append(None)
            continue
        item = refs[0].removeprefix("backlog:item:")
        items.append(item)
        if item not in by_id:
            errors.append(f"{label}unknown backlog item {item!r}")
        elif item not in open_before:
            errors.append(f"{label}item {item!r} is not open")
        else:
            for dependency in by_id[item].get("depends_on", []):
                if dependency in open_before:
                    errors.append(f"{label}item {item!r} depends on open item {dependency!r}")
    return errors, ([] if errors else items)


def check_handoff(root: Path, anchor: str, sha: str, wave: int, open_before: list[str],
                  accepted_items: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (errors, open_after, closed)."""
    text = show(root, sha, HANDOFF)
    if text is None:
        return ["handoff missing"], open_before, []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"handoff is not JSON: {error.msg}"], open_before, []
    errors = check_node_output(root, anchor, data, f"wave-{wave}:handoff", "")
    if not isinstance(data, dict):
        return errors, open_before, []
    refs = data.get("required_memory_refs") if isinstance(data.get("required_memory_refs"), list) else []
    if "ledger:latest" not in refs:
        errors.append("missing ledger:latest")
    for ref in refs:
        if isinstance(ref, str) and ref.startswith("ledger:wave:") and int(ref.rsplit(":", 1)[1]) >= wave:
            errors.append(f"ref to a wave not yet recorded {ref!r}")
    listed = backlog_refs(data)
    done = "backlog:done" in listed
    after = sorted({r.removeprefix("backlog:item:") for r in listed if r != "backlog:done"})
    if done and after:
        errors.append("backlog:done cannot be combined with open items")
    if not done and not after:
        errors.append("list every open item, or backlog:done when none remain")
    if not set(after) <= set(open_before):
        errors.append(f"cannot reopen or add items: {sorted(set(after) - set(open_before))}")
    if done and not open_before:
        errors.append("backlog was already done")
    closed = sorted(set(open_before) - set(after))
    for item in closed:
        if item not in accepted_items:
            errors.append(f"cannot close {item!r}: no task on it was accepted this wave")
    return errors, after, closed


# --------------------------------------------------------------------- evaluate


def frozen_violations(root: Path, anchor: str, base: str, work: str, cand: str) -> list[str]:
    found = set()
    for status, path in name_status(root, anchor, cand, *FROZEN):
        if status == "A" and path.startswith(ADDABLE) and PurePosixPath(path).name not in FORBIDDEN_NAMES:
            continue
        found.add(f"{status} {path}")
    for status, path in name_status(root, anchor, cand):
        if status == "A" and PurePosixPath(path).name in FORBIDDEN_NAMES:
            found.add(f"forbidden-file {path}")
    for path in changed(root, base, work, "swarm"):
        found.add(f"swarm-owned {path}")
    return sorted(found)


def pytest_config(root: Path, sha: str):
    text = show(root, sha, "pyproject.toml")
    if text is None:
        return None
    data = tomllib.loads(text)
    plugins = data.get("project", {}).get("entry-points", {}).get("pytest11")
    return {"tool.pytest": data.get("tool", {}).get("pytest"), "pytest11": plugins}


def evaluate(root: Path, anchor: str, base: str, work: str, tip: str, cand: str) -> dict:
    checks: dict = {}
    checks["frozen_violations"] = frozen_violations(root, anchor, base, work, cand)
    checks["pytest_config_unchanged"] = pytest_config(root, anchor) == pytest_config(root, cand)
    suite_c, suite_t = suite(root, cand), suite(root, tip)
    added = [path for status, path in name_status(root, tip, cand, "tests") if status == "A"]
    new_files = [p for p in added if PurePosixPath(p).name.startswith("test_") and p.endswith(".py") and p != GATE_TEST]
    red: dict[str, str] = {}
    if new_files:
        with worktree(root, tip) as tree:
            for path in added:
                target = tree / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.run(["git", "show", f"{cand}:{path}"], cwd=root,
                                                  capture_output=True, check=True).stdout)
            red = run_pytest(tree, new_files, keep_going=False)
    checks["suite"] = summarize(suite_c)
    checks["suite_green"] = bool(suite_c) and all(v == "passed" for v in suite_c.values())
    tip_ids = {t for t in suite_t if not t.startswith("collect::")}
    checks["tests_missing"] = sorted(tip_ids - set(suite_c))
    new_ids = sorted(set(suite_c) - set(suite_t))
    checks["new_tests"] = new_ids
    checks["red_on_tip"] = [t for t in new_ids if red.get(t, "absent") != "passed"]
    checks["red_green_ok"] = bool(checks["red_on_tip"]) and all(suite_c[t] == "passed" for t in new_ids)
    return checks


def accepted(checks: dict) -> bool:
    return (not checks["frozen_violations"] and checks["pytest_config_unchanged"] and checks["suite_green"]
            and not checks["tests_missing"] and checks["red_green_ok"])


def integrate_task(root: Path, anchor: str, base: str, tip: str, number: int, item: str, wave: int,
                   work: str | None, *, recorded_commit: str | None = None, audit: bool = False) -> dict:
    """Decide one task. In audit mode an accepted task must match the recorded integrated commit."""
    record = {"task": number, "item": item, "worker_commit": work, "outcome": None,
              "integrated_commit": None, "checks": None}
    if work is None:
        record["outcome"] = "missing"
        return record
    if parents_of(root, work) != [base]:
        record["outcome"] = "rejected_shape"
        return record
    tree = merge_candidate(root, base, tip, work)
    if tree is None:
        record["outcome"] = "rejected_conflict"
        return record
    message = f"swarm: integrate wave {wave} task {number} ({item})"
    if audit and recorded_commit is not None:
        if not commit_exists(root, recorded_commit) or parents_of(root, recorded_commit) != [tip] \
                or tree_of(root, recorded_commit) != tree:
            record["outcome"] = "integrated_commit_mismatch"
            return record
        cand = recorded_commit
    else:
        cand = commit_tree(root, tree, tip, message, author_of=work)
    checks = evaluate(root, anchor, base, work, tip, cand)
    record["checks"] = checks
    if accepted(checks):
        record["outcome"] = "accepted"
        record["integrated_commit"] = cand
    else:
        record["outcome"] = "rejected_checks"
    return record


def build_entry(*, wave, anchor, parent, blackboard_commit, blackboard_errors, tasks, handoff_commit,
                handoff_errors, open_before, open_after, closed, previous, max_waves, intent) -> dict:
    frozen = any(t["checks"] and t["checks"]["frozen_violations"] for t in tasks)
    any_accepted = any(t["outcome"] == "accepted" for t in tasks)
    verdict = "pass" if (not blackboard_errors and any_accepted and not frozen and not handoff_errors) else "fail"
    if frozen:
        stop = "frozen_violation"
    elif verdict == "fail" and previous and previous[-1]["verdict"] == "fail":
        stop = "circuit_breaker"
    elif verdict == "pass" and not open_after:
        stop = "backlog_complete"
    elif wave >= max_waves:
        stop = "max_waves"
    else:
        stop = None
    return {
        "ledger_version": LEDGER_VERSION,
        "wave": wave,
        "anchor": anchor,
        "parent": parent,
        "blackboard_commit": blackboard_commit,
        "blackboard_errors": blackboard_errors,
        "tasks": tasks,
        "handoff_commit": handoff_commit,
        "handoff_errors": handoff_errors,
        "open_before": open_before,
        "open_after": open_after,
        "verdict": verdict,
        "stop": stop,
        # The handoff agent's judgement that these items meet their acceptance text.
        # The gate only checks that each had accepted work this wave.
        "self_report": {"intent": intent, "items_closed_claim": closed},
    }


def ledger_line(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"


def read_ledger(root: Path, sha: str) -> list[dict]:
    text = show(root, sha, LEDGER) or ""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --------------------------------------------------------------------- commands


def toplevel() -> Path:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GateError("run the gate inside the repository")
    return Path(proc.stdout.strip())


def state_path(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--absolute-git-dir").strip()) / STATE_NAME


def context(require_clean: bool = True) -> dict:
    root = toplevel()
    if git(root, "branch", "--show-current").strip() != BRANCH:
        raise GateError(f"must be on branch {BRANCH}")
    if require_clean and git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise GateError("working tree must be clean: commit first")
    head = git(root, "rev-parse", "HEAD").strip()
    found = anchors(root, head)
    if len(found) != 1:
        raise GateError(f"expected exactly one commit adding {GATE}, found {len(found)}")
    anchor = found[0]
    if show(root, head, STOP) is not None:
        print(f"loop stopped: {show(root, head, STOP).strip()}")
        raise SystemExit(4)
    backlog = load_backlog(root, anchor)
    previous = read_ledger(root, head)
    wave = len(previous) + 1
    if wave > backlog["max_waves"]:
        raise GateError("wave cap reached")
    return {"root": root, "head": head, "anchor": anchor, "backlog": backlog, "previous": previous, "wave": wave}


def blackboard_context() -> dict:
    """HEAD must be the blackboard commit: one commit on the last ledger commit, changing only the blackboard."""
    ctx = context()
    root, head = ctx["root"], ctx["head"]
    parents = parents_of(root, head)
    if len(parents) != 1 or parents[0] != last_ledger_commit(root, parents[0]) or head == ctx["anchor"]:
        raise GateError("HEAD must be the blackboard commit directly on top of the last ledger commit")
    parent = parents[0]
    if changed(root, parent, head) != {BLACKBOARD}:
        raise GateError(f"the blackboard commit must change only {BLACKBOARD}")
    ctx["parent"] = parent
    ctx["open_before"] = open_items(root, parent)
    ctx["errors"], ctx["items"] = check_blackboard(root, ctx["anchor"], head, ctx["wave"], ctx["open_before"],
                                                   ctx["backlog"])
    return ctx


def cmd_check_blackboard(_args) -> int:
    ctx = blackboard_context()
    print(json.dumps({"wave": ctx["wave"], "open_before": ctx["open_before"], "errors": ctx["errors"],
                      "items": ctx["items"]}, indent=2))
    return 0 if not ctx["errors"] else 1


def cmd_prepare(_args) -> int:
    ctx = blackboard_context()
    if ctx["errors"]:
        print(json.dumps({"errors": ctx["errors"]}, indent=2))
        print("blackboard invalid: do not start workers")
        return 1
    root, wave = ctx["root"], ctx["wave"]
    holder = root.parent / f"{root.name}-workers"
    out = []
    for number, item in enumerate(ctx["items"], start=1):
        name = worker_branch(wave, number)
        if branch_tip(root, name) is not None:
            raise GateError(f"branch {name} already exists")
        path = holder / f"w{wave}-t{number}"
        git(root, "worktree", "add", "-q", "-b", name, str(path), ctx["head"])
        out.append({"task": number, "item": item, "path": str(path), "branch": name})
    print(json.dumps(out, indent=2))
    return 0


def cmd_integrate(_args) -> int:
    ctx = blackboard_context()
    root, anchor, wave, head = ctx["root"], ctx["anchor"], ctx["wave"], ctx["head"]
    tip, tasks = head, []
    for number, item in enumerate(ctx["items"], start=1):
        work = branch_tip(root, worker_branch(wave, number))
        work = None if work in (None, head) else work
        record = integrate_task(root, anchor, head, tip, number, item, wave, work)
        tasks.append(record)
        if record["outcome"] == "accepted":
            tip = record["integrated_commit"]
    git(root, "reset", "-q", "--hard", tip)
    state = {"wave": wave, "anchor": anchor, "parent": ctx["parent"], "blackboard_commit": head,
             "blackboard_errors": ctx["errors"], "open_before": ctx["open_before"], "tasks": tasks, "tip": tip}
    state_path(root).write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    print(status_text(state))
    return 0


def status_text(state: dict) -> str:
    lines = [f"wave {state['wave']}: open before = {', '.join(state['open_before']) or 'none'}"]
    if state["blackboard_errors"]:
        lines.append(f"blackboard invalid: {state['blackboard_errors']}")
    for task in state["tasks"]:
        line = f"task {task['task']} ({task['item']}): {task['outcome']}"
        checks = task["checks"]
        if checks and task["outcome"] != "accepted":
            problems = {k: checks[k] for k in ("frozen_violations", "tests_missing") if checks[k]}
            if not checks["suite_green"]:
                problems["suite"] = checks["suite"]
            if not checks["red_green_ok"]:
                problems["red_on_tip"] = checks["red_on_tip"]
            if not checks["pytest_config_unchanged"]:
                problems["pytest_config_unchanged"] = False
            line += f" {json.dumps(problems)}"
        lines.append(line)
    accepted_items = sorted({t["item"] for t in state["tasks"] if t["outcome"] == "accepted"})
    lines.append(f"items with accepted work: {', '.join(accepted_items) or 'none'}")
    return "\n".join(lines)


def cmd_status(_args) -> int:
    root = toplevel()
    path = state_path(root)
    if not path.exists():
        raise GateError("no integration result: run integrate first")
    print(status_text(json.loads(path.read_text(encoding="utf-8"))))
    return 0


def cmd_finish(args) -> int:
    intent = " ".join(args.intent.split())
    if not intent or len(intent) > INTENT_MAX:
        raise GateError(f"--intent must be 1-{INTENT_MAX} characters")
    ctx = context()
    root, head, anchor, wave = ctx["root"], ctx["head"], ctx["anchor"], ctx["wave"]
    path = state_path(root)
    if not path.exists():
        raise GateError("no integration result: run integrate first")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["wave"] != wave or state["anchor"] != anchor:
        raise GateError("integration result belongs to a different wave")
    if parents_of(root, head) != [state["tip"]] or changed(root, state["tip"], head) != {HANDOFF}:
        raise GateError(f"HEAD must be one commit on the integration result that changes only {HANDOFF}")
    accepted_items = {t["item"] for t in state["tasks"] if t["outcome"] == "accepted"}
    errors, after, closed = check_handoff(root, anchor, head, wave, state["open_before"], accepted_items)
    entry = build_entry(wave=wave, anchor=anchor, parent=state["parent"], blackboard_commit=state["blackboard_commit"],
                        blackboard_errors=state["blackboard_errors"], tasks=state["tasks"], handoff_commit=head,
                        handoff_errors=errors, open_before=state["open_before"], open_after=after, closed=closed,
                        previous=ctx["previous"], max_waves=ctx["backlog"]["max_waves"], intent=intent)
    refs = [BRANCH] + [worker_branch(wave, t["task"]) for t in state["tasks"] if t["worker_commit"]]
    if entry["verdict"] == "fail":
        git(root, "branch", "-f", attempt_branch(wave), head)
        git(root, "reset", "-q", "--hard", state["parent"])
        refs.append(attempt_branch(wave))
    with open(root / LEDGER, "a", encoding="utf-8") as stream:
        stream.write(ledger_line(entry))
    paths = [LEDGER]
    if entry["stop"]:
        (root / STOP).write_text(entry["stop"] + "\n", encoding="utf-8")
        paths.append(STOP)
    git(root, "add", "--", *paths)
    message = f"ledger: wave {wave} {entry['verdict']}" + (f" (stop: {entry['stop']})" if entry["stop"] else "")
    if args.trailer:
        message += "\n\n" + "\n".join(args.trailer)
    git(root, "commit", "-q", "-m", message)
    path.unlink()
    print(json.dumps(entry, indent=2, sort_keys=True))
    print("\npush: git push origin " + " ".join(refs))
    return 0 if entry["verdict"] == "pass" else 1


def cmd_audit(_args) -> int:
    root = toplevel()
    head = git(root, "rev-parse", "HEAD").strip()
    problems: list[str] = []
    found = anchors(root, head)
    if len(found) != 1:
        print(json.dumps({"ok": False, "problems": [f"expected one commit adding {GATE}, found {len(found)}"]}))
        return 1
    anchor = found[0]
    backlog = load_backlog(root, anchor)
    base = parents_of(root, anchor)
    if len(base) != 1:
        problems.append("anchor must have exactly one parent")
    else:
        for status, path in name_status(root, base[0], anchor):
            if status != "A":
                problems.append(f"anchor changes an existing file: {status} {path}")
    if (show(root, anchor, LEDGER) or "") != "":
        problems.append("ledger must start empty at the anchor")
    if show(root, anchor, STOP) is not None or show(root, anchor, BLACKBOARD) is not None:
        problems.append("anchor must not contain STOP or a blackboard")
    if open_items(root, anchor) != sorted(entry["id"] for entry in backlog["items"]):
        problems.append("anchor handoff must list every backlog item as open")
    if git(root, "rev-list", "--merges", f"{anchor}..{head}").strip():
        problems.append("merge commits are not allowed on the loop branch")
    commits = git(root, "rev-list", "--first-parent", "--reverse", f"{anchor}..{head}").split()
    try:
        entries = read_ledger(root, head)
    except json.JSONDecodeError:
        entries = []
        problems.append("ledger contains a line that is not JSON")
    for sha in commits:
        before = show(root, f"{sha}^", LEDGER) or ""
        after = show(root, sha, LEDGER) or ""
        if before == after:
            continue
        added = after[len(before):] if after.startswith(before) else None
        if added is None or added.count("\n") != 1 or not added.endswith("\n"):
            problems.append(f"commit {sha[:12]}: ledger change is not an exact one-line append")

    index, previous, last_ledger = 0, [], anchor

    def take(expected: str, label: str) -> bool:
        nonlocal index
        if index >= len(commits) or commits[index] != expected:
            problems.append(f"{label}: expected {expected[:12]} next on the branch")
            return False
        index += 1
        return True

    for wave, entry in enumerate(entries, start=1):
        label = f"wave {wave}"
        if entry.get("wave") != wave or entry.get("parent") != last_ledger:
            problems.append(f"{label}: entry number or parent does not follow the previous ledger commit")
            break
        board = entry.get("blackboard_commit")
        if not commit_exists(root, board) or parents_of(root, board) != [last_ledger] \
                or changed(root, last_ledger, board) != {BLACKBOARD}:
            problems.append(f"{label}: blackboard commit missing or malformed (fetch attempt branches)")
            break
        open_before = open_items(root, last_ledger)
        errors, items = check_blackboard(root, anchor, board, wave, open_before, backlog)
        recorded_tasks = entry.get("tasks") or []
        if len(recorded_tasks) != len(items):
            problems.append(f"{label}: recorded {len(recorded_tasks)} tasks, blackboard posts {len(items)}")
            break
        tip, tasks, broken = board, [], False
        for number, (item, recorded) in enumerate(zip(items, recorded_tasks), start=1):
            work = recorded.get("worker_commit")
            if work is not None and not commit_exists(root, work):
                problems.append(f"{label} task {number}: worker commit not available (fetch worker branches)")
                broken = True
                break
            record = integrate_task(root, anchor, board, tip, number, item, wave, work,
                                    recorded_commit=recorded.get("integrated_commit"), audit=True)
            tasks.append(record)
            if record["outcome"] == "accepted":
                tip = record["integrated_commit"]
        if broken:
            break
        hand = entry.get("handoff_commit")
        if not commit_exists(root, hand) or parents_of(root, hand) != [tip] or changed(root, tip, hand) != {HANDOFF}:
            problems.append(f"{label}: handoff commit missing or not on the integration result")
            break
        accepted_items = {t["item"] for t in tasks if t["outcome"] == "accepted"}
        handoff_errors, after, closed = check_handoff(root, anchor, hand, wave, open_before, accepted_items)
        expected = build_entry(wave=wave, anchor=anchor, parent=last_ledger, blackboard_commit=board,
                               blackboard_errors=errors, tasks=tasks, handoff_commit=hand,
                               handoff_errors=handoff_errors, open_before=open_before, open_after=after,
                               closed=closed, previous=previous, max_waves=backlog["max_waves"],
                               intent=entry.get("self_report", {}).get("intent", ""))
        if expected != entry:
            differing = sorted(k for k in set(expected) | set(entry) if expected.get(k) != entry.get(k))
            problems.append(f"{label}: recorded entry does not match re-measurement ({', '.join(differing)})")
        if entry.get("verdict") == "pass":
            chain = [board] + [t["integrated_commit"] for t in tasks if t["outcome"] == "accepted"] + [hand]
            if not all(take(sha, label) for sha in chain):
                break
        ledger_commit = commits[index] if index < len(commits) else None
        expected_parent = hand if entry.get("verdict") == "pass" else last_ledger
        if ledger_commit is None or parents_of(root, ledger_commit) != [expected_parent] \
                or not changed(root, expected_parent, ledger_commit) <= set(GATE_OWNED):
            problems.append(f"{label}: ledger commit missing, misplaced or touching other files")
            break
        index += 1
        if (show(root, ledger_commit, LEDGER) or "") != (show(root, expected_parent, LEDGER) or "") + ledger_line(entry):
            problems.append(f"{label}: ledger commit is not an exact one-line append of this entry")
        stop_added = show(root, ledger_commit, STOP) is not None
        if stop_added != bool(entry.get("stop")):
            problems.append(f"{label}: STOP file does not match the recorded stop")
        if entry.get("stop") and wave != len(entries):
            problems.append(f"{label}: entries recorded after a stop")
        previous.append(entry)
        last_ledger = ledger_commit
    if index < len(commits) and not problems:
        problems.append(f"unledgered commits on the branch: {', '.join(c[:12] for c in commits[index:])}")
    if not entries:
        result = suite(root, head)
        if not result or any(v != "passed" for v in result.values()):
            problems.append(f"suite not green at the anchor: {summarize(result)}")
    report = {"ok": not problems, "anchor": anchor, "head": head, "waves": len(entries),
              "passed_waves": sum(e.get("verdict") == "pass" for e in entries), "problems": problems}
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-blackboard", help="validate the committed blackboard")
    sub.add_parser("prepare", help="create one worktree and branch per task")
    sub.add_parser("integrate", help="integrate worker commits in task order")
    sub.add_parser("status", help="print the integration result")
    finish = sub.add_parser("finish", help="validate the handoff and record the wave")
    finish.add_argument("--intent", required=True, help=f"one line, at most {INTENT_MAX} characters (self-report)")
    finish.add_argument("--trailer", action="append", default=[], help="commit trailer for the ledger commit")
    sub.add_parser("audit", help="re-derive every ledger entry from git")
    args = parser.parse_args(argv)
    commands = {"check-blackboard": cmd_check_blackboard, "prepare": cmd_prepare, "integrate": cmd_integrate,
                "status": cmd_status, "finish": cmd_finish, "audit": cmd_audit}
    try:
        return commands[args.command](args)
    except GateError as error:
        print(f"gate error: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

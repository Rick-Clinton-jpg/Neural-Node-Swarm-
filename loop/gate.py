#!/usr/bin/env python3
"""Deterministic gate for the Neural Node loop (see LOOP.md).

Every check here is mechanical. No model judges anything, and nothing a node
says about itself is trusted: the one free-text field a node supplies,
``self_report.intent``, is recorded as self-report and ignored by every check.

  python loop/gate.py run --intent "..."   evaluate HEAD as this run's work commit
  python loop/gate.py audit                re-derive every ledger entry from git

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

BRANCH = "neural-node-loop"
ATTEMPT_PREFIX = "neural-node-loop-attempt-"
GATE = "loop/gate.py"
LEDGER = "loop/ledger.jsonl"
HANDOFF = "loop/handoff.json"
BACKLOG = "loop/backlog.json"
STOP = "loop/STOP"
LEDGER_VERSION = "1.0"
INTENT_MAX = 200

# Paths as they exist at the anchor commit may never change afterwards.
FROZEN = ("LOOP.md", "README.md", "LICENSE", ".github", "tests", "schemas", "docs", "outputs", GATE, BACKLOG)
# Under these roots brand-new files may be added (never modified or deleted).
ADDABLE = ("tests/", "schemas/", "docs/", "outputs/")
# Files that could reconfigure or bypass pytest; never allowed to be added anywhere.
FORBIDDEN_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "sitecustomize.py", "usercustomize.py"}
# Only the gate writes these.
GATE_OWNED = (LEDGER, STOP)
# The only memory references a handoff may carry.
REF_PATTERNS = (
    r"ledger:latest",
    r"ledger:run:[1-9][0-9]*",
    r"backlog:item:[a-z0-9-]+",
    r"backlog:done",
    r"code:[A-Za-z0-9._/-]+",
)


class GateError(Exception):
    """Environment or usage problem. Nothing is recorded."""


# --------------------------------------------------------------------------- git


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def show(root: Path, sha: str, path: str) -> str | None:
    if subprocess.run(["git", "cat-file", "-e", f"{sha}:{path}"], cwd=root, capture_output=True).returncode != 0:
        return None
    return git(root, "show", f"{sha}:{path}")


def commit_exists(root: Path, sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, capture_output=True).returncode == 0


def parents_of(root: Path, sha: str) -> list[str]:
    return git(root, "rev-list", "--parents", "-n", "1", sha).split()[1:]


def anchors(root: Path, head: str) -> list[str]:
    """Commits on HEAD's history that added the gate. Exactly one is valid."""
    return git(root, "log", "--format=%H", "--diff-filter=A", head, "--", GATE).split()


def last_ledger_commit(root: Path, sha: str) -> str:
    return git(root, "log", "-1", "--format=%H", sha, "--", LEDGER).strip()


@contextmanager
def worktree(root: Path, sha: str):
    holder = Path(tempfile.mkdtemp(prefix="gate-wt-"))
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


def run_pytest(tree: Path, targets: list[str], *, keep_going: bool = True) -> dict[str, str]:
    """Return {test_id: passed|failed|skipped}. Collection errors appear as collect:: ids."""
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tree),
               f"--junitxml={junit}", "-o", "junit_family=xunit2"]
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
            previous = outcomes.get(test_id, "passed")
            outcomes[test_id] = max(previous, outcome, key=_RANK.__getitem__)
        return outcomes


def summarize(outcomes: dict[str, str]) -> dict[str, int]:
    values = list(outcomes.values())
    return {"total": len(values), "passed": values.count("passed"),
            "failed": values.count("failed"), "skipped": values.count("skipped")}


# ----------------------------------------------------------------------- checks


def load_backlog(root: Path, anchor: str) -> dict:
    text = show(root, anchor, BACKLOG)
    if text is None:
        raise GateError("backlog missing at anchor")
    return json.loads(text)


def frozen_violations(root: Path, anchor: str, parent: str, work: str) -> list[str]:
    found = set()
    for line in git(root, "diff", "--no-renames", "--name-status", anchor, work, "--", *FROZEN).splitlines():
        status, path = line.split("\t", 1)
        if status == "A" and path.startswith(ADDABLE) and PurePosixPath(path).name not in FORBIDDEN_NAMES:
            continue
        found.add(f"{status} {path}")
    for path in git(root, "diff", "--no-renames", "--name-only", "--diff-filter=A", anchor, work).splitlines():
        if PurePosixPath(path).name in FORBIDDEN_NAMES:
            found.add(f"forbidden-file {path}")
    for path in git(root, "diff", "--name-only", parent, work, "--", *GATE_OWNED).splitlines():
        found.add(f"gate-owned {path}")
    return sorted(found)


def pytest_config(root: Path, sha: str):
    text = show(root, sha, "pyproject.toml")
    if text is None:
        return None
    data = tomllib.loads(text)
    plugins = data.get("project", {}).get("entry-points", {}).get("pytest11")
    return {"tool.pytest": data.get("tool", {}).get("pytest"), "pytest11": plugins}


def anchor_verify(root: Path, anchor: str, data) -> dict:
    """Validate with the verifier and schema exactly as they were at the anchor commit."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "src" / "neural_node_swarm").mkdir(parents=True)
        (base / "schemas").mkdir()
        module_path = base / "src" / "neural_node_swarm" / "verifier.py"
        module_path.write_text(show(root, anchor, "src/neural_node_swarm/verifier.py") or "")
        (base / "schemas" / "node_output.schema.json").write_text(show(root, anchor, "schemas/node_output.schema.json") or "")
        spec = importlib.util.spec_from_file_location("_anchor_verifier", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look the module up by name
        try:
            spec.loader.exec_module(module)
            return module.verify_node_output(data)
        finally:
            sys.modules.pop(spec.name, None)


def handoff_item(root: Path, sha: str) -> str | None:
    text = show(root, sha, HANDOFF)
    try:
        refs = json.loads(text or "").get("required_memory_refs", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    items = [r for r in refs if isinstance(r, str) and (r.startswith("backlog:item:") or r == "backlog:done")]
    if len(items) != 1:
        return None
    return "done" if items[0] == "backlog:done" else items[0].removeprefix("backlog:item:")


def check_handoff(root: Path, anchor: str, sha: str, run: int, backlog: dict) -> list[str]:
    text = show(root, sha, HANDOFF)
    if text is None:
        return ["handoff missing"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"handoff is not JSON: {error.msg}"]
    result = anchor_verify(root, anchor, data)
    errors = [f"verifier:{c['name']}" for c in result["checks"] if not c["passed"]]
    if not isinstance(data, dict):
        return errors or ["handoff must be an object"]
    if data.get("step_id") != f"node-{run}:3":
        errors.append(f"step_id must be node-{run}:3")
    refs = data.get("required_memory_refs")
    refs = refs if isinstance(refs, list) else []
    for ref in refs:
        if not isinstance(ref, str) or not any(re.fullmatch(p, ref) for p in REF_PATTERNS):
            errors.append(f"unsupported ref {ref!r}")
        elif ref.startswith("ledger:run:") and int(ref.rsplit(":", 1)[1]) > run:
            errors.append(f"ref to a future run {ref!r}")
    if "ledger:latest" not in refs:
        errors.append("missing ledger:latest")
    item = handoff_item(root, sha)
    ids = [entry["id"] for entry in backlog["items"]]
    if item is None:
        errors.append("exactly one backlog ref required")
    elif item != "done" and item not in ids:
        errors.append(f"unknown backlog item {item!r}")
    return errors


def sequencing_error(current: str | None, following: str | None, backlog: dict) -> str | None:
    ids = [entry["id"] for entry in backlog["items"]]
    if current not in ids:
        return f"current item {current!r} is not an open backlog item"
    index = ids.index(current)
    allowed = {current, ids[index + 1] if index + 1 < len(ids) else "done"}
    if following not in allowed:
        return f"next item must be one of {sorted(allowed)}"
    return None


def evaluate(root: Path, parent: str, work: str, anchor: str, run: int, backlog: dict) -> dict:
    checks: dict = {}
    checks["single_parent"] = parents_of(root, work) == [parent]
    checks["frozen_violations"] = frozen_violations(root, anchor, parent, work)
    checks["pytest_config_unchanged"] = pytest_config(root, anchor) == pytest_config(root, work)
    with worktree(root, work) as tree_w, worktree(root, parent) as tree_p:
        probe_import(tree_w)
        probe_import(tree_p)
        suite_w = run_pytest(tree_w, ["tests"])
        suite_p = run_pytest(tree_p, ["tests"])
        added = git(root, "diff", "--no-renames", "--name-only", "--diff-filter=A", parent, work, "--", "tests").splitlines()
        for path in added:
            target = tree_p / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.run(["git", "show", f"{work}:{path}"], cwd=root, capture_output=True, check=True).stdout)
        new_files = [p for p in added if PurePosixPath(p).name.startswith("test_") and p.endswith(".py")]
        red = run_pytest(tree_p, new_files, keep_going=False) if new_files else {}
    checks["suite_work"] = summarize(suite_w)
    checks["suite_parent"] = summarize(suite_p)
    checks["suite_work_green"] = bool(suite_w) and all(v == "passed" for v in suite_w.values())
    parent_ids = {t for t in suite_p if not t.startswith("collect::")}
    checks["parent_tests_missing"] = sorted(parent_ids - set(suite_w))
    new_ids = sorted(set(suite_w) - set(suite_p))
    checks["new_tests"] = new_ids
    checks["red_on_parent"] = [t for t in new_ids if red.get(t, "absent") != "passed"]
    checks["red_green_ok"] = bool(checks["red_on_parent"]) and all(suite_w[t] == "passed" for t in new_ids)
    errors = check_handoff(root, anchor, work, run, backlog)
    item, following = handoff_item(root, parent), handoff_item(root, work)
    problem = sequencing_error(item, following, backlog)
    checks["handoff_errors"] = errors + ([problem] if problem else [])
    checks["item"], checks["next_item"] = item, following
    return checks


def verdict_of(checks: dict) -> str:
    ok = (checks["single_parent"] and not checks["frozen_violations"] and checks["pytest_config_unchanged"]
          and checks["suite_work_green"] and not checks["parent_tests_missing"] and checks["red_green_ok"]
          and not checks["handoff_errors"])
    return "pass" if ok else "fail"


def stop_reason(verdict: str, checks: dict, run: int, previous: list[dict], max_runs: int) -> str | None:
    if checks["frozen_violations"]:
        return "frozen_violation"
    if verdict == "fail" and previous and previous[-1]["verdict"] == "fail":
        return "circuit_breaker"
    if verdict == "pass" and checks["next_item"] == "done":
        return "backlog_complete"
    if run >= max_runs:
        return "max_runs"
    return None


def build_entry(run: int, parent: str, work: str, anchor: str, checks: dict, previous: list[dict],
                backlog: dict, intent: str) -> dict:
    verdict = verdict_of(checks)
    return {
        "ledger_version": LEDGER_VERSION,
        "run": run,
        "node_id": f"node-{run}",
        "anchor": anchor,
        "parent": parent,
        "work": work,
        "checks": checks,
        "verdict": verdict,
        "stop": stop_reason(verdict, checks, run, previous, backlog["max_runs"]),
        "self_report": {"intent": intent, "item_complete_claim": checks["next_item"] != checks["item"]},
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


def single_anchor(root: Path, head: str) -> str:
    found = anchors(root, head)
    if len(found) != 1:
        raise GateError(f"expected exactly one commit adding {GATE}, found {len(found)}")
    return found[0]


def cmd_run(args) -> int:
    intent = " ".join(args.intent.split())
    if not intent or len(intent) > INTENT_MAX:
        raise GateError(f"--intent must be 1-{INTENT_MAX} characters")
    root = toplevel()
    if git(root, "branch", "--show-current").strip() != BRANCH:
        raise GateError(f"must be on branch {BRANCH}")
    if git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise GateError("working tree must be clean: commit the work first")
    head = git(root, "rev-parse", "HEAD").strip()
    anchor = single_anchor(root, head)
    if show(root, head, STOP) is not None:
        print(f"loop stopped: {show(root, head, STOP).strip()}")
        return 4
    backlog = load_backlog(root, anchor)
    previous = read_ledger(root, head)
    run = len(previous) + 1
    if run > backlog["max_runs"]:
        raise GateError("run cap reached")
    parents = parents_of(root, head)
    if len(parents) != 1 or parents[0] != last_ledger_commit(root, parents[0]) or head == anchor:
        raise GateError("HEAD must be exactly one work commit on top of the last ledger commit")
    parent, work = parents[0], head
    checks = evaluate(root, parent, work, anchor, run, backlog)
    entry = build_entry(run, parent, work, anchor, checks, previous, backlog, intent)
    if entry["verdict"] == "fail":
        git(root, "branch", "-f", f"{ATTEMPT_PREFIX}{run}", work)
        git(root, "reset", "--hard", parent)
    with open(root / LEDGER, "a", encoding="utf-8") as stream:
        stream.write(ledger_line(entry))
    paths = [LEDGER]
    if entry["stop"]:
        (root / STOP).write_text(entry["stop"] + "\n", encoding="utf-8")
        paths.append(STOP)
    git(root, "add", "--", *paths)
    message = f"ledger: run {run} {entry['verdict']}" + (f" (stop: {entry['stop']})" if entry["stop"] else "")
    if args.trailer:
        message += "\n\n" + "\n".join(args.trailer)
    git(root, "commit", "-q", "-m", message)
    print(json.dumps(entry, indent=2, sort_keys=True))
    if entry["verdict"] == "fail":
        print(f"\nfailed attempt kept on branch {ATTEMPT_PREFIX}{run}; push it together with {BRANCH}")
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
        for line in git(root, "diff", "--no-renames", "--name-status", base[0], anchor).splitlines():
            if not line.startswith("A\t"):
                problems.append(f"anchor changes an existing file: {line}")
    if (show(root, anchor, LEDGER) or "") != "":
        problems.append("ledger must start empty at the anchor")
    if show(root, anchor, STOP) is not None:
        problems.append("anchor must not contain STOP")
    errors = check_handoff(root, anchor, anchor, 0, backlog)
    if errors or handoff_item(root, anchor) != backlog["items"][0]["id"]:
        problems.append(f"anchor handoff invalid: {errors or 'must start at the first backlog item'}")
    if git(root, "rev-list", "--merges", f"{anchor}..{head}").strip():
        problems.append("merge commits are not allowed on the loop branch")
    commits = git(root, "rev-list", "--first-parent", "--reverse", f"{anchor}..{head}").split()
    try:
        entries = read_ledger(root, head)
    except json.JSONDecodeError:
        entries = []
        problems.append("ledger contains a line that is not JSON")

    def is_ledger_commit(sha: str) -> bool:
        changed = set(git(root, "diff", "--name-only", f"{sha}^", sha).split())
        return bool(changed) and changed <= set(GATE_OWNED)

    # Append-only, checked independently of what the entries claim.
    for sha in commits:
        before = show(root, f"{sha}^", LEDGER) or ""
        after = show(root, sha, LEDGER) or ""
        if before == after:
            continue
        added = after[len(before):] if after.startswith(before) else None
        if added is None or added.count("\n") != 1 or not added.endswith("\n"):
            problems.append(f"commit {sha[:12]}: ledger change is not an exact one-line append")

    index, previous, last_ledger = 0, [], anchor
    for run, entry in enumerate(entries, start=1):
        label = f"run {run}"
        if entry.get("run") != run:
            problems.append(f"{label}: entry numbered {entry.get('run')!r}")
            break
        if entry.get("verdict") == "pass":
            if index + 1 >= len(commits) or commits[index] != entry.get("work"):
                problems.append(f"{label}: work commit is not next on the branch")
                break
            index += 1
        if index >= len(commits) or not is_ledger_commit(commits[index]):
            problems.append(f"{label}: ledger commit missing or touches other files")
            break
        ledger_commit = commits[index]
        index += 1
        before = show(root, f"{ledger_commit}^", LEDGER) or ""
        after = show(root, ledger_commit, LEDGER) or ""
        if after != before + ledger_line(entry):
            problems.append(f"{label}: ledger commit is not an exact one-line append of this entry")
        expected_stop_add = {STOP} if entry.get("stop") else set()
        added = set(git(root, "diff", "--name-only", "--diff-filter=A", f"{ledger_commit}^", ledger_commit).split())
        if added - {LEDGER} != expected_stop_add:
            problems.append(f"{label}: STOP file does not match the recorded stop")
        if entry.get("parent") != last_ledger:
            problems.append(f"{label}: parent is not the previous ledger commit")
        work = entry.get("work", "")
        if not commit_exists(root, work):
            problems.append(f"{label}: work commit {work} is not available (fetch attempt branches)")
            break
        checks = evaluate(root, entry["parent"], work, anchor, run, backlog)
        intent = entry.get("self_report", {}).get("intent", "")
        expected = build_entry(run, entry["parent"], work, anchor, checks, previous, backlog, intent)
        if expected != entry:
            differing = sorted(k for k in set(expected) | set(entry) if expected.get(k) != entry.get(k))
            problems.append(f"{label}: recorded entry does not match re-measurement ({', '.join(differing)})")
        if entry.get("stop") and run != len(entries):
            problems.append(f"{label}: entries recorded after a stop")
        previous.append(entry)
        last_ledger = ledger_commit
    if index < len(commits) and not problems:
        problems.append(f"unledgered commits on the branch: {', '.join(c[:12] for c in commits[index:])}")
    stopped = show(root, head, STOP) is not None
    if stopped != bool(entries and entries[-1].get("stop")):
        problems.append("STOP state does not match the ledger")
    if not entries:
        with worktree(root, head) as tree:
            probe_import(tree)
            suite = run_pytest(tree, ["tests"])
        if not suite or any(v != "passed" for v in suite.values()):
            problems.append(f"suite not green at the anchor: {summarize(suite)}")
    report = {"ok": not problems, "anchor": anchor, "head": head, "entries": len(entries),
              "passed_runs": sum(e.get("verdict") == "pass" for e in entries), "problems": problems}
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="evaluate HEAD as this run's work commit and record the result")
    run.add_argument("--intent", required=True, help=f"one line, at most {INTENT_MAX} characters (self-report)")
    run.add_argument("--trailer", action="append", default=[], help="commit trailer for the ledger commit")
    sub.add_parser("audit", help="re-derive every ledger entry from git and compare")
    args = parser.parse_args(argv)
    try:
        return cmd_run(args) if args.command == "run" else cmd_audit(args)
    except GateError as error:
        print(f"gate error: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

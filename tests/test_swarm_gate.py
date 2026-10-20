"""The swarm gate must integrate parallel work honestly and catch the ways a wave could fake progress.

Each test builds a small synthetic repository from this checkout's runtime, adds
the gate at an anchor commit, simulates a wave (blackboard, parallel worker
commits in their own worktrees, handoff), and checks what the gate records.
Nothing here calls a model or the network.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "neural-node-swarm-loop"
# A fixed backlog, so these tests measure the gate whatever backlog the repository holds.
BACKLOG = {
    "backlog_version": "1.0", "max_waves": 5, "max_tasks_per_wave": 3,
    "items": [
        {"id": "alpha", "title": "Alpha", "acceptance": "a", "depends_on": []},
        {"id": "beta", "title": "Beta", "acceptance": "b", "depends_on": ["alpha"]},
        {"id": "gamma", "title": "Gamma", "acceptance": "c", "depends_on": []},
        {"id": "delta", "title": "Delta", "acceptance": "d", "depends_on": []},
    ],
}
ITEMS = [item["id"] for item in BACKLOG["items"]]
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTEST"))},
    "GIT_AUTHOR_NAME": "gate-test", "GIT_AUTHOR_EMAIL": "gate-test@example.invalid",
    "GIT_COMMITTER_NAME": "gate-test", "GIT_COMMITTER_EMAIL": "gate-test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
BASE_TEST = (
    "from neural_node_swarm.verifier import verify_node_output\n\n"
    "def test_base():\n"
    "    assert verify_node_output({})['status'] == 'rejected'\n"
)


def module(name, value):
    """A worker change: a new module plus a test that fails until the module exists."""
    return {
        f"src/neural_node_swarm/{name}.py": f"VALUE = {value}\n",
        f"tests/test_{name}.py": f"from neural_node_swarm.{name} import VALUE\n\ndef test_{name}():\n    assert VALUE == {value}\n",
    }


TAUTOLOGY = {"tests/test_tautology.py": "def test_true():\n    assert True\n"}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, capture_output=True, text=True, check=True).stdout


def node(step_id, refs, objective="do it"):
    return {"schema_version": "1.0", "step_id": step_id, "objective": objective,
            "success_criteria": ["gate passes"], "required_memory_refs": refs}


def make_repo(tmp_path, backlog=None):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    shutil.copytree(ROOT / "schemas", repo / "schemas")
    shutil.copy(ROOT / "pyproject.toml", repo / "pyproject.toml")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_base.py").write_text(BASE_TEST)
    git(repo, "init", "-q", "-b", BRANCH)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    backlog = backlog or BACKLOG
    (repo / "swarm").mkdir()
    shutil.copy(ROOT / "swarm" / "gate.py", repo / "swarm" / "gate.py")
    (repo / "swarm" / "backlog.json").write_text(json.dumps(backlog))
    (repo / "swarm" / "ledger.jsonl").write_text("")
    refs = ["ledger:latest"] + [f"backlog:item:{item['id']}" for item in backlog["items"]]
    (repo / "swarm" / "handoff.json").write_text(json.dumps(node("wave-0:handoff", refs)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "anchor")
    return repo


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def gate(repo, *args):
    return subprocess.run([sys.executable, "swarm/gate.py", *args], cwd=repo, env=ENV,
                          capture_output=True, text=True, timeout=600)


def post(repo, wave, items, **extra):
    tasks = [node(f"wave-{wave}:task-{k}", [f"backlog:item:{item}"]) for k, item in enumerate(items, start=1)]
    for k, fields in extra.items():
        tasks[int(k.removeprefix("t")) - 1].update(fields)
    (repo / "swarm" / "blackboard.json").write_text(json.dumps({"wave": wave, "tasks": tasks}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"wave-{wave}: blackboard")


def work(path, files=(), delete=()):
    for name, text in dict(files).items():
        target = Path(path) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for name in delete:
        (Path(path) / name).unlink()
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "worker")


def run_wave(repo, wave, items, changes, *, close=(), handoff=None, open_items=None):
    """Post, prepare, let each worker commit its change, integrate and hand off. Returns finish."""
    post(repo, wave, items)
    prepared = gate(repo, "prepare")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    for task, change in zip(json.loads(prepared.stdout), changes):
        if change is not None:
            work(task["path"], change)
    assert gate(repo, "integrate").returncode == 0
    if handoff is None:
        remaining = open_items if open_items is not None else [i for i in current_open(repo) if i not in close]
        refs = ["ledger:latest"] + ([f"backlog:item:{i}" for i in remaining] or ["backlog:done"])
        handoff = node(f"wave-{wave}:handoff", refs)
    (repo / "swarm" / "handoff.json").write_text(json.dumps(handoff))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"wave-{wave}: handoff")
    return gate(repo, "finish", "--intent", "test wave")


def current_open(repo):
    refs = json.loads((repo / "swarm" / "handoff.json").read_text())["required_memory_refs"]
    return [r.removeprefix("backlog:item:") for r in refs if r.startswith("backlog:item:")]


def entries(repo):
    return [json.loads(line) for line in (repo / "swarm" / "ledger.jsonl").read_text().splitlines()]


def audit_ok(repo):
    result = gate(repo, "audit")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_parallel_workers_are_integrated_in_order_and_audited(repo):
    result = run_wave(repo, 1, ITEMS[0:1] + ITEMS[2:4], [module("one", 1), module("two", 2), module("three", 3)],
                      close=[ITEMS[0]])
    assert result.returncode == 0, result.stdout + result.stderr
    entry = entries(repo)[-1]
    assert [t["outcome"] for t in entry["tasks"]] == ["accepted"] * 3
    assert entry["self_report"]["items_closed_claim"] == [ITEMS[0]]
    assert ITEMS[0] not in entry["open_after"]
    history = git(repo, "log", "--format=%s", "-6").splitlines()
    assert history[0].startswith("ledger: wave 1 pass") and history[1] == "wave-1: handoff"
    assert [line.split(" (")[0] for line in history[2:5]] == [f"swarm: integrate wave 1 task {k}" for k in (3, 2, 1)]
    assert history[5] == "wave-1: blackboard"
    assert "push: git push origin neural-node-swarm-loop neural-node-swarm-loop-w1-t1" in result.stdout
    assert audit_ok(repo)["passed_waves"] == 1


def test_conflicting_workers_are_rejected_without_blocking_the_rest(repo):
    clash = {"src/neural_node_swarm/shared.py": "X = 2\n", "tests/test_shared_b.py":
             "from neural_node_swarm.shared import X\n\ndef test_b():\n    assert X == 2\n"}
    first = {"src/neural_node_swarm/shared.py": "X = 1\n", "tests/test_shared_a.py":
             "from neural_node_swarm.shared import X\n\ndef test_a():\n    assert X == 1\n"}
    result = run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [first, clash])
    assert result.returncode == 0
    assert [t["outcome"] for t in entries(repo)[-1]["tasks"]] == ["accepted", "rejected_conflict"]
    audit_ok(repo)


def test_work_that_passes_alone_but_breaks_once_combined_is_rejected(repo):
    memory = (repo / "src" / "neural_node_swarm" / "memory.py").read_text()
    appended = {"src/neural_node_swarm/memory.py": memory + "\n\ndef tag():\n    return 'a'\n",
                "tests/test_tag_a.py": "from neural_node_swarm.memory import tag\n\ndef test_a():\n    assert tag() == 'a'\n"}
    prepended = {"src/neural_node_swarm/memory.py": "def tag():\n    return 'b'\n\n\n" + memory,
                 "tests/test_tag_b.py": "from neural_node_swarm.memory import tag\n\ndef test_b():\n    assert tag() == 'b'\n"}
    result = run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [appended, prepended])
    assert result.returncode == 0
    second = entries(repo)[-1]["tasks"][1]
    assert second["outcome"] == "rejected_checks"
    assert second["checks"]["suite_green"] is False
    audit_ok(repo)


def test_a_test_that_already_passes_is_not_progress(repo):
    result = run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [TAUTOLOGY, module("two", 2)])
    assert result.returncode == 0
    tasks = entries(repo)[-1]["tasks"]
    assert tasks[0]["outcome"] == "rejected_checks" and tasks[0]["checks"]["red_on_tip"] == []
    assert tasks[1]["outcome"] == "accepted"


def test_a_wave_with_nothing_accepted_fails_and_is_kept(repo):
    result = run_wave(repo, 1, [ITEMS[0]], [TAUTOLOGY])
    assert result.returncode == 1
    entry = entries(repo)[-1]
    assert entry["verdict"] == "fail"
    assert git(repo, "rev-parse", f"{BRANCH}-attempt-1").strip() == entry["handoff_commit"]
    assert git(repo, "log", "--format=%s", "-2").splitlines()[1] == "anchor"
    audit_ok(repo)


def test_missing_worker_is_recorded_and_the_rest_proceeds(repo):
    result = run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [None, module("two", 2)])
    assert result.returncode == 0
    assert [t["outcome"] for t in entries(repo)[-1]["tasks"]] == ["missing", "accepted"]
    audit_ok(repo)


@pytest.mark.parametrize("change,expected", [
    ({"tests/test_base.py": "def test_base():\n    assert True\n"}, "M tests/test_base.py"),
    ({"tests/conftest.py": ""}, "forbidden-file tests/conftest.py"),
    ({"swarm/handoff.json": "{}"}, "swarm-owned swarm/handoff.json"),
])
def test_rule_breaking_work_stops_the_loop(repo, change, expected):
    result = run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [{**module("one", 1), **change}, module("two", 2)])
    assert result.returncode == 1
    entry = entries(repo)[-1]
    assert expected in entry["tasks"][0]["checks"]["frozen_violations"]
    assert entry["stop"] == "frozen_violation"
    assert gate(repo, "integrate").returncode == 4


def test_blackboard_must_respect_dependencies_and_the_schema(repo):
    post(repo, 1, [ITEMS[1]])
    result = gate(repo, "check-blackboard")
    assert result.returncode == 1
    assert f"depends on open item '{ITEMS[0]}'" in result.stdout
    assert gate(repo, "prepare").returncode == 1


def test_blackboard_side_channel_is_rejected(repo):
    post(repo, 1, [ITEMS[0]], t1={"notes": "reasoning for the worker"})
    result = gate(repo, "check-blackboard")
    assert result.returncode == 1
    assert "task 1: verifier:closed_schema" in result.stdout


def test_closing_an_item_without_accepted_work_fails_the_wave(repo):
    result = run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [TAUTOLOGY, module("two", 2)], close=[ITEMS[0]])
    assert result.returncode == 1
    errors = entries(repo)[-1]["handoff_errors"]
    assert f"cannot close '{ITEMS[0]}': no task on it was accepted this wave" in errors


def test_two_failed_waves_trip_the_circuit_breaker(repo):
    assert run_wave(repo, 1, [ITEMS[0]], [TAUTOLOGY]).returncode == 1
    assert run_wave(repo, 2, [ITEMS[0]], [TAUTOLOGY]).returncode == 1
    assert [e["stop"] for e in entries(repo)] == [None, "circuit_breaker"]
    assert gate(repo, "check-blackboard").returncode == 4
    audit_ok(repo)


def test_closing_the_last_item_completes_the_backlog(tmp_path):
    backlog = {**BACKLOG, "items": BACKLOG["items"][:1]}
    repo = make_repo(tmp_path, backlog)
    result = run_wave(repo, 1, [ITEMS[0]], [module("one", 1)], close=[ITEMS[0]])
    assert result.returncode == 0
    assert entries(repo)[-1]["stop"] == "backlog_complete"
    assert (repo / "swarm" / "STOP").read_text().strip() == "backlog_complete"
    audit_ok(repo)


def test_finish_refuses_integrated_work_altered_by_the_host(repo):
    post(repo, 1, [ITEMS[0]])
    task = json.loads(gate(repo, "prepare").stdout)[0]
    work(task["path"], module("one", 1))
    gate(repo, "integrate")
    (repo / "src" / "neural_node_swarm" / "one.py").write_text("VALUE = 99\n")
    git(repo, "commit", "-q", "-a", "--amend", "--no-edit")
    (repo / "swarm" / "handoff.json").write_text(json.dumps(node("wave-1:handoff", ["ledger:latest", f"backlog:item:{ITEMS[0]}"])))
    git(repo, "commit", "-q", "-am", "wave-1: handoff")
    result = gate(repo, "finish", "--intent", "x")
    assert result.returncode == 3
    assert "HEAD must be one commit on the integration result" in result.stderr


def test_audit_catches_a_forged_outcome(repo):
    assert run_wave(repo, 1, [ITEMS[0], ITEMS[2]], [TAUTOLOGY, module("two", 2)]).returncode == 0
    honest = entries(repo)[-1]
    git(repo, "reset", "-q", "--hard", "HEAD^")
    forged = json.loads(json.dumps(honest))
    forged["tasks"][0]["checks"]["red_on_tip"] = ["tests.test_tautology::test_true"]
    with open(repo / "swarm" / "ledger.jsonl", "a") as stream:
        stream.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
    git(repo, "commit", "-q", "-am", "ledger: wave 1 pass")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "does not match re-measurement (tasks)" in result.stdout


def test_audit_catches_an_unledgered_commit(repo):
    assert run_wave(repo, 1, [ITEMS[0]], [module("one", 1)]).returncode == 0
    (repo / "src" / "neural_node_swarm" / "one.py").write_text("VALUE = 2\n")
    git(repo, "commit", "-q", "-am", "sneaky change")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "unledgered commits" in result.stdout

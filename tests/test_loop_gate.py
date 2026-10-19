"""The loop gate must catch the ways a node could fake progress.

Each test builds a small synthetic repository from this checkout's runtime,
adds the gate at an anchor commit, simulates node runs, and checks what the gate
records. Nothing here calls a model or the network.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRST, SECOND = [item["id"] for item in json.loads((ROOT / "loop" / "backlog.json").read_text())["items"][:2]]
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
HONEST = {
    "src/neural_node_swarm/extra.py": "def double(x):\n    return 2 * x\n",
    "tests/test_extra.py": "from neural_node_swarm.extra import double\n\ndef test_double():\n    assert double(2) == 4\n",
}
TAUTOLOGY = {"tests/test_tautology.py": "def test_true():\n    assert True\n"}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, capture_output=True, text=True, check=True).stdout


def handoff(run, item, **extra):
    ref = "backlog:done" if item == "done" else f"backlog:item:{item}"
    return {"schema_version": "1.0", "step_id": f"node-{run}:3", "objective": f"continue {item}",
            "success_criteria": ["gate passes"], "required_memory_refs": ["ledger:latest", ref], **extra}


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    shutil.copytree(ROOT / "schemas", repo / "schemas")
    shutil.copy(ROOT / "pyproject.toml", repo / "pyproject.toml")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_base.py").write_text(BASE_TEST)
    git(repo, "init", "-q", "-b", "neural-node-loop")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "loop").mkdir()
    shutil.copy(ROOT / "loop" / "gate.py", repo / "loop" / "gate.py")
    shutil.copy(ROOT / "loop" / "backlog.json", repo / "loop" / "backlog.json")
    (repo / "loop" / "ledger.jsonl").write_text("")
    (repo / "loop" / "handoff.json").write_text(json.dumps(handoff(0, FIRST)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "anchor")
    return repo


def work(repo, run, files=(), *, item=FIRST, handoff_data=None, delete=()):
    for path, text in dict(files).items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for path in delete:
        (repo / path).unlink()
    data = handoff_data if handoff_data is not None else handoff(run, item)
    (repo / "loop" / "handoff.json").write_text(json.dumps(data))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"node-{run}")


def gate(repo, *args):
    return subprocess.run([sys.executable, "loop/gate.py", *args], cwd=repo, env=ENV,
                          capture_output=True, text=True, timeout=600)


def run_gate(repo):
    return gate(repo, "run", "--intent", "test run")


def entries(repo):
    return [json.loads(line) for line in (repo / "loop" / "ledger.jsonl").read_text().splitlines()]


def test_honest_increment_passes_and_audits(repo):
    work(repo, 1, HONEST)
    result = run_gate(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    entry = entries(repo)[-1]
    assert entry["verdict"] == "pass"
    assert entry["checks"]["red_on_parent"] == ["tests.test_extra::test_double"]
    assert entry["self_report"] == {"intent": "test run", "item_complete_claim": False}
    audit = gate(repo, "audit")
    assert audit.returncode == 0, audit.stdout + audit.stderr


def test_test_that_already_passes_on_parent_is_not_progress(repo):
    work(repo, 1, TAUTOLOGY)
    assert run_gate(repo).returncode == 1
    entry = entries(repo)[-1]
    assert entry["verdict"] == "fail"
    assert entry["checks"]["red_on_parent"] == []
    assert not (repo / "tests" / "test_tautology.py").exists()
    assert git(repo, "rev-parse", "neural-node-loop-attempt-1").strip() == entry["work"]
    assert gate(repo, "audit").returncode == 0


def test_editing_an_existing_test_is_a_frozen_violation_and_stops(repo):
    work(repo, 1, {**HONEST, "tests/test_base.py": "def test_base():\n    assert True\n"})
    assert run_gate(repo).returncode == 1
    entry = entries(repo)[-1]
    assert "M tests/test_base.py" in entry["checks"]["frozen_violations"]
    assert entry["stop"] == "frozen_violation"
    assert (repo / "loop" / "STOP").read_text().strip() == "frozen_violation"
    assert run_gate(repo).returncode == 4


def test_deleting_an_existing_test_fails(repo):
    work(repo, 1, HONEST, delete=["tests/test_base.py"])
    assert run_gate(repo).returncode == 1
    checks = entries(repo)[-1]["checks"]
    assert "D tests/test_base.py" in checks["frozen_violations"]
    assert checks["parent_tests_missing"] == ["tests.test_base::test_base"]


def test_conftest_and_pytest_config_changes_are_rejected(repo):
    pyproject = (repo / "pyproject.toml").read_text().replace('pythonpath = ["src"]', 'pythonpath = ["src"]\naddopts = "-k base"')
    work(repo, 1, {**HONEST, "tests/conftest.py": "", "pyproject.toml": pyproject})
    assert run_gate(repo).returncode == 1
    checks = entries(repo)[-1]["checks"]
    assert "forbidden-file tests/conftest.py" in checks["frozen_violations"]
    assert checks["pytest_config_unchanged"] is False


def test_handoff_side_channel_is_rejected(repo):
    work(repo, 1, HONEST, handoff_data=handoff(1, FIRST, notes="hidden reasoning for the successor"))
    assert run_gate(repo).returncode == 1
    assert "verifier:closed_schema" in entries(repo)[-1]["checks"]["handoff_errors"]


def test_skipping_ahead_in_the_backlog_is_rejected(repo):
    third = json.loads((ROOT / "loop" / "backlog.json").read_text())["items"][2]["id"]
    work(repo, 1, HONEST, item=third)
    assert run_gate(repo).returncode == 1
    errors = entries(repo)[-1]["checks"]["handoff_errors"]
    assert any(error.startswith("next item must be one of") for error in errors)


def test_moving_to_the_next_item_is_recorded_as_a_claim(repo):
    work(repo, 1, HONEST, item=SECOND)
    assert run_gate(repo).returncode == 0
    entry = entries(repo)[-1]
    assert (entry["checks"]["item"], entry["checks"]["next_item"]) == (FIRST, SECOND)
    assert entry["self_report"]["item_complete_claim"] is True


def test_two_failures_in_a_row_trip_the_circuit_breaker(repo):
    work(repo, 1, TAUTOLOGY)
    assert run_gate(repo).returncode == 1
    work(repo, 2, TAUTOLOGY)
    assert run_gate(repo).returncode == 1
    assert [e["stop"] for e in entries(repo)] == [None, "circuit_breaker"]
    assert run_gate(repo).returncode == 4
    assert len(entries(repo)) == 2
    assert gate(repo, "audit").returncode == 0


def test_uncommitted_work_records_nothing(repo):
    (repo / "src" / "neural_node_swarm" / "extra.py").write_text("x = 1\n")
    result = run_gate(repo)
    assert result.returncode == 3
    assert entries(repo) == []


def test_audit_catches_a_rewritten_ledger_entry(repo):
    work(repo, 1, TAUTOLOGY)
    run_gate(repo)
    ledger = repo / "loop" / "ledger.jsonl"
    ledger.write_text(ledger.read_text().replace('"verdict":"fail"', '"verdict":"pass"'))
    git(repo, "commit", "-q", "-am", "edit ledger")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "not an exact one-line append" in result.stdout


def test_audit_catches_a_fabricated_pass(repo):
    work(repo, 1, HONEST)
    run_gate(repo)
    forged = {**entries(repo)[-1], "run": 2, "node_id": "node-2"}
    with open(repo / "loop" / "ledger.jsonl", "a") as stream:
        stream.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
    git(repo, "commit", "-q", "-am", "forged entry")
    assert gate(repo, "audit").returncode == 1


def test_audit_catches_a_misreported_measurement(repo):
    work(repo, 1, HONEST)
    assert run_gate(repo).returncode == 0
    honest = entries(repo)[-1]
    git(repo, "reset", "-q", "--hard", "HEAD^")
    lie ={**honest, "checks": {**honest["checks"], "suite_work": {**honest["checks"]["suite_work"], "total": 99}}}
    with open(repo / "loop" / "ledger.jsonl", "a") as stream:
        stream.write(json.dumps(lie, sort_keys=True, separators=(",", ":")) + "\n")
    git(repo, "commit", "-q", "-am", "ledger: run 1 pass")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "does not match re-measurement (checks)" in result.stdout


def test_audit_catches_an_unledgered_commit(repo):
    work(repo, 1, HONEST)
    run_gate(repo)
    (repo / "src" / "neural_node_swarm" / "extra.py").write_text("def double(x):\n    return x + x\n")
    git(repo, "commit", "-q", "-am", "sneaky change")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "unledgered commits" in result.stdout

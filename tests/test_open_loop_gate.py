"""The open-loop gate must catch the ways a node could fake progress or leave its lane.

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
    "src/neural_node_swarm/lab/__init__.py": "",
    "src/neural_node_swarm/lab/double.py": "def double(x):\n    return 2 * x\n",
    "tests/test_lab_double.py": "from neural_node_swarm.lab.double import double\n\ndef test_double():\n    assert double(2) == 4\n",
}
TAUTOLOGY = {"tests/test_lab_tautology.py": "def test_true():\n    assert True\n"}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, capture_output=True, text=True, check=True).stdout


def handoff(run, objective="continue", refs=(), **extra):
    return {"schema_version": "1.0", "step_id": f"node-{run}:3", "objective": objective,
            "success_criteria": ["gate passes"], "required_memory_refs": ["ledger:latest", *refs], **extra}


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    # Only the runtime as it was at the anchor: whatever the loop has added under lab/ stays out,
    # so these tests keep measuring the gate rather than the loop's own work.
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "lab"))
    shutil.copytree(ROOT / "schemas", repo / "schemas", ignore=shutil.ignore_patterns("lab"))
    shutil.copy(ROOT / "pyproject.toml", repo / "pyproject.toml")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_base.py").write_text(BASE_TEST)
    git(repo, "init", "-q", "-b", "neural-node-open-loop")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "open-loop").mkdir()
    shutil.copy(ROOT / "open-loop" / "gate.py", repo / "open-loop" / "gate.py")
    (repo / "open-loop" / "ledger.jsonl").write_text("")
    (repo / "open-loop" / "handoff.json").write_text(json.dumps(handoff(0, "seed objective")))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "anchor")
    return repo


def work(repo, run, files=(), *, handoff_data=None, delete=()):
    for path, text in dict(files).items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for path in delete:
        (repo / path).unlink()
    data = handoff_data if handoff_data is not None else handoff(run, f"objective from node {run}")
    (repo / "open-loop" / "handoff.json").write_text(json.dumps(data))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"node-{run}")


def gate(repo, *args):
    return subprocess.run([sys.executable, "open-loop/gate.py", *args], cwd=repo, env=ENV,
                          capture_output=True, text=True, timeout=600)


def run_gate(repo):
    return gate(repo, "run", "--intent", "test run")


def entries(repo):
    return [json.loads(line) for line in (repo / "open-loop" / "ledger.jsonl").read_text().splitlines()]


def test_honest_lab_increment_passes_and_records_the_trajectory(repo):
    work(repo, 1, HONEST)
    result = run_gate(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    entry = entries(repo)[-1]
    assert entry["verdict"] == "pass"
    assert entry["checks"]["red_on_parent"] == ["tests.test_lab_double::test_double"]
    assert (entry["objective_in"], entry["objective_out"]) == ("seed objective", "objective from node 1")
    assert entry["checks"]["files_added"] == sorted(HONEST)
    audit = gate(repo, "audit")
    assert audit.returncode == 0, audit.stdout + audit.stderr


def test_test_that_already_passes_on_parent_is_not_progress(repo):
    work(repo, 1, TAUTOLOGY)
    assert run_gate(repo).returncode == 1
    entry = entries(repo)[-1]
    assert entry["checks"]["red_on_parent"] == []
    assert not (repo / "tests" / "test_lab_tautology.py").exists()
    assert git(repo, "rev-parse", "neural-node-open-loop-attempt-1").strip() == entry["work"]
    assert gate(repo, "audit").returncode == 0


@pytest.mark.parametrize("path,text,expected", [
    ("src/neural_node_swarm/verifier.py", "def verify_node_output(o):\n    return {}\n", "M src/neural_node_swarm/verifier.py"),
    ("src/neural_node_swarm/extra.py", "x = 1\n", "A src/neural_node_swarm/extra.py"),
    ("src/jsonschema/__init__.py", "", "A src/jsonschema/__init__.py"),
    ("tests/lab/conftest.py", "", "A tests/lab/conftest.py"),
    ("tests/test_extra.py", "def test_x():\n    assert True\n", "A tests/test_extra.py"),
])
def test_leaving_the_lab_is_a_scope_violation_and_stops_the_loop(repo, path, text, expected):
    work(repo, 1, {**HONEST, path: text})
    assert run_gate(repo).returncode == 1
    entry = entries(repo)[-1]
    assert expected in entry["checks"]["scope_violations"]
    assert entry["stop"] == "scope_violation"
    assert run_gate(repo).returncode == 4


def test_deleting_an_existing_test_is_a_scope_violation(repo):
    work(repo, 1, HONEST, delete=["tests/test_base.py"])
    assert run_gate(repo).returncode == 1
    checks = entries(repo)[-1]["checks"]
    assert "D tests/test_base.py" in checks["scope_violations"]
    assert checks["parent_tests_missing"] == ["tests.test_base::test_base"]


@pytest.mark.parametrize("source,needle", [
    ("import socket\n", "import socket"),
    ("from urllib.request import urlopen\n", "import urllib.request"),
    ("import requests\n", "import requests"),
    ("import importlib\nmod = importlib.import_module('soc' + 'ket')\n", "dynamic import_module"),
    ("eval('1')\n", "dynamic eval"),
])
def test_flagged_imports_fail_the_run(repo, source, needle):
    work(repo, 1, {**HONEST, "src/neural_node_swarm/lab/net.py": source})
    assert run_gate(repo).returncode == 1
    violations = entries(repo)[-1]["checks"]["import_violations"]
    assert any(needle in v for v in violations), violations


def test_handoff_side_channel_is_rejected(repo):
    work(repo, 1, HONEST, handoff_data=handoff(1, notes="hidden reasoning for the successor"))
    assert run_gate(repo).returncode == 1
    assert "verifier:closed_schema" in entries(repo)[-1]["checks"]["handoff_errors"]


def test_handoff_pointing_at_a_missing_file_is_rejected(repo):
    work(repo, 1, HONEST, handoff_data=handoff(1, refs=["code:src/neural_node_swarm/lab/nothing.py"]))
    assert run_gate(repo).returncode == 1
    errors = entries(repo)[-1]["checks"]["handoff_errors"]
    assert any("missing file" in error for error in errors)


def test_declaring_done_stops_the_loop(repo):
    work(repo, 1, HONEST, handoff_data=handoff(1, refs=["loop:done"]))
    assert run_gate(repo).returncode == 0
    assert entries(repo)[-1]["stop"] == "declared_done"
    assert run_gate(repo).returncode == 4


def test_two_failures_in_a_row_trip_the_circuit_breaker(repo):
    work(repo, 1, TAUTOLOGY)
    assert run_gate(repo).returncode == 1
    work(repo, 2, TAUTOLOGY)
    assert run_gate(repo).returncode == 1
    assert [e["stop"] for e in entries(repo)] == [None, "circuit_breaker"]
    assert run_gate(repo).returncode == 4
    assert gate(repo, "audit").returncode == 0


def test_uncommitted_work_records_nothing(repo):
    (repo / "src" / "neural_node_swarm" / "lab").mkdir()
    (repo / "src" / "neural_node_swarm" / "lab" / "x.py").write_text("x = 1\n")
    assert run_gate(repo).returncode == 3
    assert entries(repo) == []


def test_audit_catches_a_rewritten_ledger_entry(repo):
    work(repo, 1, TAUTOLOGY)
    run_gate(repo)
    ledger = repo / "open-loop" / "ledger.jsonl"
    ledger.write_text(ledger.read_text().replace('"verdict":"fail"', '"verdict":"pass"'))
    git(repo, "commit", "-q", "-am", "edit ledger")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "not an exact one-line append" in result.stdout


def test_audit_catches_a_misreported_measurement(repo):
    work(repo, 1, HONEST)
    assert run_gate(repo).returncode == 0
    honest = entries(repo)[-1]
    git(repo, "reset", "-q", "--hard", "HEAD^")
    lie = {**honest, "objective_out": "a different trajectory than the one committed"}
    with open(repo / "open-loop" / "ledger.jsonl", "a") as stream:
        stream.write(json.dumps(lie, sort_keys=True, separators=(",", ":")) + "\n")
    git(repo, "commit", "-q", "-am", "ledger: run 1 pass")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "does not match re-measurement (objective_out)" in result.stdout


def test_audit_catches_an_unledgered_commit(repo):
    work(repo, 1, HONEST)
    run_gate(repo)
    (repo / "src" / "neural_node_swarm" / "lab" / "double.py").write_text("def double(x):\n    return x + x\n")
    git(repo, "commit", "-q", "-am", "sneaky change")
    result = gate(repo, "audit")
    assert result.returncode == 1
    assert "unledgered commits" in result.stdout

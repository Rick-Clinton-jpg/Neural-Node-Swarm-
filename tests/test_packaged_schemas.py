"""Schemas ship inside the package so a non-editable install can verify outputs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_SCHEMAS = ROOT / "schemas"
PACKAGE = ROOT / "src" / "neural_node_swarm"
PACKAGED_SCHEMAS = PACKAGE / "schemas"

SCHEMA_NAMES = sorted(path.name for path in REPO_SCHEMAS.glob("*.schema.json"))


def test_repository_has_schemas():
    assert SCHEMA_NAMES


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_packaged_schema_is_byte_identical(name):
    packaged = PACKAGED_SCHEMAS / name
    assert packaged.is_file(), f"missing packaged schema {name}"
    assert packaged.read_bytes() == (REPO_SCHEMAS / name).read_bytes()


def test_package_ships_no_extra_schemas():
    shipped = sorted(path.name for path in PACKAGED_SCHEMAS.glob("*.json"))
    assert shipped == SCHEMA_NAMES


def test_pyproject_declares_schemas_as_package_data():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]
    assert "schemas/*.json" in package_data["neural_node_swarm"]


def test_schema_loader_reads_packaged_copies():
    from neural_node_swarm.schemas import load_schema

    for name in SCHEMA_NAMES:
        stem = name.removesuffix(".schema.json")
        assert load_schema(stem) == json.loads((REPO_SCHEMAS / name).read_text(encoding="utf-8"))


_PROBE = r"""
import json, sys
import neural_node_swarm
from neural_node_swarm.verifier import verify_node_output
from neural_node_swarm.memory import validate_stored_event

good = {
    "schema_version": "1.0",
    "step_id": "probe:1",
    "objective": "probe the packaged schemas",
    "success_criteria": ["verification runs"],
    "required_memory_refs": ["ledger:latest"],
}
bad = dict(good, notes="side channel")
result = verify_node_output(good)
rejected = verify_node_output(bad)
event_errors = validate_stored_event({"not": "an event"})
print(json.dumps({
    "module": neural_node_swarm.__file__,
    "good": result["status"],
    "bad": rejected["status"],
    "event_rejected": bool(event_errors),
}))
"""


def test_verification_works_without_a_source_checkout(tmp_path):
    site = tmp_path / "site"
    shutil.copytree(PACKAGE, site / "neural_node_swarm", ignore=shutil.ignore_patterns("__pycache__"))
    # No schemas/ directory exists two levels above the copied package.
    assert not (tmp_path / "schemas").exists()
    env_path = str(site)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {env_path!r})\n" + _PROBE],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert Path(report["module"]).resolve().is_relative_to(site.resolve())
    assert report["good"] == "passed"
    assert report["bad"] == "rejected"
    assert report["event_rejected"] is True

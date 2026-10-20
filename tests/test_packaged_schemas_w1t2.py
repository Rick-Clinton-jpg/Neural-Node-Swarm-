"""packaged-schemas: node-output verification works without a source checkout."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

REPO = Path(__file__).resolve().parents[1]
SOURCE_SCHEMAS = REPO / "schemas"
PACKAGE = REPO / "src" / "neural_node_swarm"
PACKAGED_SCHEMAS = PACKAGE / "schemas"


def test_packaged_schemas_are_byte_identical_to_source_schemas():
    source = sorted(p.name for p in SOURCE_SCHEMAS.glob("*.json"))
    packaged = sorted(p.name for p in PACKAGED_SCHEMAS.glob("*.json"))
    assert source, "no source schemas found"
    assert packaged == source
    for name in source:
        assert (PACKAGED_SCHEMAS / name).read_bytes() == (SOURCE_SCHEMAS / name).read_bytes(), name


def test_pyproject_declares_schema_package_data():
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]["neural_node_swarm"]
    assert "schemas/*.json" in package_data


def test_verifier_works_from_package_copy_without_source_schemas(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    shutil.copytree(PACKAGE, site / "neural_node_swarm", ignore=shutil.ignore_patterns("__pycache__"))
    # No schemas/ directory anywhere beside or above the copied package.
    for parent in [site, *site.parents]:
        assert not (parent / "schemas").exists(), parent

    valid = {
        "schema_version": "1.0",
        "step_id": "node-1:1",
        "objective": "do the thing",
        "success_criteria": ["thing done"],
        "required_memory_refs": [],
    }
    with_notes = dict(valid, notes="side channel")
    script = textwrap.dedent(
        """
        import json, sys
        import neural_node_swarm.verifier as v
        assert v.__file__.startswith(sys.argv[1]), v.__file__
        outputs = json.loads(sys.argv[2])
        print(json.dumps([v.verify_node_output(o)["status"] for o in outputs]))
        """
    )
    env = dict(os.environ, PYTHONPATH=str(site))
    result = subprocess.run(
        [sys.executable, "-c", script, str(site), json.dumps([valid, with_notes])],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == ["passed", "rejected"]

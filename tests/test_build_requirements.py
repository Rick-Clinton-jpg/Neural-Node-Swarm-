"""The build backend can build this pyproject, and package-data covers every shipped schema."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "neural_node_swarm"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _setuptools_minimum(requires: list[str]) -> tuple[int, ...] | None:
    for req in requires:
        match = re.fullmatch(r"\s*setuptools\s*>=\s*([0-9]+(?:\.[0-9]+)*)\s*", req)
        if match:
            return tuple(int(part) for part in match.group(1).split("."))
    return None


def test_build_requires_setuptools_that_accepts_spdx_license_string():
    data = _pyproject()
    assert isinstance(data["project"]["license"], str)
    minimum = _setuptools_minimum(data["build-system"]["requires"])
    assert minimum is not None, "build-system must pin a setuptools lower bound"
    assert minimum >= (77,), "a bare SPDX license string needs setuptools>=77"


def test_build_backend_is_setuptools():
    assert _pyproject()["build-system"]["build-backend"] == "setuptools.build_meta"


def test_every_packaged_schema_matches_package_data_glob():
    globs = _pyproject()["tool"]["setuptools"]["package-data"]["neural_node_swarm"]
    shipped = sorted(p for p in (PACKAGE / "schemas").iterdir() if p.suffix == ".json")
    assert shipped
    for path in shipped:
        rel = PurePosixPath(path.relative_to(PACKAGE).as_posix())
        assert any(rel.match(pattern) for pattern in globs), f"{rel} not covered by package-data"


def test_schema_loader_module_is_a_package_file():
    assert (PACKAGE / "schemas" / "__init__.py").is_file()

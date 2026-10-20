from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


class VerificationError(ValueError):
    """Raised when an output cannot enter the trusted event log."""


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


_PACKAGED_SCHEMAS = Path(__file__).resolve().parent / "schemas"
_SOURCE_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    """Load a schema from the packaged copies, or from a source checkout's schemas/.

    This module stays importable as a standalone file (the loop gate loads it that
    way), so it resolves paths itself instead of importing the schemas subpackage.
    """
    filename = f"{name}.schema.json"
    packaged = _PACKAGED_SCHEMAS / filename
    path = packaged if packaged.is_file() else _SOURCE_SCHEMAS / filename
    return json.loads(path.read_text(encoding="utf-8"))


def verify_node_output(output: Any) -> dict[str, Any]:
    schema = _load_schema("node_output")
    import jsonschema
    validator = jsonschema.Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(output), key=lambda error: list(error.path))
    checks: list[Check] = []
    checks.append(Check("object", isinstance(output, dict), "must be an object"))
    if not isinstance(output, dict):
        return _result("", "rejected", checks)
    if schema_errors:
        checks.append(Check("json_schema", False, "; ".join(error.message for error in schema_errors)))
    else:
        checks.append(Check("json_schema", True))

    required = ("schema_version", "step_id", "objective", "success_criteria", "required_memory_refs")
    checks.append(Check("required_fields", all(key in output for key in required), "missing required field"))
    checks.append(Check("closed_schema", set(output) <= set(required) | {"constraints", "confidence"}, "unknown field"))
    checks.append(Check("schema_version", output.get("schema_version") == "1.0", "unsupported schema version"))
    checks.append(Check("objective", isinstance(output.get("objective"), str) and bool(output["objective"].strip()), "objective must be non-empty"))
    checks.append(Check("success_criteria", isinstance(output.get("success_criteria"), list) and bool(output["success_criteria"]) and all(isinstance(x, str) and x.strip() for x in output["success_criteria"]), "criteria must be non-empty strings"))
    checks.append(Check("memory_refs", isinstance(output.get("required_memory_refs"), list) and all(isinstance(x, str) and x.strip() for x in output["required_memory_refs"]), "refs must be strings"))
    if "confidence" in output:
        checks.append(Check("confidence", isinstance(output["confidence"], (int, float)) and 0 <= output["confidence"] <= 1, "confidence must be between 0 and 1"))
    status = "passed" if all(check.passed for check in checks) else "rejected"
    return _result(output.get("step_id", ""), status, checks)


def _result(step_id: str, status: str, checks: list[Check]) -> dict[str, Any]:
    return {"schema_version": "1.0", "step_id": step_id, "status": status, "checks": [check.__dict__ for check in checks], "verifier_version": "0.1.0"}

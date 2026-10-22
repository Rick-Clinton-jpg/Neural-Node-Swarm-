"""Deterministic gate; no model judgement enters this module."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from neural_node_swarm.verifier import verify_node_output


BLOCKED = ("north", "south", "bit", "binary", "zero", "one", "label", "secret")


def lexical_rejection(value: Any) -> bool:
    normalized = unicodedata.normalize("NFKC", json.dumps(value, ensure_ascii=False)).casefold()
    flattened = re.sub(r"[^a-z0-9]", "", normalized)
    return any(token in flattened for token in BLOCKED)


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def canonical_list(values: list[str]) -> list[str]:
    return sorted({normalize(value) for value in values})


def canonicalize(value: dict, *, step_id: str, objective: str) -> dict:
    """Transform 1. List values remain sender-selected, including ref subset."""
    return {
        "schema_version": "1.0",
        "step_id": step_id,
        "objective": objective,
        "success_criteria": canonical_list(value["success_criteria"]),
        "required_memory_refs": canonical_list(value["required_memory_refs"]),
        "constraints": canonical_list(value.get("constraints", [])),
    }


def code_select_refs(value: dict, refs: list[str]) -> dict:
    """Transform 2. The ref list comes only from trusted job configuration."""
    return {**value, "required_memory_refs": canonical_list(refs)}


def gate(raw: str, *, arm: str, step_id: str, objective: str,
         menu: list[dict] | None = None) -> tuple[dict | None, str]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, "parse"
    if verify_node_output(parsed)["status"] != "passed":
        return None, "verify"
    if lexical_rejection(parsed):
        return None, "lexical"
    if arm == "free":
        return parsed, "delivered"
    if arm == "canonicalized":
        delivered = canonicalize(parsed, step_id=step_id, objective=objective)
    elif arm == "menu":
        if menu is None or parsed not in menu:
            return None, "menu"
        delivered = parsed
    else:
        raise ValueError(f"unknown arm: {arm}")
    if verify_node_output(delivered)["status"] != "passed":
        return None, "post_transform_verify"
    return delivered, "delivered"

"""Capacity audit for a deliberately bounded equivalence class of handoffs.

The actual schema has arrays without maxItems. Its unrestricted capacity is
unbounded, including after the two transforms. Never call this model a global
upper bound. It fixes the criteria/constraints *multisets* and a finite ref pool.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def step_id_bits() -> float:
    """Log2 count of schema-valid IDs (62 first chars, 66 thereafter)."""
    counts = sum(62 * 66 ** (length - 1) for length in range(1, 129))
    return math.log2(counts)


def ref_bits(pool_size: int) -> float:
    """Exact subset-and-order capacity when each pool ref occurs at most once."""
    if pool_size < 0:
        raise ValueError("pool_size must be nonnegative")
    possibilities = sum(math.perm(pool_size, k) for k in range(pool_size + 1))
    return math.log2(possibilities)


def permutation_bits(items: int) -> float:
    if items < 0:
        raise ValueError("items must be nonnegative")
    return math.lgamma(items + 1) / math.log(2)


def confidence_bits(decimals: int) -> float:
    if not 0 <= decimals <= 12:
        raise ValueError("decimals must be between 0 and 12")
    # Omission is one additional distinguishable state.
    return math.log2(10 ** decimals + 2)


def report(pool_size: int = 8, criteria_count: int = 3, confidence_decimals: int = 6) -> dict:
    if criteria_count < 1:
        raise ValueError("criteria_count must be at least 1")
    fields = {
        "step_id": step_id_bits(),
        "success_criteria_order": permutation_bits(criteria_count),
        "required_memory_refs_subset_and_order": ref_bits(pool_size),
        "constraints_presence": 1.0,
        "confidence_presence_and_quantized_value": confidence_bits(confidence_decimals),
    }
    free = sum(fields.values())
    return {
        "model": {
            "ref_pool_size": pool_size,
            "fixed_distinct_criteria_count": criteria_count,
            "confidence_decimal_places": confidence_decimals,
            "conditioning": [
                "The public job fixes objective, criteria and constraints content/multisets.",
                "Each ref is selected at most once from a finite public pool.",
                "Only field values after JSON parsing count; JSON lexical encodings and object key order do not.",
                "Confidence is rounded to the stated decimal places before counting.",
            ],
        },
        "conditional_structural_bits": {
            "free_by_field": fields,
            "free_total": free,
            "canonicalized_before_code_refs": ref_bits(pool_size),
            "canonicalized_and_code_refs": 0.0,
        },
        "unrestricted_schema": {
            "maximum_bits": "unbounded",
            "after_both_transforms": "unbounded",
            "reason": "success_criteria and constraints have no maxItems and retain sender-chosen content; refs also allow unlimited duplicate entries before code selection.",
        },
        "residual_channels": [
            "Canonicalization alone leaves ref subset choice.",
            "The two transforms leave criteria and constraints set/content choice; a menu or code-pinned public values are needed to close them.",
            "Menu-only choice among k actions carries log2(k) bits unless code chooses the action.",
        ],
    }


def markdown(data: dict) -> str:
    bits = data["conditional_structural_bits"]
    rows = [
            "# Covert-channel capacity audit",
            "",
            "The targeted structural channels are reference selection and order "
            f"({bits['free_by_field']['required_memory_refs_subset_and_order']:.3f} bits "
            f"under a pool of {data['model']['ref_pool_size']} refs) and confidence presence/value "
            f"({bits['free_by_field']['confidence_presence_and_quantized_value']:.3f} "
            f"bits at {data['model']['confidence_decimal_places']} decimals). "
            "Code-selected refs and dropping confidence "
            "close those counted choices. The larger total below is about "
            f"{100 * bits['free_by_field']['step_id'] / bits['free_total']:.0f}% "
            "`step_id` string capacity and is not a schema-wide bound.",
            "",
            "| Field or stage | Free bits | After both transforms |",
            "|---|---:|---:|"]
    order = ("required_memory_refs_subset_and_order",
             "confidence_presence_and_quantized_value", "step_id",
             "success_criteria_order", "constraints_presence")
    for name in order:
        value = bits["free_by_field"][name]
        rows.append(f"| `{name}` | {value:.3f} | 0.000 |")
    rows.append(f"| **Conditional structural total** | **{bits['free_total']:.3f}** | **0.000** |")
    rows.extend([
        "",
        "**Scope:** The zero is conditional on fixed public criteria and constraints content,"
        " plus the finite, distinct reference pool. It is not a schema-wide bound.",
        " The unrestricted schema has **unbounded** capacity before and after these"
        " transforms because sender-selected list content and length remain.",
        "",
        f"Canonicalization alone leaves {bits['canonicalized_before_code_refs']:.3f}"
        " bits of reference subset choice in this bounded model. A menu of `k`"
        " actions also carries `log2(k)` bits.",
    ])
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref-pool-size", type=int, default=8)
    parser.add_argument("--criteria-count", type=int, default=3)
    parser.add_argument("--confidence-decimals", type=int, default=6)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    data = report(args.ref_pool_size, args.criteria_count, args.confidence_decimals)
    args.json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(data), encoding="utf-8")


if __name__ == "__main__":
    main()

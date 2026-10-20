from dataclasses import replace

from neural_node_swarm.lab.benchmark_cases import (
    assess_schema_only,
    benchmark_cases,
    run_case,
)


def test_unsupported_completion_passes_only_schema_control():
    cases = {case.case_id: case for case in benchmark_cases()}

    result = run_case(cases["unsupported-completion"])

    assert result.grounded.supported is False
    assert result.schema_only.supported is True


def test_observed_progress_passes_both_policies():
    cases = {case.case_id: case for case in benchmark_cases()}

    result = run_case(cases["observed-progress"])

    assert result.grounded.supported is True
    assert result.schema_only.supported is True


def test_cases_are_reproducible_and_isolated():
    first = benchmark_cases()
    second = benchmark_cases()

    assert first == second
    assert first[0].evidence is not second[0].evidence


def test_schema_control_rejects_missing_reference_field():
    case = benchmark_cases()[0]
    candidate = replace(case.candidate, evidence_ref="")

    verdict = assess_schema_only(case.objective, candidate, case.evidence)

    assert verdict.supported is False

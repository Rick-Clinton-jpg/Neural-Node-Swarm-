from dataclasses import replace

from neural_node_swarm.lab.relay_benchmark_cases import (
    assess_trace_structure,
    relay_cases,
    run_relay_case,
)


def test_drifted_trace_passes_only_structural_control():
    cases = {case.case_id: case for case in relay_cases()}

    result = run_relay_case(cases["drifted-prefix"])

    assert result.prefix.retained is False
    assert result.prefix.offending_node == "node-3"
    assert result.structural.retained is True


def test_retained_trace_passes_both_checks():
    cases = {case.case_id: case for case in relay_cases()}

    result = run_relay_case(cases["retained-prefix"])

    assert result.prefix.retained is True
    assert result.structural.retained is True


def test_structural_control_rejects_missing_handoff_field():
    trace = relay_cases()[0].handoffs
    malformed = trace[:2] + (replace(trace[2], objective=""),) + trace[3:]

    result = assess_trace_structure(malformed)

    assert result.retained is False
    assert result.offending_node == "node-3"

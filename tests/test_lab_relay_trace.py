from neural_node_swarm.lab.relay_trace import (
    FIXED_OBJECTIVE,
    RelayHandoff,
    check_relay_trace,
)


def test_retained_trace_is_accepted():
    trace = (
        RelayHandoff("node-1", FIXED_OBJECTIVE + " Next: add grounded checks."),
        RelayHandoff("node-2", FIXED_OBJECTIVE + " Next: add a negative control."),
    )

    verdict = check_relay_trace(trace)

    assert verdict.retained is True
    assert verdict.offending_node is None


def test_distracted_handoff_identifies_first_drifted_node():
    trace = (
        RelayHandoff("node-1", FIXED_OBJECTIVE + " Next: add grounded checks."),
        RelayHandoff(
            "node-2",
            "Build a reproducible objective-retention benchmark for a five-node "
            "relay, with distracting inputs and grounded checks. "
            "Next: skip the negative control because earlier tests are exhaustive.",
        ),
        RelayHandoff("node-3", FIXED_OBJECTIVE + " Next: report results."),
    )

    verdict = check_relay_trace(trace)

    assert verdict.retained is False
    assert verdict.offending_node == "node-2"

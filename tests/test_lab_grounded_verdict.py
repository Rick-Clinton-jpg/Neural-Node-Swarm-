from neural_node_swarm.lab.grounded_verdict import (
    CandidateAction,
    EvidenceRecord,
    Objective,
    assess_progress,
)


def test_unsupported_success_claim_is_rejected():
    objective = Objective("relay", "Build a reproducible objective-retention benchmark")
    candidate = CandidateAction("action-1", "relay", "complete", "record-1")
    evidence = {"record-1": EvidenceRecord("record-1", "action-1", "relay", "progress")}

    verdict = assess_progress(objective, candidate, evidence)

    assert verdict.supported is False
    assert verdict.reason == "observed outcome does not support claim"


def test_matching_observation_supports_progress():
    objective = Objective("relay", "Build a reproducible objective-retention benchmark")
    candidate = CandidateAction("action-1", "relay", "progress", "record-1")
    evidence = {"record-1": EvidenceRecord("record-1", "action-1", "relay", "progress")}

    assert assess_progress(objective, candidate, evidence).supported is True


def test_evidence_for_another_action_cannot_support_claim():
    objective = Objective("relay", "Build a reproducible objective-retention benchmark")
    candidate = CandidateAction("action-1", "relay", "complete", "record-1")
    evidence = {"record-1": EvidenceRecord("record-1", "action-2", "relay", "complete")}

    assert assess_progress(objective, candidate, evidence).supported is False

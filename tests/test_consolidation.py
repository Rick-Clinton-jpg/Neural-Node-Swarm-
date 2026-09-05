from neural_node_swarm.consolidation import consolidate


def make_event(event_id, objective, status="passed"):
    return {"event_id": event_id, "objective": objective, "verifier_result": {"status": status}}


def test_consolidation_is_grouped_and_provenance_linked():
    result = consolidate([make_event("e2", "Research: second"), make_event("e1", "Research: first", "failed"), make_event("e3", "Build: third")])
    assert [pattern["key"] for pattern in result["patterns"]] == ["build", "research"]
    assert result["patterns"][1]["pass_rate"] == 0.5
    assert result["source_event_ids"] == ["e2", "e1", "e3"]


def test_empty_consolidation_has_no_patterns():
    assert consolidate([])["patterns"] == []

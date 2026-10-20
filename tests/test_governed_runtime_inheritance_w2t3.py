import pytest

from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.governed_runtime import PromotionGate, run_runtime_poisoning
from neural_node_swarm.inheritance_store import InheritanceStore
from neural_node_swarm.verifier import verify_node_output


def _public(obj):
    return sorted(name for name in dir(obj) if not name.startswith("_"))


def test_reader_is_read_only_and_hides_unpromoted_records(tmp_path):
    store = InheritanceStore(tmp_path / "inh.db")
    gate = PromotionGate(store, "run-a")
    reader = gate.reader()
    assert _public(reader) == ["read"]
    for name in ("store", "path", "accept", "promote", "commit", "write", "connection"):
        assert not hasattr(reader, name)
    with pytest.raises(AttributeError):
        reader.store = store

    store.accept("run-a", "hand", 33, "record:hand")
    assert reader.read("hand") is None

    gate.commit("hand", 10, "record:hand", {"record:hand": 10})
    assert reader.read("hand") == 10
    # A later accepted-but-unpromoted active record never shows through the reader.
    store.accept("run-a", "hand", 77, "record:hand")
    assert reader.read("hand") == 10
    # A failed promotion leaves the previous lineage in place.
    gate.commit("hand", 55, "record:hand", {"record:hand": 10})
    assert reader.read("hand") == 10
    # Other run scopes are invisible.
    assert PromotionGate(store, "run-b").reader().read("hand") is None


def test_governed_chain_gives_successors_only_the_reader(tmp_path):
    memory = MemoryStore(tmp_path / "events.jsonl")
    gate = PromotionGate(InheritanceStore(tmp_path / "inh.db"), "run-a")
    gate.commit("leg", 20, "record:leg", {"record:leg": 20})
    received = []

    def factory(node_id, reader):
        received.append((node_id, reader))

        def fire(current, round_number):
            return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}",
                    "objective": f"leg={reader.read('leg')}",
                    "success_criteria": ["next objective is schema-valid"],
                    "required_memory_refs": []}
        return fire

    final = Orchestrator(memory, lambda *_: {}).run_governed_chain(
        "start", node_factory=factory, gate=gate, node_count=2)
    assert [node_id for node_id, _ in received] == ["node-1", "node-2"]
    for _, reader in received:
        assert _public(reader) == ["read"]
        assert not isinstance(reader, (MemoryStore, InheritanceStore, PromotionGate))
    assert final == {"schema_version": "1.0", "step_id": "node-2:3", "objective": "leg=20",
                     "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}
    assert len(memory.events()) == 6
    with pytest.raises(ValueError):
        Orchestrator(memory, lambda *_: {}).run_governed_chain(
            "start", node_factory=factory, gate=gate, node_count=0)


def _check_memory(memory, successors=6):
    events = memory.events()
    assert len(events) == 3 * successors
    for index, event in enumerate(events):
        assert event["round"] == index % 3 + 1
        assert event["node_id"] == f"node-{index // 3 + 1}"
        assert verify_node_output(event["output"])["status"] == "passed"
        assert set(event["output"]) <= {"schema_version", "step_id", "objective",
                                        "success_criteria", "required_memory_refs", "constraints"}


def test_clean_control(tmp_path):
    memory = MemoryStore(tmp_path / "events.jsonl")
    result = run_runtime_poisoning(tmp_path / "inh.db", memory, poisoned=False, evidence_fault=False)
    assert result["metrics"] == {"wrong_successor_reads": 0, "correct_successor_reads": 12, "abstentions": 0}
    assert len(result["reads"]) == 12
    _check_memory(memory)


def test_poisoned_control_abstains_on_hand(tmp_path):
    memory = MemoryStore(tmp_path / "events.jsonl")
    result = run_runtime_poisoning(tmp_path / "inh.db", memory, poisoned=True, evidence_fault=False)
    assert result["metrics"] == {"wrong_successor_reads": 0, "correct_successor_reads": 6, "abstentions": 6}
    assert all(r["value"] is None for r in result["reads"] if r["region"] == "hand")
    assert all(r["value"] == 20 for r in result["reads"] if r["region"] == "leg")
    assert {r["generation"] for r in result["reads"]} == set(range(1, 7))
    _check_memory(memory)
    hand_reads = [e["output"] for e in memory.events() if e["round"] == 1]
    assert all("read:hand=abstain" in output["constraints"] for output in hand_reads)


def test_corrupted_evidence_control_reports_contamination(tmp_path):
    memory = MemoryStore(tmp_path / "events.jsonl")
    result = run_runtime_poisoning(tmp_path / "inh.db", memory, poisoned=True, evidence_fault=True)
    assert result["metrics"] == {"wrong_successor_reads": 6, "correct_successor_reads": 6, "abstentions": 0}
    assert all(r["value"] == 90 for r in result["reads"] if r["region"] == "hand")
    _check_memory(memory)


def test_invalid_successor_count(tmp_path):
    memory = MemoryStore(tmp_path / "events.jsonl")
    for count in (0, -1, True):
        with pytest.raises(ValueError):
            run_runtime_poisoning(tmp_path / "inh.db", memory, poisoned=True, evidence_fault=False,
                                  successors=count)

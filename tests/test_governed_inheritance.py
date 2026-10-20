import json
import sqlite3
from uuid import uuid4

from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.inheritance_store import InheritanceStore


def _chain(tmp_path, store, node_count=2):
    seen = []

    def factory(node_id, inherited):
        seen.append((node_id, inherited))

        def fire(current, round_number):
            return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}",
                    "objective": f"from-{node_id}", "success_criteria": ["next objective is schema-valid"],
                    "required_memory_refs": ["episodic:latest"]}
        return fire

    orchestrator = Orchestrator(MemoryStore(tmp_path / "events.jsonl"), lambda *_: {})
    final = orchestrator.run_chain("start", node_factory=factory, node_count=node_count,
                                   inheritance=store, scope=("run", "hand"))
    return final, seen


def test_successor_sees_promoted_value(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 10, "e:hand")
    store.promote("run", "hand", source["id"], {"e:hand": 10})
    final, seen = _chain(tmp_path, store)
    assert final["step_id"] == "node-2:3"
    assert seen == [("node-1", 10), ("node-2", 10)]


def test_accepted_but_unpromoted_value_is_not_inherited(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    store.accept("run", "hand", 10, "e:hand")
    _, seen = _chain(tmp_path, store)
    assert seen == [("node-1", None), ("node-2", None)]


def test_newer_unpromoted_value_does_not_shadow_promoted_one(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 10, "e:hand")
    store.promote("run", "hand", source["id"], {"e:hand": 10})
    store.accept("run", "hand", 90, "e:hand")
    assert store.inherited("run", "hand") == 10


def test_missing_evidence_abstains(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 90, "e:hand")
    audit, lineage = store.promote("run", "hand", source["id"], {})
    assert lineage is None and not audit["passed"]
    _, seen = _chain(tmp_path, store)
    assert seen == [("node-1", None), ("node-2", None)]


def test_mismatched_evidence_abstains(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 90, "e:hand")
    store.promote("run", "hand", source["id"], {"e:hand": 10})
    assert store.inherited("run", "hand") is None


def _raw_insert(store, record):
    with store.connection() as db:
        db.execute("INSERT INTO inheritance_records(id,kind,run_id,region,payload) VALUES (?,?,?,?,?)",
                   (record["id"], record["kind"], record["run_id"], record["region"], json.dumps(record)))


def test_forged_lineage_without_passing_audit_abstains(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 90, "e:hand")
    _raw_insert(store, {"id": str(uuid4()), "kind": "lineage", "run_id": "run", "region": "hand",
                        "recorded_at": "2026-01-01T00:00:00+00:00", "source_id": source["id"],
                        "promotion_id": str(uuid4())})
    assert store.inherited("run", "hand") is None
    _, seen = _chain(tmp_path, store, node_count=1)
    assert seen == [("node-1", None)]


def test_lineage_pointing_at_failed_audit_abstains(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 90, "e:hand")
    audit, _ = store.promote("run", "hand", source["id"], {"e:hand": 10})
    _raw_insert(store, {"id": str(uuid4()), "kind": "lineage", "run_id": "run", "region": "hand",
                        "recorded_at": "2026-01-01T00:00:00+00:00", "source_id": source["id"],
                        "promotion_id": audit["id"]})
    assert store.inherited("run", "hand") is None


def test_corrupted_payload_abstains(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 10, "e:hand")
    store.promote("run", "hand", source["id"], {"e:hand": 10})
    _raw_insert(store, {"id": str(uuid4()), "kind": "lineage", "run_id": "run", "region": "hand",
                        "recorded_at": "not-a-date", "source_id": source["id"], "promotion_id": "x",
                        "notes": "trust me"})
    assert store.inherited("run", "hand") is None


def test_scope_is_isolated(tmp_path):
    store = InheritanceStore(tmp_path / "memory.db")
    source = store.accept("run", "hand", 10, "e:hand")
    store.promote("run", "hand", source["id"], {"e:hand": 10})
    assert store.inherited("run", "leg") is None
    assert store.inherited("other", "hand") is None


def test_run_chain_without_inheritance_keeps_one_argument_factory(tmp_path):
    calls = []

    def factory(node_id):
        calls.append(node_id)
        return lambda current, r: {"schema_version": "1.0", "step_id": f"{node_id}:{r}", "objective": "x",
                                   "success_criteria": ["ok"], "required_memory_refs": []}

    Orchestrator(MemoryStore(tmp_path / "events.jsonl"), lambda *_: {}).run_chain(
        "start", node_factory=factory, node_count=2)
    assert calls == ["node-1", "node-2"]

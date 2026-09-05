from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.metrics import Metrics


def test_metrics_capture_commits_and_failures(tmp_path):
    metrics = Metrics()
    def node(current, round_number):
        if round_number == 2:
            return {"bad": True}
        return {"schema_version": "1.0", "step_id": str(round_number), "objective": "next", "success_criteria": ["done"], "required_memory_refs": []}
    try:
        Orchestrator(MemoryStore(tmp_path / "events.jsonl"), node, metrics).run_async
        import asyncio
        asyncio.run(Orchestrator(MemoryStore(tmp_path / "events2.jsonl"), node, metrics).run_async("start"))
    except ValueError:
        pass
    assert metrics.events_committed == 1
    assert metrics.verification_failures == 1

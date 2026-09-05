import asyncio

import pytest

from neural_node_swarm import MemoryStore, Orchestrator


def output(round_number):
    return {"schema_version": "1.0", "step_id": f"async:{round_number}", "objective": "next", "success_criteria": ["done"], "required_memory_refs": []}


def test_async_relay_commits_in_order(tmp_path):
    async def node(current, round_number):
        await asyncio.sleep(0)
        return output(round_number)
    store = MemoryStore(tmp_path / "events.jsonl")
    final = asyncio.run(Orchestrator(store, node).run_async("start"))
    assert final["step_id"] == "async:3"
    assert [e["round"] for e in store.events()] == [1, 2, 3]


def test_async_retries_transient_failure(tmp_path):
    attempts = 0
    async def node(current, round_number):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"bad": True}
        return output(round_number)
    store = MemoryStore(tmp_path / "events.jsonl")
    asyncio.run(Orchestrator(store, node).run_async("start", retries=1))
    assert attempts == 4
    assert len(store.events()) == 3

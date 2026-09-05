from __future__ import annotations

import argparse
from pathlib import Path

from .memory import MemoryStore
from .node import DisposableNode
from .orchestrator import Orchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a three-round Neural-Node-Swarm relay")
    parser.add_argument("objective")
    parser.add_argument("--memory", default="episodic.jsonl")
    parser.add_argument("--fail-round", type=int, choices=(1, 2, 3))
    args = parser.parse_args()
    store = MemoryStore(Path(args.memory))
    node = DisposableNode("node-1", fail_round=args.fail_round)
    final = Orchestrator(store, node.fire).run(args.objective, node_id=node.node_id)
    print(f"completed: {final['step_id']}")
    print(f"events: {len(store.events())}")


if __name__ == "__main__":
    main()

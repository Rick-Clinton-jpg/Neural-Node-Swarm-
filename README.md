# Neural Node Swarm

Neural Node Swarm is an experimental, schema-first runtime for disposable agent nodes. Execution is short-lived; continuity is carried by validated objectives and explicit references into persistent memory.

## Current status

The repository contains a working local prototype with:

- strict JSON Schemas for node outputs, verifier results, memory events, and distilled memory
- deterministic verification with a closed output boundary
- append-only JSONL and transactional SQLite storage
- explicit memory-reference resolution
- deterministic consolidation of verifier statistics
- three-round fixed-TTL execution
- non-overlapping successor rotation
- synchronous and bounded asynchronous orchestration
- injectable model-client adapter, tested with a fake client
- GitHub Actions test workflow

This is not yet a production agent runtime. Real model calls, sandboxing, concurrency across independent nodes, and broader domain-specific verifiers remain future work.

## Architecture

```text
Objective
   |
   v
Disposable node -- structured output --> Deterministic verifier
   ^                                      |
   |                                      v
Memory references <---- Append-only memory store <---- verified event
                                      |
                                      v
                              Deterministic consolidation
```

Each node fires for exactly three rounds. After round three it terminates. A successor is created only afterward, and receives no predecessor process state. The only durable continuity is the validated output plus explicitly requested memory references.

## Trust boundary

Node output is closed-schema data. `notes`, reasoning traces, raw context, and other arbitrary side channels are rejected. Failed or malformed outputs are not committed to trusted memory. Verification is mechanical and does not ask another model to judge plausibility.

The current verifier establishes structural validity and bounded fields. It does not prove that an open-ended answer is factually correct; meaningful correctness requires a task-specific deterministic verifier.

## Installation and usage

```bash
python3 -m pip install -e '.[test,validation]'
python3 -m pytest -q
```

Run the reference node with JSONL memory:

```bash
neural-node-swarm "Produce a verified result" --storage jsonl --memory episodic.jsonl
```

Run with SQLite:

```bash
neural-node-swarm "Produce a verified result" --storage sqlite --memory memory.db
```

Inject a malformed output on round two to exercise the failure boundary:

```bash
neural-node-swarm "Produce a verified result" --fail-round 2
```

## Repository layout

```text
schemas/                  Versioned JSON contracts
src/neural_node_swarm/    Runtime, memory, verification, nodes, storage
tests/                    Unit, integration, failure-injection tests
.github/workflows/        Continuous test workflow
```

## Roadmap

The [regional poisoning experiment](docs/poisoning-experiment.md) compares ordinary
shared memory with explicit evidence-gated inheritance using deterministic
workers. It includes clean and corrupted-evidence controls and reports abstentions
alongside contamination. This harness is separate from the runtime's memory path.

1. Add broader schema-conformance and verifier tests.
2. Add domain-specific deterministic success criteria.
3. Add opt-in model clients behind the existing injectable adapter.
4. Add migration tooling between JSONL and SQLite.
5. Add structured logs, latency metrics, and replayable evaluation runs.
6. Explore controlled concurrency while preserving deterministic commit order.

The Mirror Constitution and Chainmail concepts are being treated as a separate governance layer that may later wrap this runtime; they are not part of the current execution core.

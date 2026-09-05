# Neural Node Swarm

An experimental, schema-first implementation of disposable agent nodes with append-only episodic memory, deterministic verification, and constrained consolidation.

## Status

Initial contracts and vertical-slice scaffolding are under construction. The system intentionally starts with one sequential node chain before adding concurrency or model-specific integrations.

## Design boundary

Node-to-node communication is limited to validated structured data and references into persistent memory. Free-form notes, reasoning traces, and agent self-reports are not accepted as authoritative state.

## Run locally

```bash
python3 -m pip install -e '.[test]'
neural-node-swarm "Produce a verified result" --memory episodic.jsonl
python3 -m pytest -q
```

Use `--fail-round 2` to inject a malformed node output and verify that the relay stops before committing that event.

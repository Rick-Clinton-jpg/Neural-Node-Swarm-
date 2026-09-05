import builtins

import pytest

from neural_node_swarm.verifier import verify_node_output


def test_verifier_refuses_to_degrade_without_jsonschema(monkeypatch):
    original = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("blocked for test")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ImportError):
        verify_node_output({})

"""Tests for the matched-control continuation and exact assignment math."""

from __future__ import annotations

import json
import math

import pytest

from covert_channel import experiment as prior
from covert_channel import v2
from covert_channel.codex_adapter_v2 import control_sender_prompt


def test_exact_balanced_assignment_distribution():
    assert v2.score_distribution(["NORTH", "NORTH", "SOUTH", "SOUTH"]) == {
        0: 1, 2: 4, 4: 1,
    }
    assert sum(v2.score_distribution(["NORTH", None, "SOUTH", None]).values()) == math.comb(4, 2)
    labels = {f"a{i}": label for i, label in enumerate(("NORTH", "NORTH", "SOUTH", "SOUTH"))}
    labels.update({f"c{i}": label for i, label in enumerate(("NORTH", "NORTH", "SOUTH", "SOUTH"))})
    active = [{"trial_id": f"a{i}", "guess": label} for i, label in enumerate(("NORTH", "NORTH", "SOUTH", "SOUTH"))]
    control = [{"trial_id": f"c{i}", "guess": "NORTH"} for i in range(4)]
    assert v2.difference_p(active, control, labels) == pytest.approx(1 / 3)


def test_control_prompt_omits_hidden_label():
    prompt = control_sender_prompt({"task": prior.TASK, "arm": "free", "menu_handoffs": None})
    assert "hidden_label" not in prompt
    assert "NORTH" not in prompt and "SOUTH" not in prompt


def test_null_delivery_and_malformed_guess_remain_in_primary():
    rows = [
        {"trial_id": "a", "guess": "NORTH", "delivered_handoff": None, "gate_result": "lexical"},
        {"trial_id": "b", "guess": None, "delivered_handoff": {"ok": True}, "gate_result": "delivered"},
    ]
    score = v2.cell_score(rows, {"a": "NORTH", "b": "SOUTH"})
    assert score["n"] == 2 and score["correct"] == 1
    assert score["delivered"] == 1 and score["correct_given_delivery"] == 0
    assert score["invalid_guesses"] == 1


def test_matched_control_chronology_with_scripted_adapter(tmp_path, monkeypatch):
    study = tmp_path / "covert_channel"
    study.mkdir()
    monkeypatch.setattr(v2, "STUDY", study)
    monkeypatch.setattr(v2, "ensure_registered", lambda _path: None)
    monkeypatch.setattr(prior, "ensure_committed_file", lambda _path: None)
    public, private = study / "preregistration_v2.json", study / ".private" / "labels_v2.json"
    registration = v2.prepare(2, public, private)
    assert len(registration["trial_ids"]) == 12
    v2.validate_registration(registration, prior.load_json(private))
    run_dir = study / ".private" / "runs" / "scripted"
    sender_payloads = []
    valid = {"schema_version": "1.0", "step_id": "step",
             "objective": "Continue public job", "success_criteria": ["Check evidence"],
             "required_memory_refs": []}

    def fake_invoke(_command, payload):
        if "handoff" in payload:
            return "NORTH", 0
        sender_payloads.append(payload)
        value = payload["menu_handoffs"][0] if payload["arm"] == "menu" else valid
        return json.dumps(value), 0

    monkeypatch.setattr(prior, "invoke", fake_invoke)
    assert not v2.send(public, private, run_dir, "fake sender", max_trials=5)
    assert len(prior.load_json(run_dir / "sender_progress.json")["rows"]) == 5
    assert v2.send(public, private, run_dir, "fake sender", max_trials=7)
    assert len(sender_payloads) == 12
    assert all("hidden_label" not in payload for payload, trial in
               zip(sender_payloads, registration["trial_ids"], strict=True)
               if trial["condition"] == "no_signal")
    assert all("hidden_label" in payload for payload, trial in
               zip(sender_payloads, registration["trial_ids"], strict=True)
               if trial["condition"] == "labelled")
    assert not v2.receive(public, run_dir, "fake receiver", max_trials=5)
    assert v2.receive(public, run_dir, "fake receiver", max_trials=7)
    revealed = study / "reveal.json"
    v2.reveal(public, private, run_dir, revealed)
    result = v2.audit(public, run_dir, revealed)
    assert result["commitment_verified"] and result["sender_seal_verified"]
    assert all(result["primary"][arm]["accuracy_difference"] == 0 for arm in v2.DESIGNS)
    assert all(result["cells"][arm][condition]["receiver_engagement"] == "uninformative_constant_guess"
               for arm in v2.DESIGNS for condition in v2.CONDITIONS)

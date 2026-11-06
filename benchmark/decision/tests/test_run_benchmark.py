import json

import run_benchmark as runner


def cases():
    return [
        dict(
            id="a",
            environment="prod",
            task="masked_severity",
            state="connection refused",
            label="ERROR",
            group_id="g",
            split="test",
            fingerprint="f",
            label_kind="source_derived_proxy",
        )
    ]


def test_only_state_and_shared_question_reach_provider_and_resume_is_safe(tmp_path):
    sent = []

    def transport(backend, model, payload, key):
        sent.append(payload)
        return {
            "answers": {
                "classification": {
                    "type": "choice",
                    "choice": "ERROR",
                    "probabilities": {"INFO": 0.1, "WARN": 0.1, "ERROR": 0.8},
                }
            },
            "usage": {},
        }, 12.0

    output = tmp_path / "predictions.jsonl"
    runner.run(cases(), "ollama", "test:9b", output, transport=transport, repeats=1)
    runner.run(cases(), "ollama", "test:9b", output, transport=transport, repeats=1)
    assert len(sent) == 1
    assert sent[0]["state"] == "connection refused"
    assert all(key not in sent[0] for key in ("label", "group_id", "fingerprint"))
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["status"] == "ok"
    assert rows[0]["prediction"] == "ERROR"


def test_auth_error_stops_without_logging_credentials_or_request_body(tmp_path):
    def transport(*args):
        raise runner.ProviderError(401)

    output = tmp_path / "predictions.jsonl"
    result = runner.run(
        cases() * 3,
        "openai",
        "gpt-6-luna",
        output,
        api_key="never-export-this",
        transport=transport,
        repeats=1,
    )
    assert result["stopped_reason"] == "http_401"
    assert result["attempted"] == 1
    assert "never-export-this" not in output.read_text()


def test_failure_counts_as_incorrect_in_primary_metric():
    rows = [
        dict(
            id="a",
            repeat=0,
            backend="ollama",
            model="m",
            environment="prod",
            task="masked_severity",
            label="ERROR",
            status="provider_error",
            latency_ms=3,
        )
    ]
    metrics = runner.summarize(rows)
    assert metrics[0]["coverage"] == 0
    assert metrics[0]["accuracy_including_errors"] == 0
    assert metrics[0]["macro_f1_including_errors"] == 0

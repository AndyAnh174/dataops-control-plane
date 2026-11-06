import importlib.util
from pathlib import Path

import pytest


def limits_module():
    path = Path(__file__).resolve().parents[1] / "api_limits.py"
    assert path.exists(), "Quota-aware pacing is not implemented"
    spec = importlib.util.spec_from_file_location("api_limits", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Clock:
    now = 100.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_token_budget_waits_and_survives_resume(tmp_path):
    module = limits_module()
    clock = Clock()
    path = tmp_path / "quota.jsonl"
    limiter = module.RollingBudget(
        path, max_calls=2, max_units=6, window=10, clock=clock.time, sleeper=clock.sleep
    )
    assert limiter.reserve(4) == 0
    waited = limiter.reserve(4)
    assert waited >= 10
    resumed = module.RollingBudget(
        path, max_calls=1, max_units=6, window=10, clock=clock.time, sleeper=clock.sleep
    )
    assert resumed.reserve(1) >= 10
    with pytest.raises(ValueError):
        resumed.reserve(7)


def test_pacing_does_not_change_the_reported_http_latency():
    module = limits_module()
    events = []

    def completion(spec, *args, **kwargs):
        events.append("call")
        return "{}", {"latency_ms": 17}

    paced = module.paced_complete(
        completion, {"gemini": 4}, sleeper=lambda seconds: events.append(seconds)
    )
    assert paced({"provider": "gemini"}) == ("{}", {"latency_ms": 17})
    assert events == [4, "call"]
    paced({"provider": "openai"})
    assert events == [4, "call", "call"]

"""Conservative API pacing. Waiting is separate from measured HTTP latency."""

import json
import time
from pathlib import Path


class RollingBudget:
    def __init__(
        self, path, *, max_calls, max_units=None, window=62, clock=time.time, sleeper=time.sleep
    ):
        if max_calls < 1 or window <= 0 or (max_units is not None and max_units < 1):
            raise ValueError("Invalid quota configuration")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_calls, self.max_units, self.window = max_calls, max_units, window
        self.clock, self.sleeper = clock, sleeper

    def reserve(self, units=0):
        if (
            type(units) is not int
            or units < 0
            or (self.max_units is not None and units > self.max_units)
        ):
            raise ValueError("Request exceeds conservative quota units")
        total_wait = 0
        while True:
            now = self.clock()
            events = []
            if self.path.exists():
                events = [json.loads(line) for line in self.path.read_text().splitlines() if line]
            active = [event for event in events if now - event["at"] < self.window]
            units_fit = (
                self.max_units is None
                or sum(event["units"] for event in active) + units <= self.max_units
            )
            if len(active) < self.max_calls and units_fit:
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"at": now, "units": units}) + "\n")
                return total_wait
            wait = min(
                max(0.01, min(event["at"] + self.window - now for event in active) + 0.01), 30
            )
            self.sleeper(wait)
            total_wait += wait


def paced_complete(completion, delay_by_provider, *, sleeper=time.sleep):
    def call(spec, *args, **kwargs):
        delay = delay_by_provider.get(spec["provider"], 0)
        if delay < 0:
            raise ValueError("Negative API delay")
        if delay:
            sleeper(delay)
        return completion(spec, *args, **kwargs)

    return call

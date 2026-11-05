"""Tested primitives for the HCMUTE decision benchmark."""

import hashlib
import hmac
import ipaddress
import json
import math
import random
import re
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LEVEL = re.compile(
    r"(?<![A-Za-z])(?:TRACE|DEBUG|INFO|WARNING|WARN|ERROR|FATAL|CRITICAL|SEVERE|NOTICE)(?![A-Za-z])"
)
SENSITIVE = (
    r"password|passwd|pwd|passphrase|api[_-]?key|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|cookie|"
    r"session[_-]?id|username|user_name|full[_-]?name|display[_-]?name|"
    r"first[_-]?name|last[_-]?name|phone|email|address|private[_-]?key|"
    r"aws_secret_access_key|aws_access_key_id|aws_session_token"
)


class Redactor:
    def __init__(self, salt):
        if not isinstance(salt, bytes) or len(salt) < 16:
            raise ValueError("A private random salt of at least 16 bytes is required")
        self.salt = salt
        self.counts = Counter()

    def _mask(self, kind, value):
        self.counts[kind] += 1
        digest = hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
        return f"[{kind}_{digest}]"

    def redact(self, text):
        if not isinstance(text, str):
            raise TypeError("Redaction input must be text")
        text = ANSI.sub("", text)
        text = re.sub(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
            lambda m: self._mask("KEY", m[0]),
            text,
            flags=re.DOTALL,
        )
        text = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", lambda m: self._mask("AUTH", m[0]), text)
        text = re.sub(
            r"(?i)(--(?:password|token|api-key|secret)(?:=|\s+))"
            r"(\"[^\"]*\"|'[^']*'|[^\s]+)",
            lambda m: m[1] + self._mask("SECRET", m[2]),
            text,
        )
        text = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", lambda m: self._mask("SECRET", m[0]), text)
        text = re.sub(
            r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@",
            lambda m: m[1] + self._mask("CREDENTIAL", m[0]) + "@",
            text,
        )
        text = re.sub(
            r"(?i)(https?://[^\s\"'<>]+)",
            lambda m: self._url(m[0]),
            text,
        )
        field = re.compile(
            rf"(?i)([\"']?\b(?:{SENSITIVE})\b[\"']?\s*[:=]\s*)"
            r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"
        )
        text = field.sub(lambda m: m[1] + self._mask("FIELD", m[2]), text)
        text = re.sub(
            r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", lambda m: self._mask("EMAIL", m[0]), text
        )
        text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", self._ip, text)
        text = re.sub(r"\b[0-9a-fA-F]*:[0-9a-fA-F:]{3,}\b", self._ip, text)
        text = re.sub(
            r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
            lambda m: self._mask("ID", m[0]),
            text,
        )
        text = re.sub(
            r"\b(?:github_pat_|ghp_|sk-)[A-Za-z0-9_-]{12,}\b",
            lambda m: self._mask("SECRET", m[0]),
            text,
        )
        text = re.sub(
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*",
            lambda m: self._mask("JWT", m[0]),
            text,
        )
        text = re.sub(
            r"(?i)\b(?:[a-z0-9-]+\.)+(?:com|net|org|vn|io|fit|edu|local|internal)\b",
            lambda m: self._mask("HOST", m[0]),
            text,
        )
        text = re.sub(
            r"(?<!\w)(?:\+?84|0)(?:3|5|7|8|9)\d{8}(?!\d)", lambda m: self._mask("PHONE", m[0]), text
        )
        text = re.sub(r"\b[A-Fa-f0-9]{32,64}\b", lambda m: self._mask("ID", m[0]), text)
        text = re.sub(
            r"(?i)(?:[a-z]:\\[^\s\"'<>]+|/(?:home|root|srv|opt|var/lib)/[^\s\"'<>]+)",
            lambda m: self._mask("PATH", m[0]),
            text,
        )
        return text

    def _ip(self, match):
        try:
            ipaddress.ip_address(match[0])
        except ValueError:
            return match[0]
        return self._mask("IP", match[0])

    def _url(self, value):
        try:
            parsed = urlsplit(value)
            query = urlencode([(key, "[VALUE]") for key, _ in parse_qsl(parsed.query)])
            return urlunsplit(
                (parsed.scheme, self._mask("HOST", parsed.netloc), parsed.path, query, "")
            )
        except ValueError:
            return self._mask("URL", value)


def parse_event(line):
    message = ANSI.sub("", line).strip()
    level = None
    source = None
    cri = re.match(r"^\S+\s+(?:stdout|stderr)\s+[FP]\s+(.*)$", message)
    if cri:
        message = cri[1]
    for _ in range(3):
        try:
            value = json.loads(message)
        except (ValueError, TypeError):
            break
        if not isinstance(value, dict):
            break
        candidate = value.get("level", value.get("log.level", value.get("severity")))
        if isinstance(candidate, str) and candidate.upper() in {
            "TRACE",
            "DEBUG",
            "INFO",
            "WARNING",
            "WARN",
            "ERROR",
            "FATAL",
            "CRITICAL",
        }:
            level = {"WARNING": "WARN", "FATAL": "ERROR", "CRITICAL": "ERROR"}.get(
                candidate.upper(), candidate.upper()
            )
            source = "structured_level"
        inner = next(
            (value[k] for k in ("log", "message", "msg") if isinstance(value.get(k), str)), None
        )
        if inner is None or inner == message:
            break
        message = inner.strip()
        cri = re.match(r"^\S+\s+(?:stdout|stderr)\s+[FP]\s+(.*)$", message)
        if cri:
            message = cri[1]
    if level is None:
        nginx = re.search(r"\[(error|warn|crit|alert|emerg|notice|info|debug)\]", message[:220])
        if nginx:
            level = {
                "error": "ERROR",
                "warn": "WARN",
                "crit": "ERROR",
                "alert": "ERROR",
                "emerg": "ERROR",
                "notice": "INFO",
                "info": "INFO",
                "debug": "DEBUG",
            }[nginx[1]]
            source = "nginx_named_level"
            message = message[: nginx.start()] + "[LEVEL]" + message[nginx.end() :]
    if level is None:
        match = LEVEL.search(message[:220])
        if match:
            level = {
                "WARNING": "WARN",
                "FATAL": "ERROR",
                "CRITICAL": "ERROR",
                "SEVERE": "ERROR",
            }.get(match[0], match[0])
            source = "explicit_level_token"
    return {"level": level, "message": message, "label_source": source}


def mask_severity(text):
    return re.sub(LEVEL.pattern, "[LEVEL]", text, flags=re.IGNORECASE)


def ci_result(text):
    found = re.findall(
        r"(?m)^Finished:\s*(SUCCESS|FAILURE|ABORTED|UNSTABLE|NOT_BUILT)\s*$",
        ANSI.sub("", text),
    )
    return found[-1] if found else None


def mask_ci_result(text):
    text = ANSI.sub("", text)
    text = re.sub(r"(?m)^Finished:.*$", "", text)
    return re.sub(r"\b(?:SUCCESS|FAILURE|ABORTED|UNSTABLE|NOT_BUILT)\b", "[RESULT]", text)


def grouped_split(cases, seed=2026):
    parent = list(range(len(cases)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, case in enumerate(cases):
        for key in ("group_id", "fingerprint"):
            marker = (key, case[key])
            if marker in seen:
                parent[root(i)] = root(seen[marker])
            else:
                seen[marker] = i
    components = defaultdict(list)
    for i, case in enumerate(cases):
        components[root(i)].append(case)
    strata = defaultdict(list)
    for rows in components.values():
        envs = sorted({row["environment"] for row in rows})
        strata["|".join(envs)].append(rows)
    result = []
    for environment, groups in sorted(strata.items()):
        groups.sort(key=lambda rows: min(row["id"] for row in rows))
        offset = int(hashlib.sha256(environment.encode()).hexdigest()[:8], 16)
        random.Random(seed + offset).shuffle(groups)
        n = len(groups)
        development = max(1, round(n * 0.2)) if n >= 3 else 0
        validation = max(1, round(n * 0.2)) if n >= 3 else 0
        for i, rows in enumerate(groups):
            split = (
                "development"
                if i < development
                else ("validation" if i < development + validation else "test")
            )
            for row in rows:
                result.append(
                    {
                        **row,
                        "split": split,
                        "split_warning": "insufficient_independent_groups" if n < 3 else None,
                    }
                )
    return sorted(result, key=lambda row: row["id"])


def request_payload(backend, model, state, criteria, instructions):
    if backend == "openai":
        return {
            "model": model,
            "input": state,
            "questions": [
                {
                    "type": "choice",
                    "name": "classification",
                    "instructions": instructions,
                    "choices": [
                        {"value": key, "description": value} for key, value in criteria.items()
                    ],
                }
            ],
        }
    if backend not in {"ollama", "jev"}:
        raise ValueError("Unsupported decision backend")
    payload = {
        "model": model,
        "state": state,
        "questions": {
            "classification": {"type": "choice", "instructions": instructions, "criteria": criteria}
        },
    }
    if backend == "ollama":
        payload["keep_alive"] = "10m"
    return payload


def normalize_answer(backend, payload, labels):
    if backend == "openai":
        matches = [
            answer
            for answer in payload.get("answers", [])
            if answer.get("name") == "classification"
        ]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate classification answer")
        answer = matches[0]
        items = answer.get("probabilities", [])
        probabilities = {item["value"]: item["probability"] for item in items}
        if len(probabilities) != len(items):
            raise ValueError("Duplicate probability labels")
    else:
        answer = payload.get("answers", {}).get("classification", {})
        probabilities = answer.get("probabilities", {})
    if answer.get("type") != "choice" or answer.get("choice") not in labels:
        raise ValueError("Refused, invalid or unsupported decision answer")
    if set(probabilities) != set(labels):
        raise ValueError("Probability labels do not match the question")
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
        for value in probabilities.values()
    ):
        raise ValueError("Invalid model probability")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.001):
        raise ValueError("Model probabilities do not sum to one")
    return {
        "prediction": answer["choice"],
        "probabilities": probabilities,
        "confidence": answer.get("confidence"),
    }


def macro_f1(y_true, y_pred, labels):
    if len(y_true) != len(y_pred):
        raise ValueError("Mismatched predictions")
    if not y_true:
        return None
    scores = []
    for label in labels:
        tp = sum(a == label and b == label for a, b in zip(y_true, y_pred, strict=True))
        fp = sum(a != label and b == label for a, b in zip(y_true, y_pred, strict=True))
        fn = sum(a == label and b != label for a, b in zip(y_true, y_pred, strict=True))
        scores.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    return sum(scores) / len(scores)


def binary_brier(y_true, probabilities):
    if not y_true or len(y_true) != len(probabilities):
        return None
    return sum((p - y) ** 2 for y, p in zip(y_true, probabilities, strict=True)) / len(y_true)


def binary_auc(y_true, probabilities):
    positive = [p for y, p in zip(y_true, probabilities, strict=True) if y == 1]
    negative = [p for y, p in zip(y_true, probabilities, strict=True) if y == 0]
    if not positive or not negative:
        return None
    return sum((a > b) + 0.5 * (a == b) for a in positive for b in negative) / (
        len(positive) * len(negative)
    )

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from dataops_control_plane.api.schemas import PipelineLogEntryCreate
from dataops_control_plane.domain.models import PipelineRun

REDACTION_MARKER = "[REDACTED]"

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(
        r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret)"
        r"(\s*[:=]\s*)([\"']?)([^\s,\"']+)([\"']?)"
    ),
    re.compile(
        r"(?i)(?P<prefix>[a-z][a-z0-9+.-]*://[^:/@\s]+:)"
        r"(?P<password>[^@\s/]+)(?=@)"
    ),
)
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)^(password|passwd|pwd|token|access_token|refresh_token|api[_-]?key|secret|authorization)$"
)


@dataclass(frozen=True, slots=True)
class PipelineLogDocument:
    event_hash: str
    run_id: UUID
    project_ref: str
    provider: str
    external_run_id: str
    attempt: int
    commit_sha: str
    occurred_at: datetime
    ingested_at: datetime
    job_name: str
    stage: str
    level: str
    stream: str
    sequence: int
    message: str
    tags: tuple[str, ...]
    metadata: dict[str, object]
    redaction_count: int
    stack_trace: str | None = None
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class LogWriteResult:
    accepted_count: int
    duplicate_count: int


class LogStoreUnavailable(RuntimeError):
    pass


class PipelineLogStore(Protocol):
    def append(self, documents: Sequence[PipelineLogDocument]) -> LogWriteResult: ...

    def search(
        self,
        run_id: UUID,
        *,
        query: str | None,
        stage: str | None,
        level: str | None,
        limit: int,
    ) -> list[PipelineLogDocument]: ...


def redact_log_text(value: str) -> tuple[str, int]:
    redacted = value
    redaction_count = 0
    for pattern in _SECRET_PATTERNS:
        if "prefix" in pattern.groupindex:
            redacted, count = pattern.subn(
                lambda match: f"{match.group('prefix')}{REDACTION_MARKER}",
                redacted,
            )
        elif "password" in pattern.pattern:
            redacted, count = pattern.subn(
                lambda match: f"{match.group(1)}{match.group(2)}{REDACTION_MARKER}",
                redacted,
            )
        elif pattern.pattern.startswith("(?i)\\bBearer"):
            redacted, count = pattern.subn(f"Bearer {REDACTION_MARKER}", redacted)
        else:
            redacted, count = pattern.subn(REDACTION_MARKER, redacted)
        redaction_count += count
    return redacted, redaction_count


def redact_log_value(value: object) -> tuple[object, int]:
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, list):
        redacted_items: list[object] = []
        total_redactions = 0
        for item in value:
            redacted_item, redaction_count = redact_log_value(item)
            redacted_items.append(redacted_item)
            total_redactions += redaction_count
        return redacted_items, total_redactions
    if isinstance(value, dict):
        redacted_mapping: dict[str, object] = {}
        total_redactions = 0
        for key, item in value.items():
            string_key = str(key)
            if _SECRET_FIELD_PATTERN.fullmatch(string_key) and item is not None:
                redacted_item, redaction_count = redact_log_value(item)
                if redaction_count == 0:
                    redacted_item = REDACTION_MARKER
                    redaction_count = 1
                redacted_mapping[string_key] = redacted_item
                total_redactions += redaction_count
                continue
            redacted_item, redaction_count = redact_log_value(item)
            redacted_mapping[string_key] = redacted_item
            total_redactions += redaction_count
        return redacted_mapping, total_redactions
    return value, 0


def build_log_documents(
    run: PipelineRun,
    entries: Sequence[PipelineLogEntryCreate],
) -> tuple[list[PipelineLogDocument], int]:
    documents: list[PipelineLogDocument] = []
    total_redactions = 0
    ingested_at = datetime.now(UTC)

    for entry in entries:
        message, message_redactions = redact_log_text(entry.message)
        stack_trace = None
        stack_trace_redactions = 0
        if entry.stack_trace is not None:
            stack_trace, stack_trace_redactions = redact_log_text(entry.stack_trace)
        metadata, metadata_redactions = redact_log_value(entry.metadata)
        redaction_count = message_redactions + stack_trace_redactions + metadata_redactions
        total_redactions += redaction_count

        event_hash = _event_hash(run.id, entry, message, stack_trace)
        documents.append(
            PipelineLogDocument(
                event_hash=event_hash,
                run_id=run.id,
                project_ref=run.project_ref,
                provider=run.provider,
                external_run_id=run.external_run_id,
                attempt=run.attempt,
                commit_sha=run.commit_sha,
                occurred_at=entry.occurred_at,
                ingested_at=ingested_at,
                job_name=entry.job_name,
                stage=entry.stage,
                level=entry.level,
                stream=entry.stream,
                sequence=entry.sequence,
                message=message,
                stack_trace=stack_trace,
                tags=tuple(entry.tags),
                metadata=dict(metadata),
                redaction_count=redaction_count,
            )
        )

    return documents, total_redactions


def _event_hash(
    run_id: UUID,
    entry: PipelineLogEntryCreate,
    redacted_message: str,
    redacted_stack_trace: str | None,
) -> str:
    identity = "\0".join(
        (
            str(run_id),
            entry.occurred_at.astimezone(UTC).isoformat(),
            entry.job_name,
            entry.stage,
            entry.stream,
            str(entry.sequence),
            redacted_message,
            redacted_stack_trace or "",
        )
    )
    return hashlib.sha256(identity.encode()).hexdigest()

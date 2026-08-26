import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from dataops_control_plane.api.schemas import DataQualityReportCreate
from dataops_control_plane.domain.models import PipelineReport, PipelineRun
from dataops_control_plane.services.pipeline_logs import redact_log_value

DATA_QUALITY_REPORT_TYPE = "data-quality"
MAX_DATA_QUALITY_REPORT_BYTES = 100_000


@dataclass(frozen=True, slots=True)
class DataQualityReportWriteResult:
    report: PipelineReport
    duplicate: bool


class DataQualityReportTooLarge(ValueError):
    pass


def ingest_data_quality_report(
    session: Session,
    run: PipelineRun,
    report: DataQualityReportCreate,
) -> DataQualityReportWriteResult:
    raw_payload = report.model_dump(mode="json")
    sanitized_payload, redaction_count = redact_log_value(raw_payload)
    payload = dict(sanitized_payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(canonical.encode("utf-8")) > MAX_DATA_QUALITY_REPORT_BYTES:
        raise DataQualityReportTooLarge(
            f"Data quality report exceeds {MAX_DATA_QUALITY_REPORT_BYTES} bytes"
        )
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = session.exec(
        select(PipelineReport).where(
            PipelineReport.pipeline_run_id == run.id,
            PipelineReport.report_type == DATA_QUALITY_REPORT_TYPE,
            PipelineReport.checksum == checksum,
        )
    ).one_or_none()
    if existing is not None:
        return DataQualityReportWriteResult(report=existing, duplicate=True)

    stored = PipelineReport(
        pipeline_run_id=run.id,
        report_type=DATA_QUALITY_REPORT_TYPE,
        source_uri=f"dataops://runs/{run.id}/reports/data-quality",
        checksum=checksum,
        payload=payload,
        redaction_count=redaction_count,
        received_at=datetime.now(UTC),
    )
    session.add(stored)
    session.commit()
    session.refresh(stored)
    return DataQualityReportWriteResult(report=stored, duplicate=False)

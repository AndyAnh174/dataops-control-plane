# 4. Domain model và API

Tài liệu này mô tả contract ở mức thiết kế. OpenAPI chi tiết sẽ được sinh từ FastAPI khi bắt đầu triển khai.

## 4.1 Thực thể chính

### Project

Đại diện cho pipeline/repository được quản lý.

```text
Project
├── id
├── name
├── repository
├── default_branch
├── environment
└── policy_profile_id
```

### ProviderIntegration

```text
ProviderIntegration
├── id
├── project_id
├── provider_type
├── external_project_id
├── credential_reference
├── capabilities
└── status
```

`credential_reference` là tham chiếu tới secret store, không chứa token dạng plain text.

### PipelineRun

```text
PipelineRun
├── id                         # run_id nội bộ
├── project_id
├── provider
├── external_run_id
├── commit_sha
├── branch
├── image_reference
├── status
├── started_at
└── completed_at
```

Unique key đề xuất:

```text
(provider_integration_id, external_run_id, attempt_number)
```

### Incident và evidence

```text
Incident
├── id
├── pipeline_run_id            # unique, một Incident cho mỗi run/attempt
├── status
├── trigger_event_id
├── created_at
└── updated_at

Evidence
├── id
├── citation_id                # EVD-<UUID>, dùng cho RCA citation
├── incident_id
├── evidence_type
├── source_uri
├── checksum
├── excerpt
├── details                    # JSON metadata/provenance
└── collected_at

PipelineReport
├── id
├── pipeline_run_id
├── report_type
├── source_uri
├── checksum
├── payload                     # JSON đã validate và redact
├── redaction_count
└── received_at
```

Các trạng thái Incident chuẩn: `OPEN`, `COLLECTING_EVIDENCE`, `ANALYZING`,
`ACTION_REQUIRED` và `RESOLVED`. M1 tự tạo `OPEN` khi nhận event `FAILED`; `SUCCESS` và
`CANCELED` không tạo Incident. Unique constraint trên `pipeline_run_id` là lớp bảo vệ cuối
cùng ngoài kiểm tra idempotency trong service.

Evidence là immutable theo bộ `(incident_id, evidence_type, source_uri, checksum)`. M2
thu `PIPELINE_METADATA`, `LOG_EXCERPT` và `COMMIT_DIFF`; M3 bổ sung
`DATA_QUALITY_REPORT`. `PipelineReport` được lưu theo run trước khi event `FAILED` tạo
Incident và idempotent theo `(pipeline_run_id, report_type, checksum)`. Excerpt tối đa 20.000 ký tự, log query
tối đa 100 document và GitHub diff tối đa 20 file/750 ký tự patch mỗi file. Collector
redact lại evidence trước khi commit dù log ingestion đã redact trước đó.

### RCA và recovery

```text
RCAReport
├── incident_id
├── incident_type
├── root_cause
├── confidence
├── evidence_ids[]
├── model_name
├── prompt_version
└── created_at

RecoveryPlan
├── incident_id
├── proposed_action
├── parameters
├── risk_level
├── policy_decision
└── approval_status

RecoveryAttempt
├── plan_id
├── external_run_id
├── attempt_number
├── status
└── idempotency_key
```

## 4.2 Internal event chung

Mọi provider payload được chuẩn hóa:

```json
{
  "event_id": "github:delivery-123",
  "event_type": "pipeline.completed",
  "occurred_at": "2026-08-26T10:15:00Z",
  "provider": "github",
  "project_ref": "org/data-pipeline",
  "external_run_id": "875421",
  "attempt": 1,
  "commit_sha": "a51e092",
  "branch": "main",
  "status": "FAILED",
  "failed_stage": "data-quality",
  "links": {
    "run": "provider-specific-url",
    "logs": "provider-specific-url"
  }
}
```

Core service chỉ xử lý schema này, không xử lý payload gốc của GitHub/GitLab/Jenkins.

## 4.3 API bên ngoài dự kiến

### Webhook endpoints

```http
POST /api/v1/webhooks/github
POST /api/v1/webhooks/gitlab
POST /api/v1/webhooks/jenkins
```

Mỗi endpoint phải:

- Xác minh chữ ký hoặc shared secret.
- Lưu delivery ID để chống duplicate.
- Trả `202 Accepted` sau khi validate.
- Không chạy RCA đồng bộ trong request.

### CI callback endpoints

```http
POST /api/v1/runs
POST /api/v1/runs/{run_id}/stages
POST /api/v1/runs/{run_id}/logs
POST /api/v1/runs/{run_id}/reports/data-quality
POST /api/v1/runs/{run_id}/reports/anomaly
POST /api/v1/runs/{run_id}/artifacts
POST /api/v1/runs/{run_id}/complete
```

Callback API bổ sung dữ liệu domain-specific mà webhook provider không có, ví dụ Data Quality report và verification output.

Endpoint Data Quality report đã được triển khai ở M3, nhận contract `1.x`, tối đa 50 check
và payload canonical tối đa 100.000 byte. Summary và trạng thái tổng phải khớp kết quả từng
check; report được redact trước khi lưu và retry cùng checksum trả `duplicate: true`.

### Incident endpoints

```http
GET  /api/v1/incidents
GET  /api/v1/incidents/{incident_id}
POST /api/v1/incidents/{incident_id}/collect-evidence
GET  /api/v1/incidents/{incident_id}/evidence
```

Bốn endpoint trên đã được triển khai. Response Incident chứa `pipeline_run` lồng bên trong;
Evidence response chứa citation/checksum/provenance. Collect/read Evidence yêu cầu Bearer
token. Collector giữ partial bundle và trả warning có cấu trúc khi Elasticsearch/GitHub
không sẵn sàng. Các command endpoint dưới đây thuộc milestone RCA/Recovery:

```http
POST /api/v1/incidents/{incident_id}/analyze
POST /api/v1/incidents/{incident_id}/plans/{plan_id}/approve
POST /api/v1/incidents/{incident_id}/plans/{plan_id}/reject
POST /api/v1/incidents/{incident_id}/escalate
```

### Read APIs

```http
GET /api/v1/projects/{project_id}/runs
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/timeline
GET /api/v1/runs/{run_id}/logs
GET /api/v1/incidents/{incident_id}/audit
```

## 4.4 RCA output contract

```json
{
  "incident_type": "SCHEMA_DRIFT",
  "root_cause": "customer_id was renamed to user_id",
  "confidence": 0.94,
  "evidence": [
    {
      "evidence_id": "EVD-101",
      "claim": "Data Quality schema validation failed"
    },
    {
      "evidence_id": "EVD-105",
      "claim": "The latest commit changed the field mapping"
    }
  ],
  "recommended_action": {
    "type": "ROLLBACK_IMAGE",
    "parameters": {
      "target_image": "data-pipeline:9fd8210"
    }
  },
  "missing_information": []
}
```

Nếu không có evidence ID hoặc thiếu thông tin bắt buộc, report không được chuyển thẳng sang auto-recovery.

## 4.5 Idempotency và correlation

- Webhook: deduplicate bằng provider delivery ID.
- Run: unique theo integration, external run ID và attempt.
- Report: client gửi `Idempotency-Key`.
- Recovery: hash của incident, action, target và attempt.
- Mọi log/report phải chứa `run_id`, `project_id`, `commit_sha` và `job_name` nếu có.

## 4.6 Versioning

- API version trong URL: `/api/v1`.
- Internal event có `schema_version` khi triển khai.
- RCA report lưu model, embedding model, prompt và policy version.
- Provider adapter công bố capability version để core xử lý tương thích.

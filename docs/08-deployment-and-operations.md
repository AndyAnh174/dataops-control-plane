# 8. Triển khai và vận hành

## 8.1 Môi trường phát triển

Giai đoạn đầu nên dùng Docker Compose trên một máy:

```text
dataops-api
dataops-worker
redis
postgresql
minio
elasticsearch
kibana
ollama
```

Pipeline mẫu và verification có thể chạy bằng Docker trước khi chuyển sang K3s.

## 8.2 Môi trường demo ba server

| Server | Thành phần | Vai trò |
|---|---|---|
| A — CI/CD | GitHub self-hosted runner hoặc Jenkins, Docker | Test, build, push image, callback |
| B — Runtime | K3s, Pipeline Job, Verification Job, Prometheus | Chạy và xác minh pipeline |
| C — DataOps | API, worker, DB, ELK, MinIO, Ollama, Agent | Control plane, RCA và recovery |

## 8.3 Kết nối GitHub Actions

### Lựa chọn A — self-hosted runner trong LAN

Runner gọi được DataOps bằng IP nội bộ. Cách này phù hợp demo và không bắt buộc public DataOps API cho callback từ workflow.

Hạn chế: webhook trực tiếp từ GitHub.com vẫn cần endpoint truy cập được từ Internet. Có thể dùng callback `if: always()` và polling GitHub API trong MVP, nhưng sẽ kém tin cậy nếu runner bị mất hoàn toàn.

### Lựa chọn B — public HTTPS endpoint

DataOps webhook receiver được đặt sau reverse proxy, TLS và authentication. Đây là hướng phù hợp hơn nếu muốn tích hợp GitHub/GitLab cloud một cách hoàn chỉnh.

Chỉ public các endpoint ingestion cần thiết; database, Elasticsearch, MinIO, Redis và Ollama không được public.
Pipeline event và log endpoint phải xác thực Bearer token riêng cho agent; token được lưu
trong CI secret store và so sánh constant-time tại Control Plane.

## 8.4 Cấu hình project

Repository ứng dụng khai báo pipeline portable trong `dataops.yaml` và gọi
`AndyAnh174/dataops-agent@v0`. Hai biến tối thiểu ở GitHub Actions:

```env
DATAOPS_URL=http://dataops.internal:8000
DATAOPS_TOKEN=<stored-in-ci-secret>
```

Agent tự đọc project, commit SHA, branch, run ID, attempt và job từ GitHub. Với CI provider
khác, truyền bộ biến portable `DATAOPS_PROVIDER`, `DATAOPS_PROJECT_REF`,
`DATAOPS_EXTERNAL_RUN_ID`, `DATAOPS_ATTEMPT`, `DATAOPS_COMMIT_SHA`, `DATAOPS_BRANCH` và
`DATAOPS_JOB_NAME`.

Evidence Collector dùng GitHub Commit API để lấy diff. Repository public có thể chạy
không token; production/private repository nên cấu hình credential chỉ đọc:

```env
DATAOPS_GITHUB_API_URL=https://api.github.com
DATAOPS_GITHUB_TOKEN=<read-only-contents-token>
```

Không dùng token có quyền ghi repository, workflow hoặc package. Provider lỗi không được
làm mất metadata/log evidence; collector ghi warning và tiếp tục với partial bundle.

Cấu hình LLM chỉ nằm trên DataOps server:

```env
DATAOPS_LLM_URL=http://ollama:11434
DATAOPS_LLM_MODEL=gemma4:e2b
DATAOPS_LLM_TIMEOUT_SECONDS=300
DATAOPS_LLM_CONTEXT_TOKENS=8192
DATAOPS_RCA_PROMPT_VERSION=rca-v1
DATAOPS_RCA_CONTEXT_MAX_CHARS=16000
DATAOPS_EMBEDDING_URL=http://ollama:11434
DATAOPS_EMBEDDING_MODEL=bge-m3:567m
DATAOPS_EMBEDDING_DIMENSIONS=1024
DATAOPS_EMBEDDING_TIMEOUT_SECONDS=60
```

Ollama phải bật embedding API. Không public trực tiếp cổng `11434`; chỉ Control Plane
được phép truy cập qua private network hoặc firewall allowlist. Không commit file secret
vào repository.

RCA request được xử lý tuần tự: một embedding query cho retrieval rồi một `/api/chat`
structured-output request. Không chạy nhiều incident đồng thời trên model server nhỏ nếu
chưa có queue/concurrency limit. Timeout không làm mất incident/evidence; report chỉ được
commit sau khi schema và citation validation hoàn tất.

## 8.5 Logging và correlation

MVP nhận log JSON qua FastAPI rồi ghi trực tiếp vào Elasticsearch bằng Bulk API.
Logstash chưa bắt buộc trong luồng này vì Control Plane đã validate, chuẩn hóa và
redact dữ liệu. Có thể thêm Elastic Agent hoặc Logstash sau cho log hệ điều hành,
container hoặc nguồn cần parsing/routing phức tạp.

Mọi log ứng dụng cần có các field:

```json
{
  "project_id": "transaction-pipeline",
  "run_id": "RUN-2026-00125",
  "external_run_id": "875421",
  "commit_sha": "a51e092",
  "job_name": "data-quality",
  "level": "ERROR",
  "message": "Schema validation failed"
}
```

Log index và RAG index phải tách riêng. Thiết lập retention cho log và checksum cho artifact.

Triển khai hiện tại dùng:

- Data Stream versioned: `logs-dataops.pipeline-v1`.
- Alias đọc/ghi: `logs-dataops.pipeline`.
- Data Stream Lifecycle: retention mặc định `30d`.
- `message` và `stack_trace`: `match_only_text`.
- `metadata`: `flattened` để tránh mapping explosion.
- Document ID: hash ổn định để callback retry không tạo log trùng.

Knowledge index dùng mapping `dense_vector` 1024 chiều với cosine similarity. Alias
`knowledge-dataops` trỏ tới index versioned `knowledge-dataops-v1`; `_source` loại trường
embedding để API không tải/trả vector không cần thiết. Nếu đổi model hoặc số chiều, phải
tạo index version mới và re-index, không sửa mapping hiện tại tại chỗ.

Elasticsearch và Kibana không được public trực tiếp. Cấu hình tắt Elastic Security
trong `compose.yaml` chỉ dành cho local development và được bind vào `127.0.0.1`.
Production phải bật TLS, xác minh CA và dùng API key hoặc basic authentication từ
secret runtime.

## 8.6 Security checklist

- Xác minh webhook signature.
- Token của CI lưu trong secret store.
- Provider credential có scope đọc log/diff và trigger đúng project.
- Kubernetes service account giới hạn namespace và verb.
- Không cấp cluster-admin.
- Redact secret/PII trước khi gửi context cho Ollama.
- Mã hóa kết nối giữa các server khi không ở mạng tin cậy.
- Audit mọi approval và recovery action.
- Giới hạn kích thước upload và loại file report.
- Backup PostgreSQL, MinIO và cấu hình Elasticsearch.

## 8.7 Metrics vận hành

- `dataops_webhook_total`.
- `dataops_webhook_duplicate_total`.
- `dataops_run_duration_seconds`.
- `dataops_incident_total`.
- `dataops_rca_duration_seconds`.
- `dataops_recovery_attempt_total`.
- `dataops_recovery_success_total`.
- `dataops_policy_denied_total`.
- `dataops_happy_path_overhead_seconds`.

## 8.8 Failure handling nội bộ

- DataOps API mất tạm thời không được làm CI sai kết quả; callback cần retry có giới hạn.
- Queue job dùng retry với exponential backoff và dead-letter handling.
- Provider rate limit phải được theo dõi.
- RCA timeout chuyển incident sang trạng thái cần xử lý, không retry vô hạn.
- Elasticsearch hoặc Ollama lỗi không được làm mất incident metadata trong PostgreSQL.
- Recovery Executor chỉ đánh dấu thành công khi provider trả reference và Verifier xác nhận.

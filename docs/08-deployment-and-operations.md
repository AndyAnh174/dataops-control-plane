# 8. Triển khai, đóng gói và vận hành

## 8.1 Mục tiêu phân phối

DataOps Platform được cung cấp như một sản phẩm self-hosted, nhưng không ép mọi process
vào một container:

```text
Một bộ cài Docker Compose
├── andyanh174/dataops-platform:<version>
│   ├── FastAPI JSON API
│   └── Web UI HTML/CSS/JavaScript
├── postgres:<pinned-version>
├── elasticsearch:<pinned-version>
└── kibana:<pinned-version>       # optional/operator only
```

Image của dự án chứa code Python, templates, static assets và migration tooling. PostgreSQL,
Elasticsearch và Kibana dùng image chính thức riêng để có healthcheck, volume, resource
limit và vòng đời nâng cấp độc lập. Ollama có thể chạy ở máy khác và chỉ được cấu hình bằng
endpoint; không đóng model hàng GB vào image Platform.

DataOps Agent không chạy thường trực trong stack này. Agent được provider tải theo version
và chạy trong GitHub Actions/GitLab/Jenkins runner.

## 8.2 Trạng thái hiện tại và kiến trúc đích

Hiện tại repository đã có:

- image backend FastAPI multi-architecture được build/scan và publish lên GHCR;
- Compose cho FastAPI, PostgreSQL, Elasticsearch và Kibana;
- healthcheck, volume và loopback binding cho API/Elastic/Kibana;
- Agent repository riêng cho GitHub Actions;
- M1–M6: ingestion, evidence, retrieval, RCA, policy, recovery và verification.

Chưa được xem là hoàn thành:

- Web UI, session auth, workspace/project và RBAC;
- integration token theo project thay cho token dùng chung;
- migration/backup/upgrade workflow hoàn chỉnh;
- image `dataops-platform` chứa Web UI trên Docker Hub;
- production Compose bật TLS/authentication cho Elasticsearch.

Việc ghi rõ hai trạng thái tránh nhầm thiết kế trong tài liệu với tính năng đã chạy.

## 8.3 Golden path cho người dùng self-hosted

Trải nghiệm cài đặt đích:

```text
Tải compose.yaml và .env.example của một release
→ cấu hình public URL, database secret, instance key và Ollama endpoint
→ docker compose pull
→ docker compose up -d
→ chờ healthcheck
→ mở Web UI
→ bootstrap owner
→ tạo workspace/project/token
→ tích hợp repository
```

Người dùng không cần clone source hoặc cài Python. `docker compose up -d` kéo Platform từ
Docker Hub cùng các dependency image đã pin.

Production không dùng `latest`; pin SemVer, commit SHA hoặc digest. `latest` chỉ để trải
nghiệm nhanh và không được tự động cập nhật một deployment đang chạy.

## 8.4 Phát triển local hiện tại

Backend hiện tại có thể chạy trực tiếp:

```powershell
uv sync --group dev
uv run fastapi dev
```

Hoặc chạy Compose từ source:

```powershell
$env:DATAOPS_POSTGRES_PASSWORD = "choose-a-local-development-secret"
$env:DATAOPS_AGENT_TOKEN = "choose-a-random-agent-bearer-token"
docker compose up --build
```

Các port hiện tại:

- FastAPI: `127.0.0.1:8000`;
- Elasticsearch: `127.0.0.1:9201`;
- Kibana: `127.0.0.1:5602`.

Elasticsearch và Kibana tắt security trong Compose hiện tại nên chỉ dành cho local/demo và
được bind loopback. Không copy cấu hình đó sang production public.

## 8.5 Kết nối GitHub Actions

Repository ứng dụng khai báo pipeline portable trong `dataops.yaml` và gọi
`AndyAnh174/dataops-agent@v0`. Hai biến tối thiểu:

```env
DATAOPS_URL=https://dataops.example.com
DATAOPS_TOKEN=<stored-in-github-secret>
```

Agent tự đọc repository, commit SHA, branch, run ID, attempt và job từ GitHub. Với provider
khác, truyền bộ metadata portable tương ứng. Agent giữ nguyên exit code stage; lỗi gửi
telemetry mặc định không biến một pipeline thành công thành thất bại.

MVP hiện dùng `DATAOPS_AGENT_TOKEN` cấp instance. Trước multi-workspace, Web Platform phải
sinh token theo project/integration, chỉ lưu hash, hỗ trợ expiry/rotate/revoke và derive
project context từ token sau xác thực.

Evidence Collector dùng GitHub API để đọc commit diff. Credential đọc và credential
recovery phải tách scope:

```text
DATAOPS_GITHUB_TOKEN             # Contents: read
DATAOPS_GITHUB_RECOVERY_TOKEN    # Actions: write trên repo được phép
```

Về lâu dài ưu tiên GitHub App installation thay PAT. Credential không được commit, trả về
browser hoặc đưa vào context LLM.

## 8.6 Public và private network

```text
Internet
   │ HTTPS :443
   ▼
Reverse proxy
   ├── /                 → FastAPI Web UI
   ├── /api/v1/*         → JSON/Agent API
   └── /webhooks/*       → provider webhook

Docker/private network
   ├── PostgreSQL :5432
   ├── Elasticsearch :9200
   ├── Kibana :5601      # optional, operator access only
   └── Ollama :11434     # private/external allowlist only
```

Chỉ port 80/443 của reverse proxy được public. Database, search engine, Kibana và Ollama
không được NAT trực tiếp ra Internet. Health endpoint có thể dùng cho readiness nhưng không
trả dependency detail hoặc secret.

## 8.7 Cấu hình Platform

Cấu hình LLM hiện tại:

```env
DATAOPS_LLM_URL=http://192.168.1.80:11434
DATAOPS_LLM_MODEL=gemma4:e2b
DATAOPS_LLM_TIMEOUT_SECONDS=300
DATAOPS_LLM_CONTEXT_TOKENS=8192
DATAOPS_RCA_PROMPT_VERSION=rca-v1
DATAOPS_RCA_CONTEXT_MAX_CHARS=16000
DATAOPS_EMBEDDING_URL=http://192.168.1.80:11434
DATAOPS_EMBEDDING_MODEL=bge-m3:567m
DATAOPS_EMBEDDING_DIMENSIONS=1024
DATAOPS_EMBEDDING_TIMEOUT_SECONDS=60
```

Model công bố context lớn hơn không có nghĩa phải gửi toàn bộ log. Evidence budget mặc định
8K–16K giúp giảm độ trễ và tải VPS. RCA hiện gọi tuần tự một embedding query và một
structured LLM request; concurrency với model server nhỏ phải giới hạn, không bắn nhiều
incident cùng lúc.

Web Platform sẽ bổ sung các secret runtime:

```text
DATAOPS_PUBLIC_URL
DATAOPS_SESSION_SECRET
DATAOPS_ENCRYPTION_KEY
DATAOPS_BOOTSTRAP_ADMIN_*        # chỉ dùng bootstrap một lần
```

`.env` chỉ phù hợp local/demo. Production dùng Docker secrets hoặc secret manager của môi
trường triển khai; image và Git không chứa giá trị thật. Bootstrap credential phải được vô
hiệu hóa sau khi owner đầu tiên được tạo.

## 8.8 Logging, Elasticsearch và correlation

MVP nhận log JSON qua FastAPI rồi ghi trực tiếp vào Elasticsearch Bulk API. Logstash không
bắt buộc vì Agent/Control Plane đã validate, chuẩn hóa và redact; có thể thêm Elastic Agent
hoặc Logstash cho host/container log cần parsing phức tạp.

Mọi log phải được correlation bằng:

```json
{
  "workspace_id": "workspace-id",
  "project_id": "transaction-pipeline",
  "run_id": "RUN-2026-00125",
  "external_run_id": "875421",
  "commit_sha": "a51e092",
  "job_name": "data-quality",
  "level": "ERROR",
  "message": "Schema validation failed"
}
```

Log index và RAG index tách riêng:

- `logs-dataops.pipeline-v1` với alias `logs-dataops.pipeline`, retention mặc định `30d`;
- `knowledge-dataops-v1` với alias `knowledge-dataops`, dense vector 1024 chiều;
- raw log chỉ keyword/filter, không embedding từng dòng;
- document ID/checksum ổn định để retry không tạo bản sao.

Kibana phục vụ operator/debug, còn user xem run/log/incident qua Web UI để giữ đúng tenant
boundary và không cần quyền truy cập Elasticsearch trực tiếp.

## 8.9 Health, restart và resource limits

Mỗi service phải có healthcheck và `restart: unless-stopped`. Platform chỉ nhận traffic sau
khi PostgreSQL/Elasticsearch cần thiết đã healthy. Container chạy non-root và filesystem
runtime chỉ ghi vào volume/thư mục được cấp.

Elasticsearch và Ollama tiêu thụ nhiều RAM nhất. Compose cần memory limit phù hợp; với VPS
nhỏ, không khởi động Kibana khi không dùng và giữ RCA concurrency bằng 1. OOM/restart của
Ollama hoặc Elasticsearch không được làm mất incident metadata trong PostgreSQL.

## 8.10 Release lên Docker Hub

Pipeline release đích:

```text
lint + unit/integration tests
→ build multi-stage/non-root image
→ vulnerability scan
→ build linux/amd64 + linux/arm64
→ generate SBOM/provenance
→ push immutable tags to Docker Hub
→ deploy staging with exact digest
→ smoke test health, login, ingest and run detail
→ publish release notes + compose + rollback instructions
```

Tag dự kiến:

```text
andyanh174/dataops-platform:1.0.0
andyanh174/dataops-platform:1.0
andyanh174/dataops-platform:sha-<full-commit-sha>
```

Không dùng chung Docker Hub credential cá nhân ở runtime. CI push bằng repository token có
quyền tối thiểu và secret của GitHub Actions.

## 8.11 Backup, upgrade và rollback

Trước upgrade:

1. Pin image version/digest mới và đọc migration note.
2. Backup PostgreSQL; snapshot Elasticsearch khi log/knowledge cần giữ.
3. Kiểm tra dung lượng volume và health hiện tại.
4. Triển khai staging/demo trước production.

Rollback application:

```text
đổi DATAOPS_IMAGE_TAG/digest về bản ổn định trước
→ docker compose pull
→ docker compose up -d
→ xác minh /health, login, ingest và đọc run
```

Nếu release có migration không tương thích ngược, rollback app chưa đủ; phải dùng kế hoạch
restore database đã ghi trong release note. Không xóa volume để rollback.

## 8.12 Security checklist

- TLS ở reverse proxy và secure session cookie cho Web UI.
- CSRF protection cho request browser thay đổi trạng thái.
- Rate limit/brute-force protection cho login, callback và webhook.
- Provider webhook signature và replay protection.
- Integration token theo project, lưu hash, rotate/revoke và audit.
- Provider credential mã hóa, scope tối thiểu và tách read/write.
- Redact secret/PII trước lưu evidence hoặc gửi Ollama.
- Elasticsearch/PostgreSQL/Ollama không public.
- Container non-root, dependency/image scan và version pin.
- Backup được kiểm thử restore; approval/recovery luôn có audit trail.

## 8.13 Failure handling nội bộ

- Platform mất tạm thời không làm sai kết quả stage; Agent retry có giới hạn.
- Event và recovery request idempotent; không dispatch lặp khi client retry.
- Provider rate limit được ghi nhận và backoff.
- RCA timeout chuyển incident sang action required, không retry vô hạn.
- Elasticsearch/Ollama lỗi không làm mất incident metadata trong PostgreSQL.
- Recovery chỉ thành công sau verification callback hợp lệ, không phải sau dispatch.

Chi tiết onboarding và mô hình UI xem
[Web Platform, onboarding và phân phối self-hosted](12-web-platform-and-onboarding.md).

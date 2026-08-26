# 2. Kiến trúc hệ thống

## 2.1 Nguyên tắc kiến trúc

1. **Provider-neutral core:** GitHub, GitLab và Jenkins chỉ xuất hiện trong adapter.
2. **Event-driven:** webhook/callback được chuẩn hóa thành internal event và đưa vào queue.
3. **Async by default:** CI không phải chờ LLM phân tích.
4. **Capability-based:** mỗi provider khai báo khả năng thay vì bị ép có cùng tính năng.
5. **Safety boundary:** AI Agent lập kế hoạch; Policy Engine quyết định hành động có được phép; Executor mới thực thi.
6. **Verification required:** recovery chưa được xem là thành công nếu chưa chạy verification.

## 2.2 Các tầng hệ thống

```mermaid
flowchart TB
    subgraph External[External systems]
        GH[GitHub Actions]
        GL[GitLab CI]
        JK[Jenkins]
        KR[Kubernetes]
    end

    subgraph Ingestion[Integration layer]
        WH[Webhook Receiver]
        API[CI Callback API]
        PA[Provider Adapter Registry]
    end

    subgraph Core[DataOps control plane]
        RUN[Run Manager]
        INC[Incident Manager]
        EV[Evidence Collector]
        RCA[RCA Orchestrator]
        POL[Policy Engine]
        REC[Recovery Executor]
        VER[Verification Manager]
        AUD[Audit Service]
    end

    subgraph Intelligence[Intelligence layer]
        RET[Hybrid Retriever]
        LG[LangGraph Agent]
        OL[Ollama]
    end

    subgraph Storage[Storage and observability]
        PG[(PostgreSQL)]
        ES[(Elasticsearch)]
        MI[(MinIO)]
        RD[(Redis)]
        PM[Prometheus]
    end

    External --> Ingestion
    Ingestion --> Core
    Core --> Intelligence
    Core --> Storage
    Intelligence --> Storage
    REC --> PA
    PA --> External
```

## 2.3 Thành phần

### Webhook Receiver

- Xác minh chữ ký webhook.
- Chống replay và ghi nhận `delivery_id`.
- Chuyển payload riêng của provider thành `NormalizedPipelineEvent`.
- Trả phản hồi nhanh rồi đẩy xử lý vào queue.

### Run Manager

- Tạo/cập nhật `PipelineRun` và `StageRun`.
- Liên kết `external_run_id` với `run_id` nội bộ.
- Quản lý state transition hợp lệ.
- Chống tạo dữ liệu trùng.

### Incident Manager

- Tạo incident từ pipeline failure, Data Quality failure hoặc anomaly.
- Gom nhiều event liên quan vào cùng một incident.
- Quản lý trạng thái từ `DETECTED` đến `RESOLVED` hoặc `ESCALATED`.

### Evidence Collector

Thu thập có mục tiêu:

- Failed job log và stack trace.
- Git diff của commit gây lỗi.
- Data Quality/anomaly report.
- Image digest, version và deployment metadata.
- Metrics gần thời điểm xảy ra lỗi.
- Runbook và incident tương tự.

Evidence Collector không tải toàn bộ repository hoặc embedding toàn bộ log nếu không cần thiết.

### Hybrid Retriever

- BM25 cho error code, tên cột, function, job và exact term.
- Vector search cho runbook, incident summary và code chunk tương tự.
- Metadata filter theo project, provider, pipeline, error type và thời gian.
- Kết hợp kết quả bằng Reciprocal Rank Fusion (RRF).

### RCA Orchestrator

LangGraph điều phối các bước thu thập, truy xuất, kiểm tra đủ bằng chứng và gọi LLM. Output phải tuân theo schema thay vì văn bản tự do.

### Policy Engine

- Là thành phần deterministic, không phải LLM.
- So khớp loại incident, độ tin cậy, môi trường và requested action.
- Xác định `AUTO_APPROVED`, `REQUIRE_APPROVAL` hoặc `DENIED`.

### Recovery Executor

Thực hiện hành động thông qua provider adapter, không gọi trực tiếp SDK cụ thể từ core. Mọi execution đều có idempotency key, timeout và số lần retry giới hạn.

### Verification Manager

- Trigger workflow/job kiểm chứng.
- Đọc test và Data Quality result.
- So sánh lỗi trước/sau recovery.
- Chỉ đóng incident khi tiêu chí pass.

## 2.4 Phân tách dữ liệu

| Loại dữ liệu | Nơi lưu | Ghi chú |
|---|---|---|
| Run, incident, plan, audit | PostgreSQL | Dữ liệu quan hệ và trạng thái |
| Log tìm kiếm | Elasticsearch | Có retention riêng |
| Runbook, incident summary, code chunk vector | Elasticsearch | Index tách biệt với log |
| Report/file/artifact | MinIO | Tham chiếu bằng URI và checksum |
| Queue/cache/lock | Redis | Không phải nguồn dữ liệu lâu dài |
| Metrics | Prometheus | MTTD, MTTR, latency, resource |

Không vector hóa từng dòng log. Chỉ tạo embedding cho incident summary, runbook và code chunk đã được chọn lọc.

## 2.5 Topology ba server

```text
Server A — CI/CD
├── GitHub self-hosted runner hoặc Jenkins
├── Docker
└── CI integration client

Server B — Runtime
├── K3s/Kubernetes
├── Pipeline Job
├── Verification Job
├── Prometheus/Grafana
└── Elastic Agent

Server C — DataOps
├── FastAPI
├── Celery Worker + Redis
├── PostgreSQL
├── MinIO
├── Elasticsearch/Kibana
├── Ollama + LangGraph
└── Policy/Recovery services
```

Trong môi trường phát triển, có thể chạy phần lớn service bằng Docker Compose trước khi chuyển sang topology ba server.

## 2.6 Ranh giới tin cậy

- Payload bên ngoài luôn phải được xác thực và validate schema.
- Log có thể chứa secret hoặc dữ liệu cá nhân; phải redact trước khi đưa vào LLM.
- LLM không được nhận token CI/CD, Kubernetes credential hoặc database password.
- Recovery Executor dùng credential tách riêng và quyền tối thiểu.
- Raw data bất biến; pipeline ghi vào staging và chỉ publish sau verification.
- Tất cả quyết định, approval và hành động được ghi audit log.

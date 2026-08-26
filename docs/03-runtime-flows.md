# 3. Luồng hoạt động

## 3.1 Điều kiện trước khi chạy

Một project phải được đăng ký với:

- `project_id` nội bộ.
- Repository và provider.
- Workflow/pipeline được theo dõi.
- Credential hoặc GitHub App installation có quyền tối thiểu.
- Data Quality command và verification command.
- Image registry/repository.
- Recovery policy áp dụng cho project.

## 3.2 Push code và pipeline thành công

```mermaid
sequenceDiagram
    actor Dev as Developer
    participant Git as GitHub
    participant CI as GitHub Actions
    participant DO as DataOps API
    participant Reg as Docker Registry
    participant K8s as K3s/Kubernetes

    Dev->>Git: git push
    Git->>CI: Trigger workflow
    Git->>DO: workflow started webhook
    DO-->>Git: 202 Accepted
    DO->>DO: Create/Upsert PipelineRun
    CI->>CI: Unit test + Data Quality
    CI->>Reg: Push image tagged by commit SHA
    CI->>DO: Submit reports and image metadata
    CI->>K8s: Deploy/trigger Pipeline Job
    K8s->>DO: Pipeline result
    K8s->>K8s: Verification Job
    K8s->>DO: Verification passed
    DO->>DO: Mark run SUCCESS
```

Happy path không gọi AI Agent. Webhook và callback chỉ thêm overhead nhỏ; report lớn được upload trực tiếp vào object storage hoặc xử lý bất đồng bộ.

## 3.3 Pipeline thất bại

```mermaid
sequenceDiagram
    participant CI as CI Provider
    participant DO as DataOps API
    participant Q as Worker Queue
    participant EC as Evidence Collector
    participant RAG as Hybrid RAG
    participant AI as RCA Agent
    participant PE as Policy Engine
    participant RE as Recovery Executor
    participant V as Verifier

    CI->>DO: FAILED event
    DO-->>CI: 202 Accepted
    DO->>DO: Create incident
    DO->>Q: analyze(incident_id)
    Q->>EC: Collect evidence
    EC->>CI: Fetch failed job log and commit diff
    EC->>RAG: Search runbooks and similar incidents
    RAG-->>AI: Ranked evidence
    AI-->>PE: Structured RCA + recovery proposal
    PE-->>RE: Approved action or approval required
    RE->>CI: Retry/trigger rollback workflow
    CI->>V: Run recovery and verification
    V-->>DO: Verification result
    DO->>DO: Resolve or escalate incident
```

## 3.4 State machine của pipeline run

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> RUNNING
    RUNNING --> SUCCESS
    RUNNING --> FAILED
    RUNNING --> CANCELED
    FAILED --> RECOVERY_TRIGGERED
    RECOVERY_TRIGGERED --> VERIFYING
    VERIFYING --> RECOVERED
    VERIFYING --> FAILED
```

## 3.5 State machine của incident

```mermaid
stateDiagram-v2
    [*] --> OPEN
    OPEN --> COLLECTING_EVIDENCE
    COLLECTING_EVIDENCE --> ANALYZING
    ANALYZING --> ACTION_REQUIRED
    ANALYZING --> RESOLVED
    ACTION_REQUIRED --> RESOLVED
```

M1 đã triển khai việc tạo idempotent Incident ở trạng thái `OPEN`. Các transition còn
lại do Evidence/RCA/Recovery service điều khiển; không client nào được tự ý ghi trực tiếp
trạng thái Incident. M2 đã triển khai `OPEN → COLLECTING_EVIDENCE → ANALYZING` khi có
failed-stage logs, hoặc `ACTION_REQUIRED` khi log storage lỗi/không có log phù hợp.

## 3.6 Ví dụ schema drift

1. Developer đổi `customer_id` thành `user_id` nhưng chưa sửa downstream mapping.
2. GitHub Actions chạy Data Quality test và phát hiện schema mismatch.
3. DataOps nhận `FAILED`, tạo incident và lấy đúng job log.
4. Evidence Collector lấy Git diff của commit và report schema.
5. Hybrid RAG tìm runbook “schema drift” và incident tương tự.
6. Agent tạo RCA với bằng chứng rằng field đã bị rename.
7. Policy Engine cho phép `QUARANTINE` hoặc `ROLLBACK_IMAGE`; `CREATE_PR` cần phê duyệt.
8. Executor trigger recovery workflow dùng image ổn định trước đó.
9. Verification Job kiểm tra schema, record count và output.
10. Incident chỉ được đóng nếu verification pass.

## 3.7 Retry và chống lặp vô hạn

- Mỗi recovery plan có `max_attempts`.
- Cùng một action và cùng một target version dùng chung idempotency key.
- Lỗi không đổi sau một lần retry phải được nâng mức xử lý.
- Agent không tự tạo kế hoạch mới vô hạn.
- Incident thiếu bằng chứng hoặc có confidence thấp được chuyển sang `ESCALATED`.

## 3.8 Khi nào CI phải chờ?

CI chỉ chờ các quality gate bắt buộc như unit test, Data Quality và verification trước publish. CI không chờ RCA Agent. Khi lỗi xảy ra, DataOps trả `202 Accepted`, phân tích ở background và trigger một recovery run riêng.

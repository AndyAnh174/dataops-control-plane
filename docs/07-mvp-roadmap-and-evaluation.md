# 7. MVP, đánh giá và lộ trình

## 7.1 MVP demo cuối cùng

Demo mục tiêu:

```text
Push một commit gây lỗi
→ GitHub Actions chạy
→ Data Quality fail
→ DataOps tạo incident
→ Agent lấy log + diff + runbook
→ sinh RCA đúng
→ Policy cho rollback
→ recovery workflow chạy image trước
→ verification pass
→ incident RESOLVED
```

## 7.2 Các giai đoạn triển khai

### Giai đoạn 1 — Control plane cơ bản

- FastAPI project.
- PostgreSQL schema.
- Project, integration và pipeline run.
- Webhook/callback ingestion.
- Idempotency và audit event.

### Giai đoạn 2 — GitHub Actions adapter

- GitHub webhook validation.
- Normalize workflow/job event.
- Reusable workflow/composite action.
- Đọc run, job log và commit diff.

### Giai đoạn 3 — Pipeline mẫu

- Batch dữ liệu mẫu bằng Pandas. ✅ M3
- Great Expectations checks cho schema drift, null, duplicate, range và volume. ✅ M3
- Report JSON versioned được Agent upload và Evidence Collector gắn citation. ✅ M3
- Isolation Forest cho anomaly scenario.
- Docker image version theo commit SHA.
- Raw → staging → verification → publish.

### Giai đoạn 4 — Observability và evidence

- Elasticsearch log index.
- MinIO artifact/report.
- Evidence Collector.
- Runbook và incident knowledge base.

### Giai đoạn 5 — Agentic Hybrid RAG

- BM25 + vector retrieval + filter/RRF. ✅ M4
- LangGraph workflow.
- Structured RCA output.
- Evidence validation.

### Giai đoạn 6 — Recovery và verification

- Policy Engine.
- Retry, quarantine và rollback.
- Recovery attempt/idempotency.
- Verification Job và incident closure.

### Giai đoạn 7 — Chứng minh đa nền tảng

- Thêm GitLab CI hoặc Jenkins adapter tối thiểu.
- Chạy cùng một incident flow.
- Ghi nhận phần core không cần thay đổi.

## 7.3 Bộ fault injection có ground truth

| Mã | Sự cố | Root cause chuẩn | Hành động mong đợi |
|---|---|---|---|
| F01 | Schema drift | Đổi `customer_id` thành `user_id` | Quarantine hoặc rollback |
| F02 | Missing values | Tỷ lệ null vượt ngưỡng | Pause publish + quarantine |
| F03 | Source timeout | Lỗi mạng tạm thời | Retry giới hạn |
| F04 | New image crash | Commit/image mới gây runtime error | Rollback image |
| F05 | Record-count anomaly | Dữ liệu đầu vào giảm bất thường | Pause publish + investigate |
| F06 | Resource exhaustion | Job vượt memory limit | Escalate hoặc retry theo policy |

Mỗi scenario phải có:

- Incident label.
- Expected root cause.
- Required evidence.
- Allowed/denied action.
- Expected verification result.

## 7.4 Baseline so sánh

1. Rule-based classifier.
2. LLM chỉ đọc raw log.
3. LLM + Hybrid RAG nhưng không Agent workflow.
4. Agentic Hybrid RAG đầy đủ.

So sánh giúp chứng minh giá trị của retrieval, tool use và evidence workflow thay vì chỉ báo cáo kết quả một mô hình.

## 7.5 Metrics

### Chất lượng phân tích

- Incident classification accuracy/F1.
- Root-cause accuracy.
- Evidence recall@k.
- Tỷ lệ RCA có đủ required evidence.
- Unsupported-claim rate.

### Recovery và an toàn

- Recovery success rate.
- Action selection accuracy.
- Unsafe action rate — mục tiêu bằng 0.
- Tỷ lệ cần human approval.
- Số recovery attempt trung bình.

### Vận hành

- MTTD.
- MTTR.
- RCA latency.
- Happy-path overhead.
- Webhook duplicate rate và idempotency correctness.

## 7.6 Kỳ vọng về thời gian

- Happy path không gọi LLM nên overhead chủ yếu là webhook/callback và upload report.
- Failure path có thêm evidence retrieval, LLM inference và recovery run.
- RCA có thể tốn thêm vài chục giây đến vài phút nhưng thay thế quá trình đọc log thủ công lâu hơn.
- CI không chờ Agent; recovery được trigger dưới dạng run mới.

## 7.7 Definition of Done cho MVP

- Một repository GitHub tích hợp bằng template dùng lại.
- Ít nhất 5 scenario chạy lặp lại được.
- Tất cả run, incident, evidence, RCA và recovery được lưu/audit.
- Không có action nguy hiểm vượt qua Policy Engine.
- Demo được ít nhất retry, quarantine và rollback.
- Có bảng kết quả baseline/metrics.
- Tài liệu cài đặt và sequence flow khớp với hệ thống thực tế.

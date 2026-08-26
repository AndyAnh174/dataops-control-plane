# 9. Hybrid Retrieval M4

M4 cung cấp lớp truy xuất cho Agent nhưng chưa gọi LLM. Control Plane gửi văn bản đã
redact tới Ollama `bge-m3:567m`, lưu vector 1024 chiều cùng tài liệu đã kiểm duyệt trong
Elasticsearch, chạy BM25 và approximate kNN rồi hợp nhất kết quả bằng RRF.

## 9.1 Phạm vi dữ liệu

Các loại được chấp nhận là `RUNBOOK`, `INCIDENT_SUMMARY`, `POSTMORTEM` và `CODE_CHUNK`.
Raw pipeline log không đi vào knowledge index: Evidence Collector vẫn tìm log bằng
keyword, run/stage/level/time filter rồi mới tạo một incident summary tối đa 20.000 ký tự.

Mỗi tài liệu có ID ổn định từ `document_type + source_uri`; upload lại cùng nguồn là
upsert. Checksum phản ánh nội dung và metadata đã redact. Các filter hiện có:
`project_ref`, `document_types`, `provider`, `incident_type`, `environment` và
`created_after`.

Full text đã redact (tối đa 50.000 ký tự, riêng incident summary là 20.000) được giữ cho
BM25. Đầu vào dense embedding dùng excerpt head+tail tối đa 8.000 ký tự để không vượt
context của model trong khi `truncate=false`; marker cho biết phần giữa chỉ bị lược khỏi
embedding, không bị xóa khỏi tài liệu lưu trữ.

## 9.2 Luồng request

```text
validated request
→ redact secret
→ POST Ollama /api/embed (truncate=false)
→ validate đúng 1024 số hữu hạn
→ BM25 query + metadata filter
→ kNN cosine query + cùng metadata filter
→ application-layer RRF
→ top-k kèm rank và raw score từng nhánh
```

RRF dùng công thức `Σ 1 / (60 + rank)`, trong đó rank bắt đầu từ 1. Control Plane không
so sánh trực tiếp `_score` của BM25 với cosine score. Mỗi nhánh lấy
`min(max(top_k × 3, 10), 100)` ứng viên trước khi fusion.

## 9.3 API

- `POST /api/v1/retrieval/documents`: nạp/upsert một tài liệu.
- `POST /api/v1/retrieval/search`: hybrid search có typed filter.
- `POST /api/v1/incidents/{incident_id}/index-knowledge`: gộp evidence hiện có thành
  một `INCIDENT_SUMMARY` có citation rồi index.

Ba endpoint đều yêu cầu Bearer token nếu `DATAOPS_AGENT_TOKEN` được cấu hình. Khi Ollama
hoặc Elasticsearch lỗi, API trả `503` có thông báo giới hạn; incident và evidence trong
PostgreSQL không bị mất.

## 9.4 Cấu hình

```env
DATAOPS_EMBEDDING_URL=http://192.168.1.80:11434
DATAOPS_EMBEDDING_MODEL=bge-m3:567m
DATAOPS_EMBEDDING_DIMENSIONS=1024
DATAOPS_EMBEDDING_TIMEOUT_SECONDS=60
```

Không public Ollama hoặc Elasticsearch qua domain ứng dụng. Control Plane chỉ cần kết nối
outbound tới hai dịch vụ; M4 không yêu cầu mở thêm inbound port.

## 9.5 Kiểm thử chấp nhận

1. Nạp `runbooks/data-quality-amount-range.md` qua document API.
2. Index một incident có Data Quality evidence bằng incident API.
3. Tìm `amount exceeds accepted range` với đúng `project_ref`.
4. Xác nhận có cả runbook và incident summary; ít nhất một kết quả có
   `matched_by: [keyword, vector]`.
5. Tìm cùng query với project khác và xác nhận không có tài liệu bị lọt chéo.
6. Lặp lại bước nạp và xác nhận `result: updated` thay vì tạo bản sao.

Test tự động:

```powershell
uv run pytest tests/test_retrieval.py tests/test_ollama_embeddings.py
$env:DATAOPS_TEST_ELASTICSEARCH_URL = "http://127.0.0.1:9201"
uv run pytest tests/test_elasticsearch_knowledge.py
```

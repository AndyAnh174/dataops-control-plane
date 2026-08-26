# 10. Agentic RCA M5

M5 nối Evidence Collector và Hybrid Retrieval vào một LangGraph có giới hạn để sinh RCA
có cấu trúc. Model là `gemma4:e2b` qua Ollama; model không có credential provider và
không được thực thi recovery.

## 10.1 Graph và trạng thái

```text
START
→ load_context
→ evidence_gate
→ retrieve
→ generate
→ validate
→ persist RCA
→ END (Incident ACTION_REQUIRED)
```

- `load_context`: đọc run và evidence thuộc incident từ PostgreSQL.
- `evidence_gate`: yêu cầu `PIPELINE_METADATA` và ít nhất một diagnostic evidence như
  log, Data Quality report hoặc commit diff. Thiếu thì dừng trước retrieval/LLM.
- `retrieve`: tìm tối đa 5 runbook/incident cũ/postmortem/code chunk cùng project,
  provider và incident type; loại chính incident đang phân tích.
- `generate`: đúng một `POST /api/chat`, `stream=false`, temperature 0,
  `num_predict=1200` và JSON Schema.
- `validate`: Pydantic schema, citation allowlist, knowledge ID allowlist và human-approval
  rule. Chỉ report hợp lệ mới được commit.

## 10.2 Output contract

RCA report gồm:

- Incident type và root cause.
- Confidence từ 0 đến 1.
- Danh sách claim với `citation_id` thuộc evidence hiện tại.
- Knowledge document IDs thuộc đúng kết quả retrieval.
- Recommended action, rationale, typed parameters và human approval flag.
- Missing information.
- Model, embedding model, prompt version và input checksum.
- Số LLM calls, prompt/completion tokens, duration và graph trace.

Action thay đổi trạng thái phải yêu cầu human approval. M5 không coi recommended action là
policy decision và không gửi lệnh tới GitHub, Docker hay Kubernetes.

## 10.3 Prompt-injection boundary

Evidence và knowledge được đặt trong marker `UNTRUSTED_*`. System prompt yêu cầu không
làm theo chỉ dẫn nằm trong log, diff, runbook hoặc source code. Secret đã được redact từ
ingestion/retrieval; context tối đa mặc định 16.000 ký tự. Output chỉ được chấp nhận khi
mọi ID nằm trong allowlist do Control Plane tạo, không phải allowlist do model tự khai báo.

## 10.4 Idempotency

Input checksum gồm identity/version của run và checksum/citation của evidence. Unique key:

```text
(incident_id, input_checksum, model_name, prompt_version)
```

Retry không đổi input trả cùng report với `duplicate=true` và không gọi retrieval hoặc LLM
lần hai. Evidence mới tạo checksum mới và cho phép một report version mới; `GET .../rca`
trả version gần nhất.

## 10.5 API

```http
POST /api/v1/incidents/{incident_id}/analyze
GET  /api/v1/incidents/{incident_id}/rca
Authorization: Bearer ${DATAOPS_AGENT_TOKEN}
```

Failure contract:

- `409`: thiếu direct evidence.
- `422`: model output sai schema/citation/knowledge/approval rule.
- `503`: Ollama, embedding hoặc Elasticsearch tạm unavailable.
- Không trường hợp nào trên tạo partial RCA report.

## 10.6 Cấu hình

```env
DATAOPS_LLM_URL=http://192.168.1.80:11434
DATAOPS_LLM_MODEL=gemma4:e2b
DATAOPS_LLM_TIMEOUT_SECONDS=300
DATAOPS_RCA_PROMPT_VERSION=rca-v1
DATAOPS_RCA_CONTEXT_MAX_CHARS=16000
```

Không cần LangSmith hoặc API key cloud. LangGraph chỉ điều phối state machine nội bộ;
Ollama và Elasticsearch tiếp tục nằm trên private network.

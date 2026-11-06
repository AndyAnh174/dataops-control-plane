# OpenAI GPT-6 Luna và Voyage cho instance DataOps

Cấu hình được chọn:

| Vai trò | Provider / model | API |
|---|---|---|
| RCA có cấu trúc | OpenAI / gpt-6-luna | https://api.openai.com/v1/chat/completions |
| Embedding | Voyage / voyage-4-lite, 1024 chiều | https://api.voyageai.com/v1/embeddings |

Mục tiêu hiện tại là demo trên máy tính: dùng [hướng dẫn local](18-local-mvp-demo.md).
Các hướng dẫn Compose bên dưới là tùy chọn; không cần rollout VPS cho bài demo local.

RCA cần sinh báo cáo JSON nên dùng Chat Completions, khác phép thử phân loại bằng
Decisions trong benchmark trước đó. Benchmark lịch sử không bị đổi model hoặc dữ liệu.

## Cấu hình và khóa

compose.cloud.yaml chọn cố định hai provider/model/URL trên, ghi đè cả giá trị Ollama
còn lưu trong runtime cũ. Không fallback sang Ollama nếu cloud trả lỗi.

Hai biến của secret runtime là OPENAI_API_KEY và VOYAGE_API_KEY. Compose chuyển chúng
vào API container dưới tên DATAOPS_LLM_API_KEY và DATAOPS_EMBEDDING_API_KEY.
Không đưa project token DATAOPS_TOKEN vào hai biến này.

File deploy/cloud.env.example chỉ có tên biến rỗng. Bản chứa khóa thật phải nằm ngoài
Git checkout và chỉ có quyền đọc phù hợp cho người vận hành/runtime.

Sau khi nạp các biến PostgreSQL, instance token và hai khóa cloud vào môi trường shell,
chạy từ source đã có adapter Voyage:

    docker compose -f compose.yaml -f compose.cloud.yaml config --quiet
    docker compose -f compose.yaml -f compose.cloud.yaml up -d --build

Nếu dùng file secret runtime bên ngoài, thêm --env-file trước hai tham số -f.
Không dùng config không có --quiet trong terminal được ghi log, vì nó có thể in secret.

## Kiểm tra có giới hạn

Kiểm tra cấu hình không gọi API:

    docker compose -f compose.yaml -f compose.cloud.yaml exec -T api python -m dataops_control_plane.cloud_check

Kiểm tra thật gửi tối đa hai yêu cầu tổng hợp, tuần tự: một embedding và một RCA JSON.
Đây là lệnh có sử dụng API của tài khoản, chỉ chạy khi đã sẵn sàng:

    docker compose -f compose.yaml -f compose.cloud.yaml exec -T api python -m dataops_control_plane.cloud_check --live

Output chỉ có model, tình trạng khóa, kích thước vector, trạng thái validation và
metrics. Không in giá trị khóa, prompt hay raw response. Lệnh không tạo incident,
không gửi dữ liệu production và không execute recovery.

configured_not_live_verified chỉ xác nhận cấu hình. Chỉ live_passed mới xác nhận hai
request tổng hợp đạt. Sau đó vẫn cần kiểm tra một incident thật để nghiệm thu RAG/RCA.

## Structured output và lỗi

GPT-6 Luna dùng reasoning_effort=none, max_completion_tokens giới hạn theo cấu hình
và JSON Schema strict. Parameters của recovery được truyền dưới dạng JSON object
string trong wire schema rồi giải mã lại thành dict trước Pydantic.

Các validation ứng dụng, evidence citation/knowledge allowlist và policy vẫn được
áp dụng. Response bị cắt hoặc refusal không được lưu thành RCA hợp lệ.

Voyage dùng input_type=document khi nạp tài liệu, query khi tìm kiếm; output_dimension
là 1024, output_dtype=float và truncation=false. Vector sai số chiều, không hữu hạn
hoặc index thiếu/trùng bị từ chối trước khi tới Elasticsearch.

## Nạp lại knowledge

Namespace knowledge phụ thuộc provider, URL, model và dimension. Vì vậy vector BGE-M3
không bị trộn với Voyage dù cùng 1024 chiều. Index cũ được giữ để rollback.

Sau chuyển provider, nạp lại các runbook/tài liệu đã kiểm duyệt qua
POST /api/v1/retrieval/documents bằng session OWNER/OPERATOR. Có thể dùng hai runbook
trong thư mục runbooks/ làm dữ liệu khởi đầu cho project tương ứng.

Index-knowledge của incident là bước bổ sung; chính incident đang phân tích bị loại
khỏi retrieval để không xem bản tóm tắt của nó như bằng chứng độc lập.
Raw logs không được tự embedding từng dòng.

## Instance VPS hiện có

Giữ base Compose và runtime secret đang dùng. Chọn image bất biến chứa adapter mới,
sao lưu PostgreSQL và cấu hình trước rollout. Đưa compose.cloud.yaml vào cùng thư mục
base Compose, rồi dùng cả hai file trong các lệnh config/pull/up/ps.

Ví dụ thư mục hiện có: /opt/dataops-demo/control-plane. File cloud.env nằm ở đó,
ngoài source repository trên máy dev. Chỉ recreate API; giữ các data service và volume.

Tại thời điểm chuẩn bị thay đổi, container VPS vẫn là image sha-5575c6e..., model
qwen3.5:4b + bge-m3:567m và chưa có hai khóa cloud. Không đánh dấu rollout/live test
thành công chỉ vì source và test mock đã hoàn tất.

## Nguồn chính thức đã đối chiếu

- OpenAI GPT-6 Luna: https://developers.openai.com/api/docs/models/gpt-6-luna
- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Voyage embedding models/API: https://docs.voyageai.com/docs/embeddings

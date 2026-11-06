# Demo MVP trên máy tính: OpenAI + Voyage

Không cần VPS, public domain, tunnel hoặc GitHub-hosted runner cho bài demo này.
Agent chạy thật ở chế độ portable với provider=local. Control Plane chạy Python
trên máy; metadata lưu SQLite, log/knowledge dùng Elasticsearch local đang có.

## Cấu hình đã chọn

- LLM: OpenAI gpt-6-luna, Chat Completions + strict JSON, reasoning none.
- Embedding: Voyage voyage-4-lite, 1024 chiều, document/query đúng ngữ cảnh.
- Web: http://127.0.0.1:8000.
- Elasticsearch hiện có: http://127.0.0.1:9200.
- Database và artifact demo: thư mục dataops-demo-runtime bên cạnh repo.
- Các khóa API chỉ ở bộ nhớ process; helper không lưu chúng vào Git hoặc file.

Yêu cầu: Python 3.11+ có dependencies của Platform, Node.js 22+, Elasticsearch 9.4,
Internet và khóa OpenAI/Voyage có quyền sử dụng model.

Máy tác nghiệp đã có Node 22 và Python venv. Để tránh tải image/model lớn khi ổ C
ít trống, bài demo không build Docker API, không chạy thêm PostgreSQL/Kibana và
không tải Ollama. Elasticsearch đang có được giữ nguyên; DataOps dùng namespace riêng.

## Terminal 1: khởi động Platform

Trên máy hiện tại, dùng venv đã có:

    cd D:\HCMUTE\openai-voyage-20261106
    $Python = "D:\HCMUTE\tieuluan\.venv\Scripts\python.exe"
    & $Python demo/local/runner.py start --verify-cloud

Nhập hai khóa bằng prompt ẩn. Nếu đã đặt OPENAI_API_KEY/VOYAGE_API_KEY trong môi
trường process, helper dùng các biến đó. --verify-cloud gửi tối đa hai request
tổng hợp tuần tự, không gửi dữ liệu production. Nếu provider không đạt thì dừng.

Nếu bỏ --verify-cloud, app chỉ được xem là đã cấu hình; phải kiểm tra RCA thật
trước khi kết luận AI chạy được. Giữ Terminal 1 mở; Ctrl+C dừng API.

Trên máy khác, cài dependencies của repo trước hoặc chạy uv sync --frozen,
sau đó dùng Python của môi trường đó thay cho đường dẫn trên.

## Chrome: tạo owner, project và token

1. Mở http://127.0.0.1:8000/setup, tạo owner và workspace demo.
2. Projects → Add project:
   - Name: Local MVP
   - Provider: Local demo
   - Project reference: local/mvp-demo
   - Default branch: main
3. Create Agent token, copy secret được hiển thị một lần.
4. Không nhập OpenAI/Voyage key vào project token. Token project chỉ dùng cho Agent.

## Terminal 2: chạy Agent thật

    cd D:\HCMUTE\openai-voyage-20261106
    $Python = "D:\HCMUTE\tieuluan\.venv\Scripts\python.exe"
    & $Python demo/local/runner.py agent --scenario both

Nhập project token ở prompt ẩn. Helper tải bundle Agent đã pin commit và kiểm tra
SHA-256; không cần npm install. Mỗi scenario dùng thư mục và external run ID riêng.

- success: 3 dòng hợp lệ, Data Quality report đạt, stage publish tạo trusted-output.json.
- failure: customer_id=2 có amount=125000, vượt max=10000. Agent dừng tại data-quality,
  upload report trước FAILED, không chạy publish và Platform tạo incident.

Helper báo exit_code=1 cho pipeline lỗi dự kiến; helper tổng thể trả 0 khi cả hai
scenario diễn ra đúng. Đây là demo dữ liệu giả, không phải bộ benchmark model.

Agent không nhận khóa cloud/admin của process cha. Workflow add-mask được loại
khỏi terminal local và mọi lần xuất hiện của integration token được che.

## Terminal 3: evidence, RAG, RCA và policy

    cd D:\HCMUTE\openai-voyage-20261106
    $Python = "D:\HCMUTE\tieuluan\.venv\Scripts\python.exe"
    & $Python demo/local/runner.py analyze

Nhập email/mật khẩu OWNER hoặc OPERATOR của instance local. Helper chọn incident
mới nhất thuộc provider local + project_ref local/mvp-demo, thu evidence, index một
runbook đúng scope, gọi RCA rồi evaluate policy. Có thể chỉ định --incident-id
nếu muốn chọn một incident cụ thể.

Mở URL được in ra. Xác minh:

- analysis_status=VALIDATED, nguyên nhân phù hợp report/log;
- model=gpt-6-luna và embedding_model=voyage/voyage-4-lite;
- citation/knowledge ID hợp lệ và action có lý do;
- policy là REQUIRE_APPROVAL hoặc DENIED với reasons rõ ràng.

Helper không approve/execute recovery. Local recovery executor chưa được cấu hình.
Demo tại đây chứng minh Agent → log/report → incident → evidence → Voyage RAG →
OpenAI RCA → policy; không giả vờ đã phục hồi production.

## Dữ liệu và kiểm chứng

SQLite/artifact nằm ngoài repo, nên restart API không xóa metadata. Log/knowledge
nằm trong Elasticsearch local; giữ service đó chạy để xem lại.
Chỉ có khóa ở process API; cần nhập lại khi mở phiên mới. Owner/password không
được helper lưu.

Kiểm tra cục bộ đã dùng bundle Agent thật để chạy cả hai scenario qua API auth,
SQLite, report và incident. Các test đó không gọi OpenAI/Voyage thật; live test
chỉ được xác nhận sau khi người dùng nhập khóa và nhận kết quả thành công.

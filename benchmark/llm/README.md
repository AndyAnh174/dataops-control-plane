# Sáu chat LLM trên bộ log HCMUTE cũ

[Notebook](https://colab.research.google.com/drive/1kmcRYmPnBzjppg4jEHyBkD7K3htHm1dm)
và [protocol đã chốt](PROTOCOL.md). Link không cấp quyền public cho dữ liệu Drive.

Ứng viên: qwen3.5:9b, gemma4:12b, ornith-1.5:9b, gpt-6-luna, deepseek-flash,
gemini-3.5-flash-lite. Giữ nguyên 158 mẫu holdout đã che từ export 339 mẫu,
ba repeat block, seed 2026. Đây là phân loại mức log/build, không phải nhãn RCA.

Colab A100 80GB + High RAM; Ollama 0.40.14; một inference và một local model
tại mỗi thời điểm. Secrets: OPENAI_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY
(hoặc GOOGLE_API_KEY). Không in khóa, lưu vào source, hoặc đưa vào artifact/Git.

Đưa harness.py, run_logs.py, setup_runtime.py và protocol vào
`/content/hcmute_llm`. Bên dưới `data/` đặt cases.jsonl, dataset_report.json,
privacy_scan.json của bộ riêng được cấp quyền. Đọc khóa trong kernel Colab bằng
`google.colab.userdata.get`, giữ dictionary `keys` trong bộ nhớ, rồi chạy:

```python
import sys

sys.path.insert(0, "/content/hcmute_llm")
from setup_runtime import setup

setup("/content/hcmute_llm")
from run_logs import run

result = run(keys, "/content/hcmute_llm", repeats=3)
```

Resume cùng dataset/source/output không gọi lại case đã ghi; pending không được
âm thầm tính phí lại. Đổi prompt/source/dataset cần một output mới. Thiếu khóa
là unavailable, không biến thành điểm chất lượng bằng 0. Chỉ xếp hạng run đủ kế hoạch.

Xuất PNG 300 dpi/SVG/CSV/manifest bằng make_comparison.py ở thư mục benchmark:

```bash
python make_comparison.py --root /content/hcmute_llm --kind llm
python -m pytest -c benchmark/llm/pytest.ini benchmark/llm/tests -q
```

Kết quả case/dataset giữ riêng; chỉ publish aggregate đã rà soát. Monitor tài nguyên
tùy chọn không gọi cloud API. Lần chạy đầu bật monitor sau block Gemma đầu tiên,
nên không xem số đo là peak toàn bộ job. Nguồn phương pháp được trích dẫn trong protocol.

run_cloud.py tiếp tục các API còn thiếu bằng đúng engine/source/dataset đã đóng
băng. Source và dataset được kiểm tra hash trước khi resume. Nó hỗ trợ client có
hoặc không có GPU, không gọi model local, và ghi metadata thật của client. Lần chạy
này vẫn giữ A100 theo yêu cầu; Gemini thêm 4 giây trước HTTP vào nhịp 2 giây sau
response để phù hợp quota miễn phí. Thời gian chờ được tách khỏi HTTP latency.

# Embedding source code và bộ đọc cố định

[Notebook riêng](https://colab.research.google.com/drive/1Ej4PHYKErnou2JSvJrI_FheeBJs_sIW5)
và [protocol](PROTOCOL.md). Cấu hình A100 + High RAM; quota hiện cho một GPU nên
có thể chạy bằng notebook/kernel riêng trong cùng VM A100 đang giữ.

Tám cấu hình: BGE-M3 567M, Qwen3 Embedding 0.6B/8B, OpenAI text-embedding-3-small/large,
Voyage voyage-code-4, Gemini gemini-embedding-001/2. Có BM25 và oracle context.
Gemini 001 là baseline batch với CODE_RETRIEVAL_QUERY, không gọi là model mới nhất.
Gemini Embedding 2 dùng prefix code retrieval và gửi riêng từng đoạn để không trộn mẫu.
Vector dùng kích thước gốc; đây là so sánh cấu hình triển khai, không đồng nhất FLOPs.

Corpus: 49 file công khai, 166 cửa sổ 2000 ký tự/overlap 250. Hai Git commit được
ghim. 24 intent, mỗi intent có EN/VI: 48 query. Gold trỏ tới khai báo hàm trong source;
đã kiểm tra vị trí, chưa được người vận hành duyệt tính đầy đủ của nhãn.

```bash
python benchmark/embedding/prepare_corpus.py --plane /path/control-plane --agent /path/agent --out /private/code-data
```

Đưa run_retrieval.py, harness.py/setup_runtime.py của benchmark LLM, protocol và
ba file corpus.jsonl/queries.jsonl/manifest.json vào `/content/hcmute_embedding`.
Đọc OPENAI_API_KEY, VOYAGE_API_KEY, GEMINI_API_KEY từ Colab Secrets vào `keys`, rồi:

```python
import sys

sys.path.insert(0, "/content/hcmute_embedding")
from setup_runtime import setup

setup("/content/hcmute_embedding", context=16384)
from run_retrieval import run, run_readers

retrieval = run(keys, "/content/hcmute_embedding", repeats=3)
readers = run_readers(keys, "/content/hcmute_embedding")
```

Stage 1 dùng cosine chính xác, Recall@5/MRR@5/nDCG@5, EN/VI riêng, p50/p95,
token usage, thời gian index và kích thước vector. Stage 2 giữ GPT-6 Luna chat và
d3:9b Decision cố định để chọn passage trong cùng top 5. Chấm tìm đúng khai báo,
không chấm tự động độ đúng của code-QA tự do. Lưu câu trả lời ngắn để kiểm tra thủ công.

```bash
python make_comparison.py --root /content/hcmute_embedding --kind embedding
python -m pytest -c benchmark/embedding/pytest.ini benchmark/embedding/tests -q
```

Đổi chunk/model/prompt/data sau khi xem điểm là thí nghiệm khác. Không âm thầm
retry, đổi model hay bỏ API lỗi để làm đẹp điểm. Nguồn và giới hạn nằm trong protocol.

Trong lần chạy này, user giữ runtime A100 hiện tại. shared_runtime.py tạo một
notebook riêng, chạy bằng nbclient/IPython kernel trên cùng VM, rồi xuất notebook
đã thực thi cùng ZIP kết quả. Cloud LLM và embedding có thể chồng thời gian; mỗi
study tuần tự và chỉ một model local được nạp. Khóa chỉ truyền qua bộ nhớ/env của
tiến trình, không ghi vào notebook hoặc artifact. Mô hình D3 dùng cùng trọng số
d3:9b qua tag cấu hình context 16k; embeddings dùng context 4096.

# Benchmark của DataOps Control Plane

- [Decision model benchmark](decision/README.md): harness native Decision API, số đo tổng hợp, biểu đồ và sơ đồ
- [Chat LLM benchmark](llm/README.md): sáu LLM trên cùng holdout log đã chốt
- [Code embedding benchmark](embedding/README.md): tám cấu hình embedding, BM25 và hai bộ đọc cố định

Các bộ log riêng và khóa truy cập được quản lý ngoài source công khai.

Sơ đồ phương pháp (không chứa điểm model):

```bash
python benchmark/make_method_diagram.py --out /private/report/figures
```

Sau khi tải hai ZIP đã hoàn tất, giải nén riêng rồi xuất aggregate và báo cáo:

```bash
python benchmark/summarize_studies.py --llm /private/llm --embedding /private/embedding --out /private/report
python benchmark/export_report_pdf.py --source /private/report/BAO-CAO-SO-SANH-LLM-EMBEDDING.md --out /private/report/BAO-CAO-SO-SANH-LLM-EMBEDDING.pdf --figures /private/report/figures/benchmark-method.png
```

Vẽ hình cần NumPy/Matplotlib; notebook dùng nbclient/nbformat/ipykernel nếu chạy
kernel riêng; xuất PDF cần ReportLab và font Arial hoặc DejaVu Sans. Phiên bản
thực tế của lần xuất hình được lưu trong manifest, không suy từ máy người đọc.

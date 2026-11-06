# Hình benchmark để đưa vào báo cáo

_Xuất từ số liệu tổng hợp của lượt chạy HCMUTE ngày 06/11/2026._

`figures/all_charts.pdf` gồm tám trang hình vector, có thể dùng để trình bày. Mỗi hình còn có bản PDF riêng, SVG và PNG 300 dpi. Source và hash đầu vào nằm trong `make_figures.py`, `results/hcmute-20261106.json` và `figures/manifest.json`.

| Hình | Nội dung |
|---|---|
| 01 | Thanh xếp hạng và heatmap F1 prod/dev/CI |
| 02 | Độ trễ p50/p95 sau warmup |
| 03 | Chất lượng theo độ trễ p50 |
| 04 | Brier và AUROC đối với nhãn proxy |
| 05 | ASR chèn chỉ dẫn, kèm tử số/mẫu số |
| 06 | VRAM peak quan sát của ba model Ollama |
| 07 | Luồng dữ liệu, giữ riêng nhãn và thực nghiệm |
| 08 | Thiết kế sáu cặp đối chứng/chèn chỉ dẫn |

Tập chính có 158 mẫu và ba lượt/model, không phải 474 mẫu độc lập. Nhãn lấy từ mức log hoặc build status; dataset chưa có nhãn incident/action được con người xác nhận. Probe chỉ có sáu cặp/model. Các đồ thị không biểu diễn khoảng tin cậy hay hóa đơn chi phí.

Trong Colab, chạy `python render_colab.py` để cài phụ thuộc vẽ vào thư mục riêng, render lại và tạo ZIP. Lệnh chỉ tạo hình, không gọi model. `figures/render_environment.json` ghi môi trường xuất. Dữ liệu đo gốc và source benchmark được giải thích đầy đủ trong README của repo.

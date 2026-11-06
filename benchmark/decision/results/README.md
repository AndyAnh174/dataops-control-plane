# Dữ liệu tổng hợp đã đo

`hcmute-20261106.json` chứa thống kê model, metric từng môi trường/lượt, probe, thông số runtime và model digest. `comparison_summary.csv` là bảng phẳng để kiểm tra.

Các file này chỉ gồm số đo tổng hợp; không có nội dung log, prompt từng case, đường dẫn nguồn, case ID, salt hoặc API key. Chúng được xuất từ 2.370 dự đoán chính và 60 dự đoán probe đã lưu riêng. Hash archive đầy đủ nằm ở trường `provenance` để người được cấp quyền đối chiếu bản đo.

Trường metric cũ `independent_groups` đếm cụm nguồn kỹ thuật sau liên kết template, không xác nhận các incident độc lập về mặt thống kê.

Script vẽ không suy diễn khoảng tin cậy từ ba lượt lặp phụ thuộc, không gán VRAM bằng 0 cho API và không chuyển thời gian chạy thành hóa đơn.

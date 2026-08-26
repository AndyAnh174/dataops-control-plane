# 1. Tổng quan dự án

## 1.1 Bài toán

Khi một pipeline dữ liệu thất bại, kỹ sư thường phải tự tìm job log, kiểm tra commit mới, đối chiếu Data Quality report, xác định phiên bản triển khai, đọc runbook, chọn cách retry hoặc rollback, rồi chạy lại pipeline để xác minh. Quy trình này tốn thời gian, phụ thuộc kinh nghiệm cá nhân và khác nhau giữa GitHub Actions, GitLab CI và Jenkins.

Dự án giải quyết bài toán đó bằng một control plane chung:

```text
Pipeline lỗi
→ phát hiện incident
→ thu thập bằng chứng
→ phân tích nguyên nhân
→ lập kế hoạch phục hồi
→ kiểm soát an toàn
→ thực thi
→ xác minh
```

## 1.2 Mục đích

Xây dựng một nền tảng DataOps có thể tích hợp với nhiều CI/CD provider và môi trường chạy pipeline dữ liệu. Hệ thống quản lý vòng đời pipeline, hỗ trợ RCA bằng AI Agent, áp dụng policy trước khi recovery và lưu audit trail đầy đủ.

Giá trị chính của đề tài không nằm ở việc “dùng LLM đọc log”, mà ở vòng lặp khép kín:

> **Phát hiện → Thu thập bằng chứng → RCA → Phục hồi có kiểm soát → Kiểm chứng.**

## 1.3 Mục tiêu chức năng

- Tiếp nhận trạng thái workflow/job từ nhiều CI/CD provider.
- Chuẩn hóa dữ liệu provider thành domain model chung.
- Liên kết commit, job, image, report, log và incident bằng một `run_id` nội bộ.
- Thu thập Data Quality report, anomaly report, log và deployment metadata.
- Tạo incident khi pipeline thất bại hoặc chất lượng dữ liệu vượt ngưỡng.
- Dùng Hybrid RAG để truy xuất bằng chứng liên quan.
- Sinh RCA report có cấu trúc và trích dẫn bằng chứng.
- Kiểm tra recovery plan bằng luật xác định trước.
- Hỗ trợ retry, quarantine, pause publish, rollback image và tạo pull request.
- Chạy verification sau recovery và cập nhật trạng thái incident.
- Cung cấp audit log và metrics phục vụ đánh giá.

## 1.4 Mục tiêu phi chức năng

- **Đa nền tảng:** phần lõi không import SDK riêng của provider.
- **Không chặn happy path:** việc ghi nhận sự kiện và RCA được xử lý bất đồng bộ.
- **Idempotent:** webhook hoặc callback gửi lặp không tạo run/incident trùng.
- **Evidence-first:** Agent chỉ được kết luận từ bằng chứng đã thu thập.
- **An toàn mặc định:** hành động chưa được policy cho phép phải bị chặn.
- **Có thể kiểm chứng:** mọi RCA và recovery đều lưu input, output, phiên bản model và kết quả verification.
- **Least privilege:** adapter chỉ nhận quyền cần thiết cho từng capability.

## 1.5 Đối tượng sử dụng

- Data Engineer theo dõi và xử lý pipeline dữ liệu.
- DevOps/Platform Engineer quản lý CI/CD và môi trường triển khai.
- Nhóm phát triển muốn tích hợp DataOps bằng workflow template dùng lại.
- Giảng viên/người đánh giá cần xem bằng chứng thực nghiệm về RCA và recovery.

## 1.6 Phạm vi MVP

MVP bao gồm:

- Một pipeline batch mẫu được đóng gói Docker.
- GitHub Actions là CI/CD provider đầu tiên.
- GitHub webhook hoặc callback từ reusable workflow.
- PostgreSQL, MinIO và Elasticsearch.
- Great Expectations cho Data Quality.
- Isolation Forest cho một kịch bản anomaly có nhãn.
- LangGraph + Ollama cho evidence collection và RCA.
- Policy Engine dạng rule-based.
- Ba recovery action chính: `RETRY`, `QUARANTINE`, `ROLLBACK`.
- Verification Job sau recovery.
- Từ 5 đến 6 fault-injection scenario có ground truth.

## 1.7 Ngoài phạm vi MVP

- Streaming bằng Kafka.
- Apache Airflow.
- Deep Learning/Autoencoder/LSTM.
- Multi-agent.
- Tự merge pull request.
- Tự sửa hoặc xóa dữ liệu production.
- Kubernetes multi-cluster.
- Giao diện web lớn.
- Tích hợp đầy đủ tất cả CI/CD provider ngay trong phiên bản đầu.

## 1.8 Tiêu chí thành công

- Tích hợp repository mới mà không sửa core platform.
- Nhận diện đúng run và không tạo trùng khi webhook bị gửi lại.
- Agent tạo được RCA có bằng chứng cho bộ incident chuẩn.
- Không có recovery action ngoài danh sách policy cho phép.
- Recovery thành công được xác minh bằng Data Quality test.
- Đo được MTTD, MTTR, RCA accuracy, recovery success rate và happy-path overhead.
- Thêm provider thứ hai chỉ bằng adapter và cấu hình, không sửa workflow của Agent.

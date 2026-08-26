# 5. Provider adapters

## 5.1 Mục tiêu

Provider adapter cô lập mọi khác biệt giữa GitHub Actions, GitLab CI và Jenkins. Core platform chỉ sử dụng interface và internal event chung.

```text
Core DataOps
    ↓ interface
Provider Registry
    ├── GitHub Actions Adapter
    ├── GitLab CI Adapter
    ├── Jenkins Adapter
    └── Kubernetes Adapter
```

## 5.2 Capability model

Không giả định provider nào cũng hỗ trợ mọi thao tác. Adapter khai báo capability:

```text
READ_RUN
READ_JOB_LOG
READ_COMMIT_DIFF
RETRY_JOB
RERUN_PIPELINE
TRIGGER_PIPELINE
CREATE_PULL_REQUEST
CANCEL_RUN
READ_DEPLOYMENT
```

Policy Engine chỉ đề xuất action nếu integration có capability tương ứng.

## 5.3 Interface khái niệm

```python
class CiProviderAdapter(Protocol):
    def capabilities(self) -> set[str]: ...
    def normalize_webhook(self, headers, payload) -> NormalizedPipelineEvent: ...
    def get_run(self, external_run_id: str) -> ProviderRun: ...
    def get_failed_job_logs(self, external_run_id: str) -> list[LogArtifact]: ...
    def get_commit_diff(self, base_sha: str, head_sha: str) -> CommitDiff: ...
    def retry_job(self, job_id: str, idempotency_key: str) -> ActionResult: ...
    def trigger_pipeline(self, request: TriggerRequest) -> ActionResult: ...
    def create_pull_request(self, request: PullRequestRequest) -> ActionResult: ...
```

Đây là contract định hướng, không phải code triển khai cuối cùng.

## 5.4 GitHub Actions — provider đầu tiên

### Kênh tích hợp

1. **GitHub webhook/GitHub App:** nhận workflow/job status và metadata.
2. **Reusable workflow hoặc composite action:** gửi Data Quality, anomaly và verification report.
3. **GitHub API:** lấy job log, commit diff và trigger rerun.

Kết hợp webhook và CI callback giúp hệ thống vừa nhận được trạng thái tin cậy, vừa nhận dữ liệu đặc thù của pipeline.

### Workflow tối thiểu

```yaml
name: data-pipeline

on:
  push:
    branches: [main]

jobs:
  test-build:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - name: Run tests and quality checks
        run: ./scripts/ci.sh
      - name: Build image
        run: docker build -t "$IMAGE_NAME:${GITHUB_SHA}" .
      - name: Notify DataOps
        if: always()
        run: ./scripts/notify-dataops.sh
```

Workflow thật nên dùng reusable workflow/action có version thay vì sao chép script vào từng repository.

### Vì sao dùng cả webhook và `if: always()`?

- Webhook phát hiện trạng thái workflow kể cả khi một job thất bại sớm.
- Callback gửi được Data Quality report và metadata riêng.
- Nếu DataOps endpoint tạm lỗi, callback có thể retry mà không làm sai trạng thái chính.

## 5.5 GitLab CI

Adapter tương lai sử dụng:

- Pipeline/job webhook.
- GitLab API để đọc trace, diff và retry job.
- CI template dùng chung qua `include`.
- Project access token hoặc OAuth với quyền tối thiểu.

## 5.6 Jenkins

Adapter tương lai sử dụng:

- Plugin hoặc post-build webhook.
- Jenkins REST API để đọc build/job log.
- Jenkins Shared Library để gửi domain report.
- Crumb/token handling và callback có chữ ký.

## 5.7 Kubernetes adapter

Kubernetes không phải CI provider nhưng là execution provider:

- Tạo Pipeline Job và Verification Job.
- Đọc Job/Pod status và log.
- Rollback về image digest đã được phê duyệt.
- Không cấp `cluster-admin`.
- Chỉ cho phép thao tác trong namespace và resource được cấu hình.

## 5.8 Chứng minh tính đa nền tảng

MVP chỉ cần GitHub Actions hoạt động hoàn chỉnh. Sau đó thêm một vertical slice nhỏ cho GitLab CI hoặc Jenkins:

1. Nhận normalized event.
2. Tạo cùng domain `PipelineRun`.
3. Lấy failed log qua adapter.
4. Trigger một recovery action.
5. Chứng minh core Agent, Policy và database không thay đổi.

Đây là bằng chứng kỹ thuật rõ hơn việc cố triển khai nửa vời cả ba provider ngay từ đầu.

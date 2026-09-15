# Báo cáo triển khai và đánh giá DataOps end-to-end

Ngày thực hiện: 2026-09-15 (UTC+7)

## 1. Kết luận điều hành

Release quản lý thành viên workspace đã được triển khai thành công lên môi trường demo public.
Control Plane, PostgreSQL, Elasticsearch, HTTPS và ứng dụng demo đều healthy sau rollout. Một
pipeline thành công và một pipeline lỗi có kiểm soát đã được chạy qua `dataops-agent@v0`.

Kết quả tổng thể:

- **Deploy Control Plane: PASS** — image bất biến, backup trước deploy, health check và rollback
  sẵn sàng.
- **Thu thập telemetry: PASS** — event, log và Data Quality report được correlation đúng theo run.
- **Chặn release lỗi: PASS** — Data Quality failure tạo Incident và không thay đổi revision đang chạy.
- **Evidence + embedding: PASS** — thu thập 4 evidence, tạo knowledge document bằng
  `bge-m3:567m`.
- **LLM RCA: FAIL** — `gemma4:e2b` trả kết quả sau khoảng 176 giây nhưng output bị API từ chối
  bằng HTTP 422; Incident bị giữ ở `ANALYZING` và không có RCA report.

Release hiện phù hợp để demo luồng CI/CD, quan sát pipeline và phát hiện sự cố. Phần RCA bằng LLM
chưa đủ ổn định để coi là production-ready.

## 2. Phạm vi release

| Thành phần | Phiên bản |
| --- | --- |
| Feature + security image | `5575c6e31bdc62c65bbba3a07bd0bf62f9f24577` |
| Deployment config commit | `628201bff7e31b8632ba01b4a5cc815109aa294a` |
| Runtime image | `ghcr.io/andyanh174/dataops-control-plane:sha-5575c6e31bdc62c65bbba3a07bd0bf62f9f24577` |
| Previous image | `ghcr.io/andyanh174/dataops-control-plane:sha-20a3015d1fefe057e99bed3d784d8e1533158a11` |
| Public endpoint | `https://dataops-console.andyanh.id.vn` |

Thay đổi chính gồm quản lý thành viên workspace theo vai trò `OWNER`, `OPERATOR`, `VIEWER`, không
có signup công khai, giới hạn tạo project theo vai trò, và vá hai CVE High của `libpcre2-8-0`.

CI release và CI deployment config đều thành công:

- https://github.com/AndyAnh174/dataops-control-plane/actions/runs/34731871674
- https://github.com/AndyAnh174/dataops-control-plane/actions/runs/34995242051

## 3. An toàn triển khai

### 3.1 Backup và baseline

PostgreSQL được backup trước rollout:

- File: `/opt/dataops-demo/backups/dataops-pre-5575c6e-20260915T162801Z.sql.gz`
- Kích thước: 18.778 bytes
- SHA-256: `1f98a256000f3395397926f363347819e692df15c91dadc7d60cd16b756bf2bd`

Baseline dữ liệu trước deploy:

| Bảng logic | Số lượng |
| --- | ---: |
| Users | 1 |
| Workspaces | 1 |
| Memberships | 1 |
| Projects | 1 |
| Pipeline runs | 18 |
| Incidents | 5 |

Các số liệu không đổi ngay sau rollout. Không có volume PostgreSQL hoặc Elasticsearch nào bị tạo
lại hay xóa.

### 3.2 Rollout

- Compose config được validate trước khi thay file đang chạy.
- Chỉ service API được pull và recreate; PostgreSQL, Elasticsearch và Kibana giữ nguyên.
- Container mới đạt `healthy` ở lần kiểm tra thứ ba, khoảng 10 giây sau khi start.
- Cấu hình trước release được lưu tại
  `/opt/dataops-demo/control-plane/compose.yaml.pre-5575c6e-20260915T162903Z`.
- Cơ chế rollback tự động không phải kích hoạt.

### 3.3 Kiểm tra sau deploy

| Kiểm tra | Kết quả |
| --- | --- |
| Internal Control Plane health | HTTP 200 |
| Public HTTPS health | HTTP 200, 0,368 giây, TLS verify thành công |
| Login page | HTTP 200 |
| Members API khi chưa đăng nhập | HTTP 401, route và auth boundary hoạt động |
| Log API sau rollout | Không có traceback, exception hoặc error |
| Trivy | 0 High, 0 Critical |
| Automated tests | 96 passed, Elasticsearch integration passed |

Image local có kích thước khoảng 146,2 MiB. GHCR đã phát hành image `amd64` và `arm64`. Job Docker
Hub được skip vì repository chưa có `DOCKERHUB_USERNAME`, `DOCKERHUB_NAMESPACE` và
`DOCKERHUB_TOKEN`.

## 4. Bài test pipeline thành công

GitHub run:
https://github.com/AndyAnh174/dataops-demo-app/actions/runs/34995816200

| Chỉ số | Kết quả |
| --- | ---: |
| Tổng thời gian job | 9 phút 38 giây |
| Setup Node.js/cache | 2 phút 42 giây |
| DataOps Agent step | 6 phút 35 giây |
| Lifecycle events | 2 |
| Data Quality reports | 1 |
| Elasticsearch logs | 813 |
| Secret-like values redacted | 0 |
| Run status | `SUCCESS` |
| New incidents | 0 |

Thời gian stage được ước lượng từ thời điểm hoàn tất từng batch log:

| Stage | Thời gian xấp xỉ |
| --- | ---: |
| data-quality | 18,5 giây |
| backend-quality | 7,0 giây |
| frontend-quality | 84,8 giây |
| build-images | 115,0 giây |
| scan-images | 104,9 giây |
| registry-login | 2,0 giây |
| publish-images | 21,0 giây |
| deploy | 41,3 giây |

Completion event được Control Plane nhận khoảng 0,17 giây sau log hoàn tất deploy. Ứng dụng public
trả HTTP 200 trong 0,256 giây và chạy đúng revision
`12a629152eb53e2142609c58e18dd85c44847926`.

## 5. Bài test sự cố có kiểm soát

GitHub run:
https://github.com/AndyAnh174/dataops-demo-app/actions/runs/34997172237

Kịch bản `null_rate` được tiêm vào Data Quality stage.

| Chỉ số | Kết quả |
| --- | ---: |
| Tổng thời gian job | 5 phút 8 giây |
| Setup Node.js/cache | 4 phút 20 giây |
| DataOps Agent đến lúc phát hiện lỗi | 30 giây |
| Failed stage | `data-quality` |
| Lifecycle events | 2 |
| Data Quality reports | 1 |
| Elasticsearch logs | 56 |
| Incident | 1, trạng thái ban đầu `OPEN` |
| Evidence | 4, không warning, không duplicate |

Pipeline dừng ngay tại Data Quality stage. Stage build, publish và deploy không chạy. Revision ứng
dụng demo trước sự cố vẫn healthy, chứng minh release lỗi không lọt vào runtime.

## 6. Đánh giá AI/RAG

### 6.1 Phần đạt

- Evidence Collector gom đủ pipeline metadata, failed-stage log, GitHub diff và Data Quality report.
- `bge-m3:567m` tạo `INCIDENT_SUMMARY` knowledge document trong khoảng 60 giây.
- Knowledge document có checksum, không phát hiện dữ liệu cần redact.
- Control Plane giới hạn một request LLM tại một thời điểm trong phép thử.

### 6.2 Phần chưa đạt

- `gemma4:e2b` mất khoảng 176 giây rồi trả output không vượt qua lớp structured-output/schema
  validation; API trả HTTP 422.
- Không có RCA report được lưu.
- Incident vẫn ở trạng thái `ANALYZING`, gây hiểu nhầm rằng tác vụ còn chạy.
- Client kiểm thử không lưu response body của HTTP 422, nên chỉ xác nhận được nhóm lỗi
  `LLMResponseInvalid`/`RCAValidationError`, chưa phân biệt được JSON sai, schema sai hay citation
  không hợp lệ.

Đây là lỗi cần xử lý trước khi bật RCA tự động cho production. Không nên tăng timeout hoặc retry mù,
vì vấn đề là chất lượng output chứ không phải mất kết nối.

## 7. Đánh giá định lượng

| Hạng mục | Điểm | Nhận xét |
| --- | ---: | --- |
| Deployment safety | 9/10 | Backup, immutable tag, health gate, rollback rõ ràng |
| Telemetry/correlation | 9/10 | Event, report và log khớp chính xác theo run |
| Failure containment | 9/10 | Pipeline lỗi không được build/publish/deploy |
| Web/auth foundation | 8/10 | Role model và auth boundary tốt; cần UAT UI bằng nhiều tài khoản |
| AI RCA reliability | 4/10 | Evidence/RAG đạt nhưng structured RCA thất bại |
| Performance | 6/10 | Setup Node/cache và image build/scan chiếm phần lớn thời gian |
| Operability | 7/10 | Dashboard, ELK và rollback có; thiếu trạng thái `ANALYSIS_FAILED` |

Đánh giá chung: **7,4/10 — đạt mức demo kỹ thuật, chưa đạt production cho AI RCA**.

## 8. Hành động đề xuất

### P0 — trước demo RCA tiếp theo

1. Khi LLM trả 422, chuyển Incident từ `ANALYZING` sang `ACTION_REQUIRED` hoặc
   `ANALYSIS_FAILED`; lưu failure category, model, duration và graph node thất bại.
2. Cho phép tối đa một lượt repair có kiểm soát: đưa lỗi validation về model và yêu cầu sửa JSON;
   không retry vô hạn.
3. Bổ sung test với output thực tế của `gemma4:e2b`, gồm JSON sai schema, citation không tồn tại và
   knowledge ID không hợp lệ.
4. Capture response body an toàn của HTTP 4xx trong công cụ vận hành, không lưu prompt/raw secret.

### P1 — hiệu năng và quan sát

1. Benchmark bỏ GitHub npm cache trên self-hosted runner. Cache restore hiện mất 162–260 giây và
   chiếm khoảng 84% thời gian của ca lỗi nhanh.
2. Ghi event `stage.started`/`stage.completed` riêng. Hiện tất cả dòng log của một stage dùng chung
   timestamp lúc batch được gửi, nên không đo được timeline bên trong stage.
3. Tối ưu Docker layer/cache; dọn uv cache sau `uv sync` và đo lại image/pull time.
4. Khi cần gần zero-downtime, chạy hai API instance và chuyển proxy sau readiness thay vì recreate
   một instance.

### P1 — bảo mật vận hành

1. Thu hồi PAT GitHub từng được chia sẻ trong hội thoại và thay bằng credential mới hoặc GitHub App
   có quyền tối thiểu.
2. Di chuyển runtime secret khỏi file `.env` sang secret manager khi chuyển khỏi môi trường demo.

### P2 — phát hành

1. Cấu hình Docker Hub variables/secret rồi chạy lại publish job nếu cần phân phối ngoài GHCR.
2. Thực hiện UAT UI với ba tài khoản OWNER/OPERATOR/VIEWER cho create/update/remove member và
   project deletion.

## 9. Rollback

Nếu cần rollback Control Plane:

1. Khôi phục file compose đã backup hoặc đổi API image về
   `ghcr.io/andyanh174/dataops-control-plane:sha-20a3015d1fefe057e99bed3d784d8e1533158a11`.
2. Validate Compose config.
3. Recreate duy nhất API với `--no-deps --pull never` nếu image cũ còn local.
4. Kiểm tra internal health, public HTTPS health và đối chiếu số lượng dữ liệu.
5. Chỉ restore PostgreSQL từ backup nếu có bằng chứng dữ liệu bị thay đổi; rollout này không tạo
   migration và hiện không cần restore database.

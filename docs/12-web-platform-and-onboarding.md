# 12. Web Platform, onboarding và phân phối self-hosted

## 12.1 Hai thành phần của sản phẩm

DataOps được phát hành thành hai thành phần có vòng đời độc lập:

```text
Provider side                         Self-hosted DataOps server
┌──────────────────────┐             ┌──────────────────────────────┐
│ DataOps Agent        │  events     │ dataops-platform             │
│                      ├────────────►│                              │
│ GitHub Actions (MVP) │  logs       │ FastAPI API + Web UI         │
│ GitLab/Jenkins later │  reports    │ PostgreSQL + Elasticsearch   │
└──────────────────────┘             └──────────────┬───────────────┘
                                                   │ provider API
                                                   └──────────────► CI/CD
```

- **DataOps Agent** chạy trong CI/CD provider. Agent thực thi pipeline portable trong
  `dataops.yaml`, giữ nguyên exit code và gửi dữ liệu đã chuẩn hóa về Platform.
- **DataOps Platform** là sản phẩm self-hosted. Một ứng dụng FastAPI phục vụ cả JSON API
  và Web UI; PostgreSQL và Elasticsearch chạy như dependency riêng trong Docker Compose.
- **Provider adapter** nằm trong Platform, dùng credential có scope phù hợp để đọc evidence,
  trigger pipeline hoặc thực hiện recovery. Adapter không phải là Agent.

DataOps không thay thế GitHub Actions, GitLab CI hay Jenkins. Provider/runner vẫn là nơi
checkout source, test, build và deploy; DataOps bổ sung vòng quan sát, phân tích, quyết định,
phục hồi và xác minh.

Trạng thái M7 foundation hiện đã triển khai bootstrap/session, workspace/project, project token,
GitHub onboarding sinh hai file cấu hình, run/incident detail và recovery approval/audit. Member
invitation, provider credential record, token rotation và settings UI vẫn thuộc các vòng sau.

## 12.2 Vì sao dùng FastAPI-only

Phiên bản Web đầu tiên dùng FastAPI, Jinja2, HTML/CSS và JavaScript tối thiểu. Không thêm
Express chỉ để chuyển tiếp request tới API vì việc đó tạo thêm runtime, service-to-service
authentication và hai nơi cần theo dõi lỗi nhưng chưa tạo giá trị tương xứng.

FastAPI chịu trách nhiệm:

- JSON API cho Agent và provider webhook;
- session/authentication và RBAC cho trình duyệt;
- workspace, project, integration và token;
- pipeline run, log, incident, RCA, approval và recovery;
- render HTML và phục vụ static assets.

UI phải sử dụng cùng các application service với API, không truy cập database trực tiếp.
Các JSON endpoint được version bằng `/api/v1` để sau này có thể thay Jinja UI bằng React
hoặc Next.js mà không viết lại DataOps Core.

## 12.3 Mô hình tenant và quyền sở hữu

```text
Instance
├── Users
└── Workspaces
    ├── Members + roles
    └── Projects
        ├── Provider integrations
        ├── Repositories/environments
        ├── Integration tokens
        ├── Pipeline runs
        └── Incidents/recovery audit
```

Role tối thiểu của MVP:

| Role | Quyền chính |
|---|---|
| `OWNER` | Quản lý workspace, thành viên và credential |
| `OPERATOR` | Xem dữ liệu, duyệt và chạy recovery được policy cho phép |
| `VIEWER` | Chỉ xem dashboard, run, log và incident |

Self-hosted instance không mở public signup mặc định. Tài khoản owner đầu tiên được bootstrap
một lần khi cài đặt; các user tiếp theo được owner mời vào workspace.

## 12.4 Hai loại credential không được dùng lẫn

### Integration token: CI/CD gửi dữ liệu vào DataOps

Mỗi token thuộc một project/integration và chỉ có scope cần thiết, ví dụ
`runs:write`, `logs:write`, `reports:write` hoặc `verification:write`.

Token phải:

- chỉ hiển thị secret đầy đủ một lần khi tạo;
- chỉ lưu hash và prefix nhận diện trong database;
- có thể đặt hạn sử dụng, rotate và revoke;
- ghi `last_used_at`, source và audit event;
- không được dùng để đăng nhập Web UI hoặc gọi provider API.

MVP hiện dùng một `DATAOPS_AGENT_TOKEN` cấp instance. Token theo project là bước nâng cấp
trước khi mở Web UI cho nhiều workspace; tài liệu và API cũ phải ghi rõ giới hạn này.

### Provider credential: DataOps gọi ngược lại CI/CD

GitHub App installation hoặc fine-grained PAT cho phép Platform đọc diff/log hoặc trigger
workflow. Credential được mã hóa bằng instance encryption key, không trả lại cho browser
và không đưa vào context LLM. Credential đọc evidence và credential recovery có thể tách
riêng để áp dụng least privilege.

## 12.5 Onboarding một project GitHub

```text
Admin chạy Docker Compose
  → mở Web UI và bootstrap owner
  → tạo workspace
  → tạo project
  → kết nối repository GitHub
  → cấu hình provider credential nếu cần chiều DataOps → GitHub
  → tạo integration token cho chiều GitHub → DataOps
  → Web UI sinh workflow snippet và danh sách GitHub Secrets
  → user commit workflow
  → pipeline đầu tiên xuất hiện trên dashboard
```

Hai secret tối thiểu trong repository:

```text
DATAOPS_URL=https://dataops.example.com
DATAOPS_TOKEN=<project integration token>
```

Workflow gọi version cố định của Agent thay vì tải script không kiểm soát:

```yaml
- uses: AndyAnh174/dataops-agent@v0
  env:
    DATAOPS_URL: ${{ secrets.DATAOPS_URL }}
    DATAOPS_TOKEN: ${{ secrets.DATAOPS_TOKEN }}
```

Web UI hiển thị đầy đủ hai file cần commit và giá trị `DATAOPS_URL`; `DATAOPS_TOKEN` chỉ được
hiển thị đúng một lần lúc tạo token. Contract tương ứng cho automation:

```http
GET /api/v1/projects/{project_id}/onboarding/github
```

## 12.6 Luồng runtime khép kín

```text
Developer push
  → provider tạo workflow run
  → runner checkout/test/build/deploy
  → DataOps Agent gửi event/log/report
  → Platform xác thực token và correlation
  → PostgreSQL lưu trạng thái; Elasticsearch lưu log/knowledge
  → success: hoàn tất run và hiển thị dashboard
  → failure: tạo incident, collect evidence, Hybrid RAG và RCA
  → Policy Engine: deny / require approval / auto-approve
  → Recovery Executor gọi provider adapter
  → provider chạy recovery + verification
  → callback về Platform
  → verified: resolve; failed: action required/escalate
```

Chiều điều khiển thủ công cũng dùng cùng adapter:

```text
User bấm Run/Rerun trên Web UI
  → Platform kiểm tra RBAC và policy
  → provider adapter trigger workflow
  → Agent gửi kết quả của run mới về Platform
```

Callback tới DataOps mặc định không được làm thay đổi kết quả test/build gốc khi Platform
tạm unavailable. Agent retry có giới hạn và giữ nguyên exit code của stage; project có thể
bật chế độ fail-closed riêng cho quality gate bắt buộc.

## 12.7 Phạm vi Web UI MVP

| Màn hình | Nội dung |
|---|---|
| Login/bootstrap | Đăng nhập owner và khởi tạo instance |
| Workspaces | Thành viên và role |
| Projects | Repository, environment, provider và trạng thái tích hợp |
| Integration tokens | Tạo, rotate, revoke và xem lần dùng cuối |
| Runs | Trạng thái, commit, stage, thời lượng và deployment link |
| Run detail | Timeline, log search, report và evidence |
| Incidents | RCA có citation, confidence và missing information |
| Recovery | Policy decision, approval, attempt, verification và audit |
| Settings | Public URL, retention và kết nối Ollama/provider |

Kibana vẫn hữu ích cho kỹ sư vận hành và debug Elasticsearch nhưng không thay thế dashboard
cho người dùng DataOps.

## 12.8 Đóng gói và Docker Hub

Artifact do dự án sở hữu:

```text
andyanh174/dataops-platform:<version>
├── FastAPI application
├── HTML templates
├── CSS/JavaScript/static assets
└── database migration tooling
```

Một image không nên nhúng PostgreSQL và Elasticsearch vào cùng container. Bộ cài chính thức
là một `compose.yaml` tham chiếu image Platform cùng các image dependency được pin version:

```text
docker compose up -d
  ├── dataops-platform image từ Docker Hub
  ├── PostgreSQL image chính thức
  └── Elasticsearch image chính thức
```

Người dùng trải nghiệm đây là một sản phẩm và một lệnh khởi động, nhưng mỗi process có
healthcheck, volume và vòng đời riêng. Ollama có thể là endpoint bên ngoài; không cần đóng
model hàng GB vào image Platform.

Quy ước phát hành:

- tag bất biến theo SemVer, commit SHA và image digest;
- `latest` chỉ dùng thử, production pin version/digest;
- build `linux/amd64` và `linux/arm64`;
- tạo SBOM và scan vulnerability trước khi push;
- container chạy non-root, không chứa secret;
- publish `compose.yaml`, `.env.example`, upgrade và rollback guide cùng release.

Rollback Platform bằng cách đổi lại tag/digest ổn định trước đó, chạy Compose và xác minh
health endpoint. Migration phá vỡ tương thích phải có backup và rollback plan riêng.

## 12.9 Ranh giới public/private

```text
Public qua HTTPS reverse proxy
├── Web UI
├── /api/v1 agent callbacks
└── provider webhook endpoints

Private Docker/network only
├── PostgreSQL
├── Elasticsearch/Kibana
└── Ollama, nếu chạy cùng hạ tầng
```

Production cần cookie `HttpOnly`, `Secure`, `SameSite`, CSRF protection cho Web UI,
rate limit cho login/ingestion, audit authentication event và encryption key ổn định.
Health/readiness endpoint có thể public nhưng không được lộ secret hoặc dependency detail.

## 12.10 Definition of Done

Web Platform chỉ được xem là hoàn tất khi:

1. Một người mới có thể chạy Compose, bootstrap owner và mở Web UI từ tài liệu.
2. User tạo project/token và tích hợp repository mà không sửa core code.
3. Một commit success và một commit failure đều xuất hiện đúng project.
4. Incident hiển thị evidence, RCA, policy decision, recovery và verification audit.
5. Token project không truy cập chéo workspace; revoke có hiệu lực ngay.
6. Restart container không mất PostgreSQL/Elasticsearch data.
7. Image Docker Hub được scan, ký/tag bất biến và smoke test trên `amd64`/`arm64`.
8. Upgrade và rollback được diễn tập với dữ liệu backup.

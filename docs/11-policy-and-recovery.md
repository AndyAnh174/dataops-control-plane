# M6 — Policy Engine và Recovery có kiểm soát

M6 nối RCA của M5 với một vòng phục hồi có kiểm soát. LLM chỉ đề xuất hành động;
Policy Engine quyết định bằng luật cố định, operator duyệt hành động có thay đổi hệ thống,
Recovery Executor gọi provider, và incident chỉ được đóng sau verification callback hợp lệ.

## 1. Luồng trạng thái

```text
RCA VALIDATED
  -> POST recovery-plans
  -> Policy Engine: REQUIRE_APPROVAL hoặc DENIED
  -> operator APPROVED / REJECTED
  -> POST execute
  -> RecoveryAttempt PENDING -> DISPATCHED
  -> GitHub Actions chạy recovery + verification
  -> callback PASSED -> attempt VERIFIED -> incident RESOLVED
  -> callback FAILED -> attempt FAILED -> incident ACTION_REQUIRED
```

`DISPATCHED` không đồng nghĩa với thành công. Control Plane chỉ chuyển incident sang
`RESOLVED` sau callback `PASSED` khớp cả attempt ID, external reference và idempotency key.

## 2. Policy `recovery-v1`

Policy hiện tại không gọi LLM và không tự thay đổi theo prompt. Một plan bị `DENIED` nếu:

- RCA chưa ở trạng thái `VALIDATED`;
- confidence thấp hơn `0.80`;
- RCA vẫn còn `missing_information`;
- action là `ESCALATE` hoặc `NO_ACTION`;
- rollback không cung cấp đủ hai image immutable dạng `sha-<full commit>` và revision 40 ký tự hex.

Các action có thể thực thi đều dùng `REQUIRE_APPROVAL`. M6 chưa bật auto-approval trong
production. Risk được phân loại `LOW` cho retry, `MEDIUM` cho quarantine/create PR và `HIGH`
cho rollback. GitHub executor M6 hỗ trợ `RETRY`, `QUARANTINE`, `ROLLBACK_IMAGE`; `CREATE_PR`
được giữ trong domain model nhưng chưa có capability nên không thể dispatch.

## 3. Idempotency và audit

- Cùng `RCA report + policy_version` chỉ tạo một `RecoveryPlan`.
- Mỗi plan chỉ có tối đa một `RecoveryAttempt` trong M6.
- Idempotency key là SHA-256 từ policy version, plan ID và action.
- Gọi execute lần nữa trả attempt cũ với `duplicate: true`, không gửi request thứ hai.
- Callback giống hệt trả `duplicate: true`; callback mâu thuẫn trả HTTP `409`.
- Không có vòng retry vô hạn. Provider lỗi tạo attempt `FAILED` và dừng.

Audit trail ghi `PLAN_CREATED`, `PLAN_APPROVED`/`PLAN_REJECTED`,
`EXECUTION_DISPATCHED`/`EXECUTION_FAILED`, và `VERIFICATION_PASSED`/`VERIFICATION_FAILED`.

## 4. API

Trong M6 hiện tại, tất cả endpoint dưới đây yêu cầu
`Authorization: Bearer ${DATAOPS_AGENT_TOKEN}`. Đây là giới hạn tạm thời, không phải mô hình
quyền của Web Platform cuối cùng.

```http
POST /api/v1/incidents/{incident_id}/recovery-plans
POST /api/v1/incidents/{incident_id}/recovery-plans/{plan_id}/approve
POST /api/v1/incidents/{incident_id}/recovery-plans/{plan_id}/reject
POST /api/v1/incidents/{incident_id}/recovery-plans/{plan_id}/execute
POST /api/v1/incidents/{incident_id}/recovery-attempts/{attempt_id}/verification
GET  /api/v1/incidents/{incident_id}/recovery-audit
```

Approve:

```json
{
  "actor": "demo-operator"
}
```

Reject:

```json
{
  "actor": "demo-operator",
  "reason": "Evidence needs manual review"
}
```

Verification callback do workflow gửi:

```json
{
  "idempotency_key": "<64 hex characters>",
  "status": "PASSED",
  "external_reference": "github:workflow_dispatch:<attempt_id>",
  "details": {
    "recovery_action": "QUARANTINE",
    "success": true,
    "rows_quarantined": 2,
    "rows_released": 198
  }
}
```

## 5. GitHub Actions adapter

Control Plane gọi endpoint workflow dispatch của GitHub bằng token riêng:

```text
DATAOPS_GITHUB_RECOVERY_TOKEN=<fine-grained token with Actions: write>
DATAOPS_GITHUB_RECOVERY_WORKFLOW=dataops-recovery.yml
DATAOPS_GITHUB_RECOVERY_TIMEOUT_SECONDS=15
```

Không dùng lại `DATAOPS_GITHUB_TOKEN` chỉ-đọc của Evidence Collector. Token write phải lưu
trong secret store/runtime environment, giới hạn đúng các repository được phép recovery và
không commit vào `.env` hay Git.

Adapter gửi sáu input đã khai báo trong workflow: action, incident ID, attempt ID,
idempotency key, stable external reference và parameters JSON. Repository demo triển khai
workflow tại `.github/workflows/dataops-recovery.yml`:

- `RETRY` chạy lại data-quality pipeline với dữ liệu lành mạnh;
- `QUARANTINE` loại dòng null/duplicate/out-of-range rồi kiểm tra lại tập phát hành;
- `ROLLBACK_IMAGE` chỉ nhận image thuộc demo repo với tag immutable `sha-<40 hex>`, triển khai
  bằng script có rollback sẵn và health check;
- bước cuối chạy với `always()` và callback `PASSED` hoặc `FAILED` về Control Plane.

Theo GitHub, workflow dispatch yêu cầu workflow tồn tại trên default branch, input phải được
khai báo trong workflow, và credential gọi API cần quyền Actions write.

## 6. Demo M6

1. Tạo một incident bằng fault scenario `range` trong repo demo.
2. Thu evidence, index knowledge và chạy RCA M5.
3. Tạo recovery plan; kiểm tra action, parameters, risk và policy decision.
4. Approve plan bằng tên operator demo.
5. Execute plan. Response phải là `DISPATCHED` và có idempotency key.
6. Mở GitHub Actions, quan sát workflow **DataOps Recovery** chạy `QUARANTINE`.
7. Đọc incident và audit. Kết quả mong đợi: attempt `VERIFIED`, incident `RESOLVED`, audit có
   đủ plan → approval → dispatch → verification.
8. Gọi lại execute hoặc callback để chứng minh `duplicate: true` và không chạy lần hai.

## 7. Giới hạn M6

- Approval đang dùng shared bearer token và trường `actor`; chưa tích hợp OIDC/RBAC danh tính người dùng.
- Mỗi plan chỉ có một attempt; muốn retry recovery phải tạo RCA/plan phiên bản mới.
- GitHub Actions là write adapter đầu tiên; core `RecoveryExecutor` vẫn provider-neutral để thêm
  GitLab CI, Jenkins hoặc Kubernetes adapter sau.
- Production deployment cần backup metadata, image SHA được pin, health check và rollback plan;
  không tự cập nhật chỉ vì source code đã merge.

Khi Web Platform được triển khai, approve/reject/execute từ browser phải yêu cầu session của
`OWNER` hoặc `OPERATOR`, CSRF protection và lấy actor từ identity đã xác thực thay vì tin
trường `actor` trong payload. Verification callback dùng integration token có scope riêng;
Agent gửi log không mặc nhiên có quyền duyệt recovery.

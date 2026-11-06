"""Freeze public source snapshots and provisional code-navigation relevance labels."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

PLANE_SHA = "1e1674614c878fc1f6e4e19ca6d1192dac663ddd"
AGENT_SHA = "c1570c5c1d6d3f9e754541c41885dd6ef127f83c"

# One intent = one evaluation group. Its English/Vietnamese versions are paired,
# not independent samples. Labels are declaration locations checked against Git.
INTENTS = [
    (
        "plane",
        "api/dependencies.py",
        "require_agent_token",
        "Where is the pipeline sender's project credential checked before accepting requests?",
        "Hàm nào kiểm tra khóa project của agent trước khi nhận request?",
    ),
    (
        "plane",
        "api/dependencies.py",
        "require_same_origin",
        "Find the browser request origin check used to prevent cross-site writes.",
        "Tìm hàm kiểm tra nguồn request trình duyệt để ngăn ghi dữ liệu từ website khác.",
    ),
    (
        "plane",
        "api/dependencies.py",
        "require_operator_run_access",
        "Find where an operator's permission to access a pipeline execution is enforced.",
        "Hàm nào buộc người vận hành phải có quyền truy cập một lần chạy pipeline?",
    ),
    (
        "plane",
        "services/web_identity.py",
        "hash_password",
        "Locate the password derivation routine used before storing account credentials.",
        "Tìm hàm băm mật khẩu trước khi lưu thông tin tài khoản.",
    ),
    (
        "plane",
        "services/web_identity.py",
        "bootstrap_owner",
        "Where is the first administrator and workspace created while preventing a second initial setup?",
        "Hàm nào tạo chủ sở hữu và workspace ban đầu đồng thời chặn khởi tạo lần hai?",
    ),
    (
        "plane",
        "services/web_identity.py",
        "authenticate_user",
        "Find the login routine that verifies account credentials and issues a browser session.",
        "Tìm hàm đăng nhập kiểm tra tài khoản rồi cấp phiên làm việc cho trình duyệt.",
    ),
    (
        "plane",
        "services/web_projects.py",
        "create_integration_token",
        "Where is a scoped credential for a pipeline agent issued for a project?",
        "Hàm nào cấp token giới hạn phạm vi project cho agent pipeline?",
    ),
    (
        "plane",
        "services/web_projects.py",
        "delete_project",
        "Locate the project removal operation that also checks the caller's workspace role.",
        "Tìm hàm xóa project có kiểm tra vai trò người gọi trong workspace.",
    ),
    (
        "plane",
        "services/pipeline_events.py",
        "ensure_valid_transition",
        "Where are invalid changes between pipeline lifecycle states rejected?",
        "Hàm nào từ chối chuyển trạng thái vòng đời pipeline không hợp lệ?",
    ),
    (
        "plane",
        "services/pipeline_events.py",
        "ensure_incident_for_failed_run",
        "Find the routine that creates or reuses an incident after a failed execution.",
        "Tìm hàm tạo hoặc dùng lại sự cố khi pipeline chạy thất bại.",
    ),
    (
        "plane",
        "services/pipeline_logs.py",
        "redact_log_text",
        "Locate the function that masks sensitive strings in plain pipeline log messages.",
        "Tìm hàm che thông tin nhạy cảm trong nội dung log dạng văn bản.",
    ),
    (
        "plane",
        "services/pipeline_logs.py",
        "build_log_documents",
        "Where are incoming agent log entries converted into indexed documents with run metadata?",
        "Hàm nào chuyển log agent thành tài liệu để index kèm metadata lần chạy?",
    ),
    (
        "plane",
        "services/evidence.py",
        "collect_incident_evidence",
        "Find the evidence collection routine that combines multiple sources for one incident.",
        "Tìm hàm tổng hợp bằng chứng từ nhiều nguồn cho một sự cố.",
    ),
    (
        "plane",
        "services/retrieval.py",
        "reciprocal_rank_fusion",
        "Locate the algorithm combining keyword and vector result rankings using reciprocal positions.",
        "Tìm thuật toán gộp bảng xếp hạng từ khóa và vector theo nghịch đảo vị trí.",
    ),
    (
        "plane",
        "services/retrieval.py",
        "search",
        "Find the hybrid retrieval entry point that embeds a query and combines lexical and vector candidates.",
        "Tìm hàm truy hồi hybrid tạo embedding câu hỏi và kết hợp ứng viên từ khóa với vector.",
    ),
    (
        "plane",
        "services/rca_agent.py",
        "_evidence_gate",
        "Where does incident analysis stop when the collected evidence is insufficient?",
        "Hàm nào chặn phân tích sự cố khi bằng chứng thu thập chưa đủ?",
    ),
    (
        "plane",
        "services/rca_agent.py",
        "_validate",
        "Locate the step checking generated root-cause output against evidence and retrieved knowledge.",
        "Tìm bước kiểm tra kết quả phân tích nguyên nhân với bằng chứng và tri thức đã truy hồi.",
    ),
    (
        "plane",
        "services/rca_agent.py",
        "_retrieval_query",
        "Where is the knowledge search question assembled from a failed run and its evidence?",
        "Hàm nào tạo câu truy vấn tri thức từ pipeline lỗi và bằng chứng của nó?",
    ),
    (
        "plane",
        "services/recovery_policy.py",
        "_evaluate",
        "Find the policy decision choosing whether a proposed recovery is allowed, denied, or needs approval.",
        "Tìm hàm đánh giá đề xuất phục hồi được phép, bị từ chối hay cần phê duyệt.",
    ),
    (
        "plane",
        "services/recovery_policy.py",
        "_has_immutable_rollback_parameters",
        "Where is rollback required to use a fixed revision or immutable artifact rather than a mutable target?",
        "Hàm nào kiểm tra rollback dùng revision hoặc artifact cố định thay vì đích thay đổi được?",
    ),
    (
        "plane",
        "services/recovery_execution.py",
        "execute_recovery_plan",
        "Locate the orchestration enforcing approval and tracking a recovery dispatch attempt.",
        "Tìm hàm điều phối kiểm tra phê duyệt và theo dõi lần gửi yêu cầu phục hồi.",
    ),
    (
        "agent",
        "context.ts",
        "contextFromEnvironment",
        "Where does the agent switch between hosted CI metadata and portable environment variables?",
        "Hàm nào giúp agent chọn metadata GitHub CI hoặc biến môi trường portable?",
    ),
    (
        "agent",
        "agent.ts",
        "runAgent",
        "Find the agent entry point that executes configured stages and reports pipeline status.",
        "Tìm hàm chính của agent chạy các stage theo cấu hình và gửi trạng thái pipeline.",
    ),
    (
        "agent",
        "agent.ts",
        "uploadDataQualityReport",
        "Locate the agent routine that uploads a data quality report even when validation fails.",
        "Tìm hàm của agent gửi báo cáo chất lượng dữ liệu kể cả khi kiểm tra thất bại.",
    ),
]


def windows(text, size=2000, overlap=250):
    if not 0 <= overlap < size:
        raise ValueError("Invalid window overlap")
    result, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        result.append((start, end, text[start:end]))
        if end == len(text):
            break
        start = end - overlap
    return result


def declaration_span(text, symbol, suffix):
    keyword = "def" if suffix == ".py" else "function"
    matches = list(re.finditer(r"\b" + keyword + r"\s+" + re.escape(symbol) + r"\s*\(", text))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one declaration for {symbol}; got {len(matches)}")
    return matches[0].span()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode("utf-8")


def prepare(plane, agent, output):
    output = Path(output)
    output.mkdir(exist_ok=True, parents=True)
    corpus, sources = [], {}
    revisions = {"plane": PLANE_SHA, "agent": AGENT_SHA}
    for name, repo in [("plane", plane), ("agent", agent)]:
        commit = revisions[name]
        paths = git(repo, "ls-tree", "-r", "--name-only", commit, "src").splitlines()
        for path in paths:
            if Path(path).suffix not in {".py", ".ts"}:
                continue
            text = git(repo, "show", commit + ":" + path)
            if not text.strip():
                continue
            relative = path.removeprefix(
                "src/dataops_control_plane/" if name == "plane" else "src/"
            )
            source_id = name + "/" + relative
            sources[source_id] = text
            for start, end, content in windows(text):
                corpus.append(
                    {
                        "id": f"D{len(corpus):04}",
                        "repo": name,
                        "commit": commit,
                        "path": path,
                        "source_id": source_id,
                        "char_start": start,
                        "char_end": end,
                        "line_start": text[:start].count("\n") + 1,
                        "line_end": text[:end].count("\n") + 1,
                        "content": content,
                    }
                )
    queries = []
    for n, (repo, path, symbol, english, vietnamese) in enumerate(INTENTS, 1):
        source_id = repo + "/" + path
        start, end = declaration_span(sources[source_id], symbol, Path(path).suffix)
        gold = [
            c["id"]
            for c in corpus
            if c["source_id"] == source_id and c["char_start"] <= start and c["char_end"] >= end
        ]
        if not gold:
            raise ValueError("Missing relevant corpus window")
        for language, query in [("en", english), ("vi", vietnamese)]:
            queries.append(
                {
                    "id": f"I{n:02}_{language}",
                    "intent_id": f"I{n:02}",
                    "language": language,
                    "query": query,
                    "relevant": gold,
                    "target_source": source_id,
                    "target_symbol": symbol,
                    "label_status": "source_verified_provisional_navigation_judgment",
                }
            )
    for name, rows in [("corpus", corpus), ("queries", queries)]:
        (output / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
    metadata = {
        "revisions": revisions,
        "chunk_chars": 2000,
        "overlap_chars": 250,
        "files": len(sources),
        "chunks": len(corpus),
        "intents": len(INTENTS),
        "queries": len(queries),
        "human_reviewed": False,
        "task": "find implementing declaration; not arbitrary code QA",
        "sha256": {
            name: hashlib.sha256((output / f"{name}.jsonl").read_bytes()).hexdigest()
            for name in ["corpus", "queries"]
        },
    }
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plane", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.plane, args.agent, args.out), indent=2))

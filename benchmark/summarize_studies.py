"""Export public aggregates and a concise Vietnamese evidence report; no case text."""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "llm"))
from harness import quantile


def load_records(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            result[row["key"]] = row
    return list(result.values())


def leaders(rows, metric):
    eligible = [
        row
        for row in rows
        if row.get("complete")
        and isinstance(row.get(metric), (int, float))
        and math.isfinite(row[metric])
    ]
    if not eligible:
        return []
    best = max(row[metric] for row in eligible)
    return sorted(
        row["model"] for row in eligible if math.isclose(row[metric], best, abs_tol=1e-12)
    )


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def public_llm(root):
    root = Path(root)
    summary = read(root / "results/summary.json")
    records = load_records(root / "results/attempts.jsonl")
    result = []
    for row in summary:
        attempted = [r for r in records if r["model"] == row["model"] and not r["warmup"]]
        valid = [r for r in attempted if r["status"] == "ok"]
        times = [r["latency_ms"] for r in valid]
        environments = {}
        for environment in ["prod", "dev", "ci"]:
            groups = [g for g in row["groups"] if g["environment"] == environment]
            environments[environment] = {
                "macro_f1": sum(g["metrics"]["macro_f1"] for g in groups) / len(groups)
                if all(g["complete"] for g in groups)
                else None,
                "supported_macro_f1": sum(g["metrics"]["supported_macro_f1"] for g in groups)
                / len(groups)
                if all(g["complete"] for g in groups)
                else None,
                "planned_per_repeat": groups[0]["planned"],
            }
        result.append(
            {
                "model": row["model"],
                "provider": row["provider"],
                "complete": row["eligible_for_ranking"],
                "macro_f1": row["mean_environment_macro_f1"],
                "coverage": row["valid_of_planned"],
                "attempted_main": len(attempted),
                "valid_main": len(valid),
                "response_errors": len(attempted) - len(valid),
                "error_types": dict(
                    Counter(r.get("error", r["status"]) for r in attempted if r["status"] != "ok")
                ),
                "p50_ms": quantile(times, 0.5),
                "p95_ms": quantile(times, 0.95),
                "environments": environments,
                "returned_models": sorted(
                    {r["returned_model"] for r in valid if r.get("returned_model")}
                ),
            }
        )
    return {
        "study": "frozen masked log/build classification; not RCA correctness",
        "dataset_sha256": read(root / "results/identity.json")["dataset_sha256"],
        "test_cases": 158,
        "repeats": 3,
        "source_derived_labels": True,
        "main_attempts": sum(not r["warmup"] for r in records),
        "rows": result,
        "leader": leaders(result, "macro_f1"),
        "local_leader": leaders([r for r in result if r["provider"] == "ollama"], "macro_f1"),
        "inputs_sha256": {
            "summary": sha(root / "results/summary.json"),
            "journal": sha(root / "results/attempts.jsonl"),
        },
    }


def public_embedding(root):
    root = Path(root)
    summary = read(root / "results/retrieval_summary.json")
    records = load_records(root / "results/attempts.jsonl")
    rows = []
    for row in summary:
        metadata = row.get("metadata") or {}
        item = {
            key: row.get(key)
            for key in [
                "model",
                "complete",
                "coverage",
                "recall_at_5",
                "mrr_at_5",
                "ndcg_at_5",
                "query_p50_ms",
                "query_p95_ms",
                "languages",
            ]
        }
        item.update(
            dimensions=metadata.get("dimensions"),
            vector_float32_bytes=metadata.get("vector_storage_float32_bytes"),
            index_wall_seconds=metadata.get("index_wall_seconds"),
            availability=metadata.get("availability"),
            access_or_execution_error=metadata.get("error"),
            document_quota_wait_seconds=sum(
                r.get("quota_wait_seconds", 0)
                for r in records
                if r["model"] == row["model"] and r["input_type"] == "document"
            ),
        )
        rows.append(item)
    reader_path = root / "results/reader_summary.json"
    return {
        "study": "public code declaration navigation; not full code-QA correctness",
        "dataset": read(root / "data/manifest.json"),
        "rows": rows,
        "leaders": {
            metric: leaders(rows, metric) for metric in ["recall_at_5", "mrr_at_5", "ndcg_at_5"]
        },
        "readers": read(reader_path) if reader_path.exists() else [],
        "inputs_sha256": {
            "summary": sha(root / "results/retrieval_summary.json"),
            "journal": sha(root / "results/attempts.jsonl"),
        },
    }


def number(value, digits=3):
    return "N/A" if value is None else f"{value:.{digits}f}"


def write_report(llm_root, embedding_root, output):
    output = Path(output)
    output.mkdir(exist_ok=True, parents=True)
    llm = public_llm(llm_root)
    embedding = public_embedding(embedding_root)
    for name, data in [("llm-public-summary", llm), ("embedding-public-summary", embedding)]:
        (output / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    lines = [
        "# Báo cáo benchmark LLM và embedding DataOps — 06/11/2026",
        "",
        "## Kết quả LLM trên bộ log đã chốt",
        "",
        "158 mẫu test; ba lượt trên cùng mẫu, không phải 474 mẫu độc lập/model. "
        "Điểm tổng là trung bình đều macro F1 cố định nhãn của prod/dev/CI và ba block; "
        "lỗi phản hồi được tính là sai. Chỉ xếp hạng cấu hình chạy đủ kế hoạch.",
        "",
        "| Model | Macro F1 | Valid/planned | Phản hồi lỗi | p50 / p95 HTTP (ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in llm["rows"]:
        lines.append(
            f"| {row['model']} | {number(row['macro_f1'])} | {row['coverage']:.1%} | "
            f"{row['response_errors']} | "
            f"{number(row['p50_ms'], 1)} / {number(row['p95_ms'], 1)} |"
        )
    lines += [
        "",
        "Dẫn đầu theo điểm đã định nghĩa trong các cấu hình hoàn tất: "
        f"{', '.join(llm['leader']) or 'chưa có'}. "
        f"Nhóm local: {', '.join(llm['local_leader']) or 'chưa có'}.",
        "",
        "Prod có 29 mẫu, dev 76, CI 53. Dev thiếu INFO nên macro F1 cố định ba nhãn "
        "có trần 2/3; điểm tổng có trần 8/9. Prod chỉ có ba mẫu ERROR và một nhóm nguồn; "
        "dev cũng chỉ có một nhóm nguồn. Điểm này không phải accuracy "
        "và không chứng minh RCA/rollback đúng.",
        "",
        "Các API là endpoint chat/generateContent, còn Decision benchmark cũ dùng "
        "endpoint native riêng. Số đo cũ là tham chiếu lịch sử, "
        "không phải đối chứng chạy đồng thời. HTTP timing gồm mạng/client, "
        "loại warmup và khoảng chờ quota; các study có thể chồng thời gian trên Colab. "
        "Không suy ra VRAM phía server API hay chi phí hóa đơn.",
        "",
        "## Embedding cho tìm khai báo hàm",
        "",
        "Corpus cố định: 49 file public, 166 windows, 24 intent với hai bản EN/VI = 48 query. "
        "Nhãn kiểm tra theo source nhưng chưa được người vận hành duyệt tính đầy đủ. "
        "Ba repeat query là đo lặp, không tăng số intent độc lập.",
        "",
        "| Cấu hình | Recall@5 | MRR@5 | nDCG@5 | p50 HTTP query (ms) | Chiều vector |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in embedding["rows"]:
        lines.append(
            f"| {row['model']} | {number(row['recall_at_5'])} | {number(row['mrr_at_5'])} | "
            f"{number(row['ndcg_at_5'])} | {number(row['query_p50_ms'], 1)} | "
            f"{row['dimensions'] or '—'} |"
        )
    lines += ["", "Dẫn đầu theo từng metric (chỉ cấu hình hoàn tất):"]
    for metric, names in embedding["leaders"].items():
        lines.append(f"- {metric}: {', '.join(names) or 'chưa có'}.")
    lines += [
        "",
        "Vector dùng kích thước gốc và quantization thực tế của từng cấu hình. "
        "Thời gian index wall gồm cả pacing; đơn vị UTF8 dùng để điều tiết quota "
        "không phải token billing đã đo. BM25 là baseline từ khóa. "
        "Các API lỗi/thiếu quyền ghi N/A, không ghi điểm 0.",
        "",
        "## Kiểm tra downstream",
        "",
        "Giữ GPT-6 Luna chat và D3 9B Decision cố định trên cùng top 5; "
        "oracle context luôn chứa một đoạn liên quan và các đoạn gây nhiễu cố định. "
        "Đây không phải trần điểm toán học của reader. Chấm passage chứa khai báo đích, "
        "không tự động chấm độ đúng của câu trả lời code tự do. "
        "D3 giữ trọng số gốc và dùng profile context 16k.",
        "",
        "| Reader | Retriever | Relevant selection (tỷ lệ trên 48 mẫu) | Valid/planned |",
        "|---|---|---:|---:|",
    ]
    for row in embedding["readers"]:
        lines.append(
            f"| {row['reader']} | {row['retriever']} | "
            f"{number(row['selected_relevant_of_planned'])} | {row['valid_of_planned']:.1%} |"
        )
    lines += [
        "",
        "## Bằng chứng và cách dùng trong tiểu luận",
        "",
        "ZIP riêng giữ dataset đã che, từng case, checkpoint, phiên bản/digest quan sát "
        "và source thực thi. Git chỉ chứa code, aggregate và hình đã rà soát; "
        "salt, log gốc, khóa và case riêng không đưa lên Git. "
        "JSON public đi kèm ghi checksum của đầu vào tổng hợp để đối chiếu.",
        "",
        "Chọn model theo đúng tác vụ và nguồn lực triển khai. Leader trên bộ proxy này "
        "là bằng chứng nội bộ có giới hạn, không phải model mạnh nhất mọi lĩnh vực. "
        "Trước khi chốt production cần nhãn incident/RCA/hành động do người vận hành "
        "xác nhận, thêm thời kỳ/nguồn độc lập và lớp còn thiếu.",
        "Corpus code nhỏ, công khai và cùng dự án; không loại trừ model đã gặp code "
        "tương tự trong huấn luyện. Cần một corpus dự án khác hoặc code mới để đánh giá "
        "khả năng khái quát.",
        "",
        "Nguồn official API/model và công cụ phương pháp được ghi trong PROTOCOL.md "
        "của từng study. Scientific Agent Skills được trích dẫn cho hỗ trợ thiết kế/vẽ hình, "
        "không dùng làm nguồn điểm model.",
        "",
    ]
    path = output / "BAO-CAO-SO-SANH-LLM-EMBEDDING.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", required=True)
    parser.add_argument("--embedding", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(write_report(args.llm, args.embedding, args.out))

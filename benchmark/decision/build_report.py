"""Generate a descriptive report only from downloaded measured outputs."""

import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import run_benchmark as bench

ROOT = Path(__file__).resolve().parent


def main():
    result_dir = ROOT / "results"
    rows = [
        json.loads(line) for line in (result_dir / "predictions.jsonl").read_text().splitlines()
    ]
    metadata = json.loads((result_dir / "run_metadata.json").read_text())
    cases = [
        json.loads(line)
        for line in (ROOT / "data/cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    test = {row["id"]: row for row in cases if row["split"] == "test"}
    assert metadata.get("completed_utc"), "Main evaluation not complete"
    assert (
        hashlib.sha256((ROOT / "data/cases.jsonl").read_bytes()).hexdigest()
        == metadata["dataset_sha256"]
    )
    assert len({(r["backend"], r["model"], r["repeat"], r["id"]) for r in rows}) == len(rows)
    by_model = defaultdict(list)
    for row in rows:
        assert row["id"] in test and row["label"] == test[row["id"]]["label"]
        assert row["state_sha256"] == bench.digest(test[row["id"]]["state"])
        assert row["question_sha256"] == bench.digest(bench.TASKS[row["task"]])
        by_model[(row["backend"], row["model"])].append(row)
    incomplete = []
    for key, records in list(by_model.items()):
        if not all(
            {r["id"] for r in records if r["repeat"] == repeat} == set(test) for repeat in range(3)
        ):
            incomplete.append(
                {
                    "backend": key[0],
                    "model": key[1],
                    "attempted": len(records),
                    "statuses": dict(Counter(r["status"] for r in records)),
                    "errors": sorted({r.get("error", "") for r in records if r["status"] != "ok"}),
                }
            )
            del by_model[key]
    (result_dir / "incomplete_models.json").write_text(
        json.dumps(incomplete, indent=2), encoding="utf-8"
    )
    metrics = bench.summarize(rows)
    (result_dir / "metrics_recomputed.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    summary = []
    for (backend, model), records in sorted(by_model.items()):
        for repeat in range(3):
            assert {r["id"] for r in records if r["repeat"] == repeat} == set(test), (
                "Unequal evaluation sets"
            )
        model_metrics = [r for r in metrics if r["model"] == model and r["backend"] == backend]
        item = dict(
            backend=backend,
            model=model,
            predictions=len(records),
            errors=sum(r["status"] != "ok" for r in records),
            unique_test_cases=len(test),
        )
        for env in ("prod", "dev", "ci"):
            env_metrics = [r for r in model_metrics if r["environment"] == env]
            for metric in (
                "macro_f1_including_errors",
                "macro_f1_supported_labels",
                "accuracy_including_errors",
            ):
                values = [r[metric] for r in env_metrics]
                item[env + "_" + metric] = statistics.mean(values)
                item[env + "_" + metric + "_range"] = [min(values), max(values)]
        item["equal_environment_proxy_index"] = statistics.mean(
            item[e + "_macro_f1_including_errors"] for e in ("prod", "dev", "ci")
        )
        valid = [r for r in records if r["status"] == "ok"]
        item["p50_ms"] = bench.percentile([r["latency_ms"] for r in valid], 0.5)
        item["p95_ms"] = bench.percentile([r["latency_ms"] for r in valid], 0.95)
        blocks = [b for b in metadata["blocks"] if b["model"] == model and b["backend"] == backend]
        item["peak_gpu_gib"] = (
            max((b.get("peak_gpu_memory_mib_observed", 0) or 0 for b in blocks), default=0) / 1024
            if backend == "ollama"
            else None
        )
        predictions = defaultdict(set)
        for r in records:
            predictions[r["id"]].add(r.get("prediction", "__ERROR__"))
        item["cases_with_prediction_changes_across_repeats"] = sum(
            len(v) > 1 for v in predictions.values()
        )
        summary.append(item)
    summary.sort(key=lambda r: r["equal_environment_proxy_index"], reverse=True)
    (result_dir / "comparison_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    with (result_dir / "comparison_summary.csv").open("w", newline="", encoding="utf-8-sig") as out:
        writer = csv.DictWriter(out, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    robust = []
    robust_path = result_dir / "robustness_predictions.jsonl"
    if robust_path.exists():
        cohort = {
            r["id"]: r
            for r in [
                json.loads(line)
                for line in (ROOT / "data/robustness_cases.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        }
        by = defaultdict(dict)
        for line in robust_path.read_text().splitlines():
            r = json.loads(line)
            c = cohort[r["id"]]
            by[(r["backend"], r["model"])].setdefault(c["parent_id"], {})[c["variant"]] = (r, c)
        for (backend, model), pairs in by.items():
            eligible = success = flips = valid_pairs = 0
            for pair in pairs.values():
                if set(pair) != {"control", "injection"}:
                    continue
                control, _ = pair["control"]
                attack, c = pair["injection"]
                if control["status"] == "ok" and attack["status"] == "ok":
                    valid_pairs += 1
                    flips += control["prediction"] != attack["prediction"]
                if control.get("prediction") == control["label"] and control["status"] == "ok":
                    eligible += 1
                    success += (
                        attack.get("prediction") == c["attack_target"] and attack["status"] == "ok"
                    )
            robust.append(
                dict(
                    backend=backend,
                    model=model,
                    source_pairs=len(pairs),
                    valid_pairs=valid_pairs,
                    control_correct=eligible,
                    attack_target_successes=success,
                    conditional_asr=success / eligible if eligible else None,
                    prediction_flips=flips,
                )
            )
        (result_dir / "robustness_summary.json").write_text(
            json.dumps(robust, indent=2), encoding="utf-8"
        )

    lines = [
        "# Báo cáo benchmark Decision trên log HCMUTE",
        "",
        "**Bản kết quả thăm dò — cần sinh viên kiểm tra trước khi đưa vào báo cáo chính thức.**",
        "",
        f"Đã ghi thực tế {len(rows):,} lần gọi chính trên {len(test)} mẫu giữ riêng; {len(summary)} model hoàn tất đủ ba lượt. "
        "Số mẫu độc lập không tăng lên khi chạy lặp. Kết quả dưới đây được tính lại từ predictions.jsonl; "
        "không dùng điểm bài viết tham khảo làm kết quả của dự án.",
        "",
        "## Kết quả đo",
        "",
        "| Model | F1 prod | F1 dev, 3 nhãn | F1 CI | Chỉ số proxy, 3 môi trường | p50 ms | p95 ms | VRAM đỉnh GiB | Lỗi |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        memory = "NA" if r["peak_gpu_gib"] is None else f"{r['peak_gpu_gib']:.2f}"
        lines.append(
            f"| {r['model']} | {r['prod_macro_f1_including_errors']:.3f} | {r['dev_macro_f1_including_errors']:.3f} | {r['ci_macro_f1_including_errors']:.3f} | {r['equal_environment_proxy_index']:.3f} | {r['p50_ms']:.1f} | {r['p95_ms']:.1f} | {memory} | {r['errors']} |"
        )
    lines += [
        "",
        "F1 là trung bình mô tả của ba lượt. “Chỉ số proxy” là trung bình đều F1 ba môi trường "
        "theo quy trình đã lưu; không phải độ chính xác phần trăm và không phải chỉ số của bài tham khảo. "
        "Dev thiếu INFO nên F1 với ba nhãn có trần 2/3; không so trực tiếp F1 này với môi trường đủ nhãn. "
        "p50/p95 gộp các request hợp lệ đã warmup, bao gồm cả ba môi trường và ba lượt. "
        "VRAM là cực đại được lấy mẫu trong mỗi block, có thể gồm vùng nhớ cache của runtime.",
        "",
        "| Model | F1 dev chỉ trên WARN/ERROR | Mẫu đổi dự đoán qua ba lượt |",
        "|---|---:|---:|",
    ]
    for r in summary:
        lines.append(
            f"| {r['model']} | {r['dev_macro_f1_supported_labels']:.3f} | {r['cases_with_prediction_changes_across_repeats']} / {len(test)} |"
        )
    lines += [
        "",
        "## Diễn giải lựa chọn",
        "",
        f"`{summary[0]['model']}` có chỉ số proxy cao nhất trong các cấu hình đã đo. Đây là ứng viên "
        "để đánh giá tiếp trên các sự cố được gán nhãn thủ công, chưa phải kết luận model tốt nhất cho production. "
        "Cần xét riêng khả năng bắt ERROR/FAILURE, độ trễ và tài nguyên theo từng môi trường. "
        "Nimble-v3 có giấy phép trọng số CC BY-NC 4.0 theo model card; d3 và Clef Flash công bố Apache 2.0. "
        "Lựa chọn triển khai cần phù hợp cách sử dụng và điều khoản model.",
        "",
        "## Dữ liệu và giới hạn",
        "",
        "Nguồn thu thập gồm 387 file, 267,09 MiB log prod/dev/CI dùng chung. Không có bộ log môi trường test riêng. "
        "Xuất 339 mẫu sau kiểm tra checksum, loại bản chép thiếu/đang đổi và giới hạn kích thước giải nén. "
        "Tập cuối gồm prod INFO20/WARN6/ERROR3, dev WARN60/ERROR16, CI SUCCESS38/FAILURE15. "
        "Các bản sao template và nguồn liên quan được gom trước khi chia tập. Tập cuối chỉ có 1 cụm nguồn prod, "
        "1 cụm dev và 53 cụm CI; đây là cụm nguồn kỹ thuật, chưa được xác nhận là các sự cố độc lập.",
        "",
        "Nhãn ứng dụng lấy từ mức log gốc; nhãn CI lấy từ kết quả Jenkins. Mức log và dòng kết quả trực tiếp "
        "được che trước khi gọi model. Đây là nhãn đại diện, không xác nhận sự cố cần cảnh báo, nguyên nhân gốc "
        "hay hành động rollback. Tập ít ERROR và ít cụm độc lập không đủ cho khoảng tin cậy hoặc kết luận ưu thế "
        "thống kê đáng tin cậy. Không thực hiện fine-tune hoặc điều chỉnh câu hỏi theo tập cuối.",
        "",
        "Quan sát hậu kiểm: trong cả ba model Ollama và ba lượt, 60 mẫu WARN của dev được dự đoán INFO; "
        "16 mẫu ERROR gồm 12 dự đoán ERROR, 3 WARN và 1 INFO. Điều này gợi ý cần kiểm tra sự tương thích "
        "giữa mức log Nginx và định nghĩa mức nghiêm trọng trong câu hỏi. Đây là giả thuyết cần người vận hành "
        "xác minh; không đổi nhãn sau khi xem kết quả. Điểm prod 1,000 chỉ áp dụng cho 29 mẫu đại diện "
        "của một cụm nguồn, trong đó chỉ 3 ERROR.",
        "",
        "Thông tin thường nhạy cảm được che trước khi đưa tới Colab. Bộ quét không còn phát hiện các mẫu email, "
        "IPv4, tiền tố API key, private key và JWT đã định nghĩa; kết quả đó không bảo đảm dữ liệu hoàn toàn vô danh. "
        "Log gốc và salt vẫn nằm cục bộ. Mỗi state tối đa 2.000 ký tự; phép đo chưa đại diện xử lý log dài, RAG "
        "hoặc tác động embedding của hệ thống DataOps.",
        "",
        "## Cách chạy và bằng chứng",
        "",
        f"Phần cứng ghi nhận: {metadata['hardware']}. Ollama {metadata['ollama']['version']}, "
        "một request tại một thời điểm, một model GPU được nạp, seed 2026. Ba block có thứ tự model và mẫu "
        "được xáo bằng seed; warmup tổng hợp được loại khỏi độ trễ tập thật. Model digest, định dạng lượng tử hóa, "
        "thời điểm, usage, câu hỏi và hash đầu vào nằm trong run_metadata.json và predictions.jsonl.",
        "",
        "Do khóa API được cung cấp sau, Ollama, OpenAI và Jev được chạy trong các phiên nối tiếp; "
        "chỉ thứ tự ba model Ollama được xáo chung giữa các block. Độ trễ API bao gồm đường mạng từ Colab "
        "và chưa được đối chứng giữa nhiều thời điểm hoặc vùng địa lý. Không diễn giải các chênh lệch này "
        "như hiệu ứng nhân quả của kiến trúc model.",
        "",
        "Ba ứng viên 9B được chọn theo thứ hạng tổng của bài kỹ thuật đã dẫn, không phải tuyên bố ba model 9B "
        "mạnh nhất toàn bộ thị trường. Chạy các tag Linux mặc định và lưu cấu hình thực tế; bài tham khảo dùng "
        "BF16 cho Nimble. Metadata thực tế của lượt này xác nhận cả ba model là GGUF Q8_0, runner llamacpp. "
        "Vì vậy đây là so sánh cấu hình triển khai đo được, không tái lập nguyên trạng bài tham khảo.",
        "",
        f"SHA-256 bộ mẫu: `{metadata['dataset_sha256']}`.",
        "",
        f"Phiên worker mới nhất: {metadata['started_utc']} đến {metadata['completed_utc']}; "
        f"tổng thời gian worker đo được qua các phiên là {metadata['elapsed_seconds']:.1f} giây, "
        "không tính thời gian nghỉ giữa các phiên và toàn bộ tải/cài model trước đó.",
        "",
        "## Hai API cloud",
        "",
    ]
    for provider in ("jev", "openai"):
        value = metadata["provider_availability"][provider]
        lines.append(
            f"- {provider}: key_available={str(value['key_available']).lower()}, nguồn/trạng thái: {value['source_or_reason']}."
        )
    if incomplete:
        lines.append(
            "Model chưa hoàn tất đủ cùng bộ mẫu/lượt được loại khỏi bảng xếp hạng: "
            + json.dumps(incomplete, ensure_ascii=False)
            + "."
        )
    probe_metadata_path = result_dir / "robustness_metadata.json"
    probe_metadata = (
        json.loads(probe_metadata_path.read_text()) if probe_metadata_path.exists() else {}
    )
    external_count = max(
        metadata["external_request_count"],
        probe_metadata.get("external_requests_total_including_main", 0),
    )
    lines += [
        "",
        f"Tổng bộ đếm inference cloud gồm warmup và probe: {external_count}/1.000 request. "
        "OpenAI sử dụng `/v1/decisions`; Jev/Ollama dùng `/v1/systemone`. "
        "Chi phí Colab cần đối soát theo tài khoản, không quy đổi VRAM hay điểm F1 thành hóa đơn.",
        "",
        "| API | Input token trong request chính | Output token trong request chính |",
        "|---|---:|---:|",
    ]
    for backend in ("openai", "jev"):
        provider_rows = [row for row in rows if row["backend"] == backend]
        usage = {
            key: sum((row.get("usage") or {}).get(key, 0) or 0 for row in provider_rows)
            for key in ("input_tokens", "output_tokens")
        }
        lines.append(f"| {backend} | {usage['input_tokens']} | {usage['output_tokens']} |")
    lines += [
        "",
        "Usage này chưa gồm warmup, có cách hạch toán token khác nhau theo nhà cung cấp và không phải hóa đơn. "
        "OpenAI đọc khóa từ Secrets; Jev được cấp khóa qua ô nhập ẩn trong runtime. "
        "Chưa xác minh thành công việc lưu lâu dài secret Jev mới trong giao diện Colab; "
        "khóa không có trong source hoặc artifact kết quả.",
        "",
        "## Thử chỉ dẫn độc hại trong log",
        "",
        "| Model | Đối chứng đúng | Bị kéo đúng nhãn tấn công | ASR có điều kiện | Cặp đổi dự đoán |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in robust:
        asr = "NA" if r["conditional_asr"] is None else f"{r['conditional_asr']:.3f}"
        lines.append(
            f"| {r['model']} | {r['control_correct']} / {r['source_pairs']} | {r['attack_target_successes']} / {r['control_correct']} | {asr} | {r['prediction_flips']} / {r['valid_pairs']} |"
        )
    lines += [
        "",
        "Mỗi mẫu có bản đối chứng cùng tiền tố 1.600 ký tự và bản chèn chỉ dẫn chọn một nhãn sai. "
        "ASR chỉ tính trong các cặp có đối chứng đúng. Sáu cặp là phép thử minh họa, chưa phải kiểm định bảo mật "
        "đầy đủ. Điểm phần này không được cộng vào benchmark chính.",
        "",
        "## Việc cần làm để bảo vệ kết luận trong đồ án",
        "",
        "Bổ sung nhãn sự cố/hành động do người hiểu hệ thống xác nhận bằng annotation_template.csv; "
        "thu thêm khoảng thời gian và sự cố độc lập, đủ log bình thường và từng nhóm lỗi. Chốt tập mới trước khi "
        "so sánh cuối. Trong bộ mới, nên loại các ConsoleNote dạng ha:/// của Jenkins và kiểm tra định nghĩa "
        "WARN so với nhãn Nginx; không chỉnh tập hiện tại theo điểm đã quan sát. Giữ cả các kết quả xấu, lỗi "
        "và điểm yếu trong báo cáo.",
        "",
        "## Nguồn và công cụ",
        "",
        (ROOT / "REFERENCES.md").read_text(encoding="utf-8"),
    ]
    (ROOT / "BAO-CAO-BENCHMARK.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"predictions": len(rows), "models": summary, "robustness": robust}, indent=2))


if __name__ == "__main__":
    main()

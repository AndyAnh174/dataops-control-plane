"""Render article-style figures from aggregate measurements, without opening raw logs."""

import argparse
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from dateutil import __version__ as DATEUTIL_VERSION
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch, Rectangle
from matplotlib.path import Path as MplPath
from PIL import __version__ as PILLOW_VERSION
from pypdf import PdfWriter
from pypdf import __version__ as PYPDF_VERSION

ROOT = Path(__file__).resolve().parent
BLUE = "#2f7cd3"
ORANGE = "#ed6a32"
INK = "#17212d"
STUDY_DATE = datetime(2026, 11, 6, tzinfo=UTC)


def color(row):
    return BLUE if row["backend"] == "ollama" else ORANGE


def label(row):
    suffix = "Ollama" if row["backend"] == "ollama" else "API"
    return f"{row['model']} ({suffix})"


def frame(title, subtitle, footer, width=9.5, height=5.8):
    fig = plt.figure(figsize=(width, height), layout="constrained")
    grid = fig.add_gridspec(3, 1, height_ratios=[0.19, 1, 0.15])
    heading = fig.add_subplot(grid[0])
    heading.axis("off")
    heading.text(0, 0.78, title, fontsize=16, fontweight="bold", color=INK)
    heading.text(0, 0.15, subtitle, fontsize=10.2, color="#4b5563")
    caption = fig.add_subplot(grid[2])
    caption.axis("off")
    caption.text(0, 0.95, footer, va="top", fontsize=9, color="#374151", wrap=True)
    return fig, grid[1]


def clean_axis(ax, axis="x"):
    ax.set_axisbelow(True)
    ax.grid(axis=axis, color="#e1e6ec", linewidth=0.65)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#9ca3af")
    ax.tick_params(length=0, pad=6)


def backend_legend(fig):
    fig.legend(
        handles=[
            Patch(facecolor=BLUE, label="Ollama / A100"),
            Patch(facecolor=ORANGE, edgecolor=INK, hatch="//", label="API"),
        ],
        frameon=False,
        loc="upper right",
        bbox_to_anchor=(0.995, 0.995),
        ncol=2,
        fontsize=8.5,
    )


def export(fig, name, outdir, manifest, description):
    stem = outdir / name
    for fmt in ("svg", "pdf", "png"):
        path = stem.with_suffix("." + fmt)
        if fmt == "pdf":
            metadata = {
                "Creator": "benchmark/decision/make_figures.py",
                "CreationDate": STUDY_DATE,
                "ModDate": STUDY_DATE,
            }
        elif fmt == "svg":
            metadata = {
                "Creator": "benchmark/decision/make_figures.py",
                "Date": "2026-11-06",
                "Description": description,
            }
        else:
            metadata = {
                "Software": "benchmark/decision/make_figures.py",
                "Description": description,
            }
        fig.savefig(path, format=fmt, dpi=300, facecolor="white", metadata=metadata)
        manifest["files"].append(
            {
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "description": description,
            }
        )
    plt.close(fig)


def quality(rows, data, outdir, manifest):
    fig, cell = frame(
        "Decision model: điểm tổng và từng môi trường",
        "158 mẫu giữ riêng × 3 lượt/model · cùng state/câu hỏi · 5 cấu hình đã hoàn tất",
        "Chỉ số proxy = trung bình đều F1 prod/dev/CI; không phải accuracy hay Decision Index của bài tham khảo.\n"
        "Ô đậm: cao nhất mỗi cột. Dev thiếu INFO: F1 cố định 3 nhãn có trần 2/3. Nhãn chưa xác nhận sự cố thật.",
        height=6.1,
    )
    sub = cell.subgridspec(1, 2, width_ratios=[1.1, 1], wspace=0.1)
    bar, heat = fig.add_subplot(sub[0]), fig.add_subplot(sub[1])
    yy = np.arange(len(rows))
    values = [r["equal_environment_proxy_index"] for r in rows]
    bars = bar.barh(yy, values, color=[color(r) for r in rows], height=0.62)
    for rectangle, row, value in zip(bars, rows, values, strict=True):
        if row["backend"] != "ollama":
            rectangle.set_hatch("//")
            rectangle.set_edgecolor("#6d341c")
        bar.text(
            value + 0.015,
            rectangle.get_y() + rectangle.get_height() / 2,
            f"{value:.3f}",
            va="center",
            fontsize=11,
            fontweight="bold",
        )
    bar.set(
        yticks=yy,
        yticklabels=[r["model"] for r in rows],
        xlim=(0, 1),
        xlabel="Chỉ số proxy (0–1)",
        title="Điểm tổng",
    )
    bar.invert_yaxis()
    clean_axis(bar)
    backend_legend(fig)
    matrix = np.array(
        [[r[e + "_macro_f1_including_errors"] for e in ("prod", "dev", "ci")] for r in rows]
    )
    heat.imshow(matrix, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    heat.set(
        xticks=range(3),
        xticklabels=["Prod\nn=29", "Dev\nn=76", "CI\nn=53"],
        yticks=[],
        title="Macro F1 theo nhãn cố định",
    )
    for i, row in enumerate(rows):
        for j, value in enumerate(matrix[i]):
            best = math.isclose(value, float(matrix[:, j].max()), abs_tol=1e-12)
            heat.text(
                j,
                i,
                f"{value:.3f}",
                ha="center",
                va="center",
                fontsize=12,
                color="white" if value > 0.59 else INK,
                fontweight="bold" if best else "normal",
            )
        if row["backend"] != "ollama":
            heat.add_patch(
                Rectangle((-0.49, i - 0.49), 2.98, 0.98, fill=False, edgecolor=ORANGE, linewidth=2)
            )
    heat.set_xticks(np.arange(-0.5, 3), minor=True)
    heat.set_yticks(np.arange(-0.5, 5), minor=True)
    heat.grid(which="minor", color="white", linewidth=2)
    heat.tick_params(which="both", length=0)
    for spine in heat.spines.values():
        spine.set_visible(False)
    export(
        fig,
        "01_quality_heatmap",
        outdir,
        manifest,
        "Thanh xếp hạng và heatmap F1 theo cùng dữ liệu HCMUTE, trung bình ba lượt phụ thuộc.",
    )


def latency(rows, outdir, manifest):
    rows = sorted(rows, key=lambda r: r["p50_ms"])
    fig, cell = frame(
        "Độ trễ native Decision API",
        "474 request hợp lệ/model · warmup được tách riêng",
        "Thanh đặc: p50; thanh gạch: p95. Đây là phân vị độ trễ, không phải khoảng tin cậy.\n"
        "API có độ trễ mạng từ Colab; các nhà cung cấp được chạy trong các phiên nối tiếp.",
    )
    ax = fig.add_subplot(cell)
    yy = np.arange(len(rows))
    for i, row in enumerate(rows):
        ax.barh(i - 0.17, row["p50_ms"], height=0.30, color=color(row))
        ax.barh(
            i + 0.17,
            row["p95_ms"],
            height=0.30,
            facecolor="white",
            edgecolor=color(row),
            hatch="///",
        )
        ax.text(row["p50_ms"] + 7, i - 0.17, f"{row['p50_ms']:.1f}", va="center", fontsize=10)
        ax.text(row["p95_ms"] + 7, i + 0.17, f"{row['p95_ms']:.1f}", va="center", fontsize=10)
    ax.set(
        yticks=yy,
        yticklabels=[label(r) for r in rows],
        xlabel="Độ trễ request (ms)",
        xlim=(0, max(r["p95_ms"] for r in rows) * 1.16),
    )
    ax.invert_yaxis()
    clean_axis(ax)
    export(
        fig,
        "02_latency_p50_p95",
        outdir,
        manifest,
        "So sánh p50 và p95 end-to-end sau warmup; API bao gồm đường mạng từ Colab.",
    )


def tradeoff(rows, outdir, manifest):
    fig, cell = frame(
        "Chất lượng và độ trễ",
        "Cao hơn: chỉ số proxy lớn hơn · trái hơn: p50 thấp hơn",
        "Mỗi điểm là một cấu hình trên cùng 158 mẫu; không suy ra quan hệ nhân quả từ vị trí các điểm.\n"
        "Mốc chất lượng cao nhất trong lượt đo: OpenAI; ứng viên Ollama dẫn đầu: d3.",
    )
    ax = fig.add_subplot(cell)
    offsets = {
        "d3:9b": (9, 14),
        "clef-flash:9b": (-3, -23),
        "nimble-v3:9b": (10, -12),
        "jev-1.14.0": (10, -10),
        "gpt-6-luna": (-8, 12),
    }
    for row in rows:
        marker = "o" if row["backend"] == "ollama" else "^"
        ax.scatter(
            row["p50_ms"],
            row["equal_environment_proxy_index"],
            s=125,
            marker=marker,
            color=color(row),
            edgecolor=INK,
            linewidth=0.7,
            zorder=3,
        )
        dx, dy = offsets[row["model"]]
        ax.annotate(
            row["model"],
            (row["p50_ms"], row["equal_environment_proxy_index"]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=11,
            ha="right" if row["backend"] == "openai" else "left",
        )
    ax.set(
        xlim=(0, max(r["p50_ms"] for r in rows) * 1.25),
        ylim=(0, 1),
        xlabel="p50 end-to-end (ms)",
        ylabel="Chỉ số proxy (0–1)",
    )
    clean_axis(ax, axis="both")
    export(
        fig,
        "03_quality_latency",
        outdir,
        manifest,
        "Scatter đo chất lượng theo độ trễ, đầy đủ trục 0–1 và 0 ms.",
    )


def calibration(rows, data, outdir, manifest):
    fig, cell = frame(
        "Xác suất: Brier và AUROC",
        "Lớp dương: ERROR ở prod/dev; FAILURE ở CI",
        "Trung bình mô tả ba lượt trên các phản hồi hợp lệ. Brier thấp tốt; AUROC cao tốt.\n"
        "Đây là xác suất đối với nhãn nguồn đại diện, chưa được hiệu chuẩn cho sự cố/hành động thực tế.",
    )
    sub = cell.subgridspec(1, 2, wspace=0.18)
    for panel, (field, title, cmap) in enumerate(
        [("brier_valid_only", "Brier ↓", "Blues_r"), ("auc_valid_only", "AUROC ↑", "Blues")]
    ):
        ax = fig.add_subplot(sub[panel])
        matrix = []
        for row in rows:
            values = []
            for env in ("prod", "dev", "ci"):
                samples = [
                    r[field]
                    for r in data["per_environment_repeat"]
                    if r["model"] == row["model"]
                    and r["environment"] == env
                    and r[field] is not None
                ]
                values.append(float(np.mean(samples)) if samples else np.nan)
            matrix.append(values)
        matrix = np.array(matrix)
        ax.imshow(matrix, vmin=0, vmax=1, cmap=cmap, aspect="auto")
        ax.set(
            title=title,
            xticks=range(3),
            xticklabels=["Prod", "Dev", "CI"],
            yticks=range(len(rows)),
            yticklabels=[r["model"] for r in rows] if panel == 0 else [],
        )
        for i in range(len(rows)):
            for j in range(3):
                value = matrix[i, j]
                dark = value < 0.42 if panel == 0 else value > 0.59
                ax.text(
                    j,
                    i,
                    "NA" if np.isnan(value) else f"{value:.3f}",
                    ha="center",
                    va="center",
                    color="white" if dark else INK,
                    fontsize=10.5,
                )
        ax.set_xticks(np.arange(-0.5, 3), minor=True)
        ax.set_yticks(np.arange(-0.5, len(rows)), minor=True)
        ax.grid(which="minor", color="white", linewidth=2)
        ax.tick_params(which="both", length=0)
    export(
        fig,
        "04_probability_quality",
        outdir,
        manifest,
        "Heatmap Brier và AUROC với cùng giới hạn 0–1, không suy ra hiệu chuẩn sự cố thật.",
    )


def injection(rows, data, outdir, manifest):
    fig, cell = frame(
        "Probe chèn chỉ dẫn vào log",
        "6 cặp đối chứng–tấn công/model · 60 quyết định riêng biệt",
        "ASR = số bị kéo đúng nhãn tấn công / số đối chứng dự đoán đúng; không phải chia cho toàn bộ request.\n"
        "Chỉ có 4–5 cặp đủ điều kiện/model; không suy ra khả năng chống prompt injection tổng quát.",
    )
    ax = fig.add_subplot(cell)
    lookup = {r["model"]: r for r in data["robustness"]}
    for i, row in enumerate(rows):
        probe = lookup[row["model"]]
        value = probe["conditional_asr"]
        ax.bar(
            i,
            value,
            color=color(row),
            width=0.58,
            edgecolor=INK,
            hatch="//" if row["backend"] != "ollama" else None,
        )
        ax.text(
            i,
            value + 0.035,
            f"{probe['attack_target_successes']}/{probe['control_correct']}\n{value:.0%}",
            ha="center",
            va="bottom",
            fontsize=12,
            fontweight="bold",
        )
    ax.set(
        xticks=range(len(rows)),
        xticklabels=[r["model"] for r in rows],
        ylim=(0, 1),
        ylabel="ASR có điều kiện (0–1)",
    )
    ax.tick_params(axis="x", labelrotation=15)
    clean_axis(ax, "y")
    export(
        fig,
        "05_prompt_injection",
        outdir,
        manifest,
        "ASR có điều kiện với từng tử số và mẫu số, n nguồn bằng sáu.",
    )


def memory(rows, outdir, manifest):
    local = sorted([r for r in rows if r["backend"] == "ollama"], key=lambda r: r["peak_gpu_gib"])
    fig, cell = frame(
        "Tài nguyên GPU quan sát",
        "Ba cấu hình Ollama Q8_0 trên A100 80 GB",
        "Cực đại nvidia-smi lấy mẫu trong block, có cả warmup/cache; không phải riêng kích thước trọng số.\n"
        "Không quan sát VRAM phía nhà cung cấp API; dữ liệu thiếu không được vẽ thành số 0.",
    )
    ax = fig.add_subplot(cell)
    for i, row in enumerate(local):
        ax.barh(i, row["peak_gpu_gib"], height=0.55, color=BLUE)
        ax.text(
            row["peak_gpu_gib"] + 0.15,
            i,
            f"{row['peak_gpu_gib']:.2f} GiB",
            va="center",
            fontsize=12,
        )
    ax.set(
        yticks=range(len(local)),
        yticklabels=[r["model"] for r in local],
        xlim=(0, max(r["peak_gpu_gib"] for r in local) * 1.2),
        xlabel="Đỉnh GPU trong block (GiB)",
    )
    ax.invert_yaxis()
    clean_axis(ax)
    export(
        fig,
        "06_gpu_memory",
        outdir,
        manifest,
        "Bộ nhớ GPU đỉnh được lấy mẫu, chỉ ba model local có số đo.",
    )


def mermaid_source(path):
    """Read this project's simple quoted-node Mermaid subset for static export."""
    labels, edges = {}, []
    for line in path.read_text(encoding="utf-8").splitlines():
        node = re.match(r'\s*(\w+)(?:\[\(|\(\[|\[)"([^"]+)"', line)
        if node:
            labels[node[1]] = node[2].replace("<br/>", "\n")
        if re.match(r"\s*\w+\s+(?:-->|-\.->)", line):
            parts = re.split(r"\s+(-->|-\.->)\s+", line.strip())
            for i in range(0, len(parts) - 2, 2):
                edges.append((parts[i], parts[i + 2], parts[i + 1] == "-.->"))
    return labels, edges


def diagram(filename, name, layout, routes, outdir, manifest, figsize, limits, title, subtitle):
    labels, edges = mermaid_source(ROOT / "diagrams" / filename)
    assert set(labels) == set(layout), "Static layout differs from canonical Mermaid nodes"
    fig, ax = plt.subplots(figsize=figsize)
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.035, top=0.91)
    ax.set(xlim=(0, limits[0]), ylim=(0, limits[1]))
    ax.axis("off")
    fig.suptitle(title, fontsize=22, fontweight="bold", x=0.04, ha="left", y=0.98)
    fig.text(0.04, 0.935, subtitle, fontsize=13, color="#374151")
    colors = {
        "data": ("#f3f4f6", "#6b7280"),
        "process": ("#dbeafe", "#2563eb"),
        "result": ("#dcfce7", "#16a34a"),
        "caution": ("#fef9c3", "#a16207"),
    }
    for node, (x, y, w, h, kind, size) in layout.items():
        fill, stroke = colors[kind]
        ax.add_patch(
            FancyBboxPatch(
                (x - w / 2, y - h / 2),
                w,
                h,
                boxstyle="round,pad=0.04,rounding_size=0.09",
                facecolor=fill,
                edgecolor=stroke,
                linewidth=1.6,
                zorder=2,
            )
        )
        ax.text(
            x,
            y,
            labels[node],
            ha="center",
            va="center",
            fontsize=size,
            color=INK,
            zorder=3,
            linespacing=1.25,
        )
    for a, b, dashed in edges:
        if (a, b) in routes:
            points = routes[(a, b)]
        else:
            x1, y1, w1, h1, *_ = layout[a]
            x2, y2, w2, h2, *_ = layout[b]
            if abs(y1 - y2) < 0.1:
                sign = 1 if x2 > x1 else -1
                points = [(x1 + sign * w1 / 2, y1), (x2 - sign * w2 / 2, y2)]
            else:
                sign = 1 if y2 > y1 else -1
                points = [(x1, y1 + sign * h1 / 2), (x2, y2 - sign * h2 / 2)]
        path = MplPath(points, [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1))
        ax.add_patch(
            FancyArrowPatch(
                path=path,
                arrowstyle="-|>",
                mutation_scale=15,
                color="#526171",
                linewidth=1.4,
                linestyle="--" if dashed else "-",
                zorder=1,
            )
        )
    fig.text(
        0.04,
        0.012,
        "Số liệu: HCMUTE, 06/11/2026. Mermaid là nguồn sơ đồ chuẩn; SVG/PDF/PNG là bản xuất.",
        fontsize=11,
        color="#4b5563",
    )
    export(fig, name, outdir, manifest, title + "; dữ liệu và nhãn proxy giữ nguyên theo protocol.")


def diagrams(outdir, manifest):
    layout = {
        "raw_logs": (6.5, 16.1, 4.9, 0.9, "data", 16),
        "verify_copies": (6.5, 14.9, 4.9, 0.9, "process", 16),
        "redact_extract": (6.5, 13.7, 4.9, 0.9, "process", 16),
        "deduplicate_group": (6.5, 12.5, 4.9, 0.9, "process", 16),
        "group_split": (6.5, 11.3, 4.9, 0.9, "process", 16),
        "development_validation": (11.2, 11.3, 3.3, 0.9, "data", 14),
        "final_test": (6.5, 10.1, 4.9, 0.9, "result", 16),
        "state_payload": (6.5, 8.9, 4.9, 0.9, "process", 16),
        "proxy_labels": (11.2, 8.9, 3.3, 0.9, "data", 13),
        "shared_harness": (6.5, 7.7, 4.9, 0.9, "process", 16),
        "ollama_models": (2.15, 6.1, 3.9, 1.2, "process", 14),
        "openai_model": (6.5, 6.1, 3.9, 1.2, "process", 14),
        "jev_model": (10.85, 6.1, 3.9, 1.2, "process", 14),
        "predictions": (6.5, 4.6, 4.9, 0.9, "result", 16),
        "paired_probe": (1.85, 4.6, 3.3, 1.1, "caution", 12.5),
        "evaluate_metrics": (6.5, 3.2, 4.9, 0.9, "process", 15),
        "evidence_report": (6.5, 1.7, 4.9, 0.9, "result", 15),
        "next_dataset": (11.2, 1.7, 3.3, 1.1, "caution", 12.5),
    }
    routes = {
        ("final_test", "proxy_labels"): [(8.95, 10.1), (11.2, 10.1), (11.2, 9.35)],
        ("final_test", "paired_probe"): [(4.05, 10.1), (0.08, 10.1), (0.08, 4.6), (0.2, 4.6)],
        ("proxy_labels", "evaluate_metrics"): [
            (12.85, 8.9),
            (13.15, 8.9),
            (13.15, 3.2),
            (8.95, 3.2),
        ],
        ("paired_probe", "evidence_report"): [(1.85, 4.05), (1.85, 1.7), (4.05, 1.7)],
    }
    diagram(
        "benchmark-flow.mmd",
        "07_benchmark_flow",
        layout,
        routes,
        outdir,
        manifest,
        (11, 13),
        (13.4, 17.2),
        "Luồng benchmark Decision HCMUTE",
        "Chạy tuần tự · nhãn đối chiếu không gửi vào model · ô bước tiếp là phần chưa thực hiện",
    )
    probe_layout = {
        "source_cases": (5, 10.2, 7.6, 0.9, "process", 16),
        "common_prefix": (5, 8.9, 7.6, 0.9, "process", 16),
        "control_state": (2.6, 7.5, 4.3, 0.95, "result", 14),
        "injected_state": (7.4, 7.5, 4.3, 0.95, "caution", 14),
        "shared_question": (5, 6, 7.6, 0.9, "process", 16),
        "paired_answers": (5, 4.6, 7.6, 0.9, "result", 16),
        "conditional_asr": (5, 3.2, 7.6, 0.9, "process", 16),
        "measured_outcome": (5, 1.8, 8.7, 0.95, "result", 14),
        "scope_limit": (5, 0.5, 8.7, 0.9, "caution", 13.5),
    }
    diagram(
        "prompt-injection-probe.mmd",
        "08_injection_design",
        probe_layout,
        {},
        outdir,
        manifest,
        (9, 10),
        (10, 11),
        "Thiết kế đối chứng prompt injection",
        "Sáu cặp nguồn; phép thử riêng, không cộng vào điểm benchmark chính",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/hcmute-20261106.json")
    parser.add_argument("--output", type=Path, default=ROOT / "figures")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()) and not args.force:
        parser.error("Output directory is nonempty; use --force to regenerate derived figures")
    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.input.read_text(encoding="utf-8"))
    rows = sorted(data["models"], key=lambda r: r["equal_environment_proxy_index"], reverse=True)
    assert len(rows) == 5 and all(r["predictions"] == 474 for r in rows)
    manifest = {
        "study": data["study"],
        "source_data_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "matplotlib": matplotlib.__version__,
        "numpy": np.__version__,
        "pillow": PILLOW_VERSION,
        "python_dateutil": DATEUTIL_VERSION,
        "pypdf": PYPDF_VERSION,
        "dpi_png": 300,
        "uncertainty": "Descriptive means of three dependent repeats; no population confidence intervals.",
        "cost": "No billing-cost measurement was made; latency is not converted into dollar cost.",
        "files": [],
    }
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "svg.hashsalt": "hcmute-20261106",
            "axes.titleweight": "bold",
            "axes.labelcolor": INK,
            "text.color": INK,
        }
    ):
        quality(rows, data, args.output, manifest)
        latency(rows, args.output, manifest)
        tradeoff(rows, args.output, manifest)
        calibration(rows, data, args.output, manifest)
        injection(rows, data, args.output, manifest)
        memory(rows, args.output, manifest)
        diagrams(args.output, manifest)
    writer = PdfWriter()
    for artifact in manifest["files"]:
        if artifact["file"].endswith(".pdf"):
            writer.append(args.output / artifact["file"])
    writer.add_metadata(
        {
            "/Title": "HCMUTE Decision benchmark - 8 figures",
            "/Creator": "benchmark/decision/make_figures.py",
        }
    )
    book_path = args.output / "all_charts.pdf"
    with book_path.open("wb") as output:
        writer.write(output)
    manifest["files"].append(
        {
            "file": book_path.name,
            "bytes": book_path.stat().st_size,
            "pages": len(writer.pages),
            "sha256": hashlib.sha256(book_path.read_bytes()).hexdigest(),
            "description": "Tám hình vector ghép thành một PDF để trình bày.",
        }
    )
    writer.close()
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"figures": 8, "exported_files": len(manifest["files"]), "output": str(args.output)}
        )
    )


if __name__ == "__main__":
    main()

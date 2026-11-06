# ruff: noqa: E501
"""Create auditable PNG/SVG charts from completed checkpoints, with explicit N/A."""

import argparse
import csv
import hashlib
import json
import platform
from pathlib import Path


def read(path, fallback=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def make(root, kind):
    import matplotlib
    import matplotlib.pyplot as plt
    import numpy as np

    root = Path(root)
    output = root / "figures"
    output.mkdir(exist_ok=True, parents=True)
    produced = []
    input_hashes = {}
    for path in (root / "results").rglob("*.json"):
        input_hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()

    def save(fig, name, caption):
        fig.text(0.02, 0.015, caption, fontsize=8, va="bottom", wrap=True)
        fig.subplots_adjust(bottom=0.20)
        for extension in ["png", "svg"]:
            path = output / (name + "." + extension)
            fig.savefig(path, dpi=300, facecolor="white")
            produced.append(
                {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        plt.close(fig)

    def bars(ax, names, values, title, xlabel, color="#0072B2"):
        positions = np.arange(len(names))
        for i, value in enumerate(values):
            if value is None:
                ax.text(
                    0.02,
                    i,
                    "N/A - incomplete or unavailable",
                    va="center",
                    fontsize=8,
                    color="#666666",
                )
            else:
                ax.barh(i, value, color=color, height=0.65)
                ax.text(value + 0.012, i, f"{value:.3f}", va="center", fontsize=8)
        ax.set(
            yticks=positions,
            yticklabels=names,
            title=title,
            xlabel=xlabel,
            xlim=(0, 1.12),
            ylim=(-0.7, len(names) - 0.3),
        )
        ax.invert_yaxis()
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis="x", alpha=0.2)

    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 12,
            "svg.fonttype": "none",
        }
    ):
        if kind == "llm":
            rows = read(root / "results/summary.json", [])
            if not rows:
                raise ValueError("No LLM summary; do not invent chart data")
            names = [r["model"] for r in rows]
            fig, axes = plt.subplots(
                1, 2, figsize=(13, 6.2), gridspec_kw={"width_ratios": [1.2, 1]}
            )
            bars(
                axes[0],
                names,
                [r["mean_environment_macro_f1"] for r in rows],
                "Current chat LLM configurations",
                "Mean fixed-label macro F1",
            )
            previous = read(root / "previous_decision_aggregate.json", {})
            historical = previous.get("models", previous.get("comparison", []))
            # Historical exports may use a mapping. Only use named observed fields.
            if isinstance(historical, dict):
                historical = [{"model": name, **data} for name, data in historical.items()]
            usable = []
            for row in historical if isinstance(historical, list) else []:
                columns = [
                    row.get(env + "_macro_f1_including_errors") for env in ["prod", "dev", "ci"]
                ]
                if all(value is not None for value in columns):
                    usable.append((row.get("model", row.get("name", "unknown")), sum(columns) / 3))
            if usable:
                bars(
                    axes[1],
                    [name for name, _ in usable],
                    [value for _, value in usable],
                    "Historical native Decision reference",
                    "Earlier measurement; different API",
                    color="#666666",
                )
            else:
                axes[1].axis("off")
                axes[1].text(
                    0.1,
                    0.6,
                    "Historical reference retained as JSON.\nNo compatible aggregate field inferred.",
                )
            fig.subplots_adjust(left=0.18, right=0.97, wspace=0.75)
            save(
                fig,
                "01_llm_quality",
                "Same frozen 158 cases; 3 repeat blocks, not 474 independent cases. N/A is not zero.\nDev has no INFO; fixed three-label macro F1 is capped at 2/3. Historical data are not concurrent controls.",
            )
            heat = []
            for row in rows:
                heat.append(
                    [
                        sum(
                            g["metrics"]["macro_f1"]
                            for g in row["groups"]
                            if g["environment"] == env
                        )
                        / 3
                        if row["eligible_for_ranking"]
                        else np.nan
                        for env in ["prod", "dev", "ci"]
                    ]
                )
            fig, ax = plt.subplots(figsize=(8, 6))
            image = ax.imshow(
                np.asarray(heat),
                vmin=0,
                vmax=1,
                cmap=matplotlib.colormaps["viridis"].with_extremes(bad="#dddddd"),
                aspect="auto",
            )
            ax.set(
                yticks=range(len(names)),
                yticklabels=names,
                xticks=range(3),
                xticklabels=["Prod (n=29)", "Dev (n=76)", "CI (n=53)"],
                title="Classification quality by environment",
            )
            for i, row in enumerate(heat):
                for j, value in enumerate(row):
                    ax.text(
                        j,
                        i,
                        "N/A" if np.isnan(value) else f"{value:.3f}",
                        ha="center",
                        va="center",
                        color="white" if not np.isnan(value) and value < 0.65 else "black",
                    )
            fig.colorbar(image, ax=ax, label="Mean fixed-label macro F1")
            fig.subplots_adjust(left=0.30, right=0.87)
            save(
                fig,
                "02_llm_environment",
                "Three repeat-block means on the same holdout. Source-derived proxy labels, not independently adjudicated incidents.\nOnly 3 prod ERROR examples; prod/dev each have one source group.",
            )
            fig, ax = plt.subplots(figsize=(9, 5.8))
            bars(
                ax,
                names,
                [r["valid_of_planned"] for r in rows],
                "Validated output / planned predictions",
                "Coverage (all planned cases)",
                color="#009E73",
            )
            fig.subplots_adjust(left=0.28, right=0.95)
            save(
                fig,
                "03_llm_coverage",
                "Coverage includes missing, failed and ambiguous pending attempts in the planned denominator.\nQuality is ranked only after all planned cases have an attempt; transport/schema errors count as incorrect.",
            )
            journal_rows = {}
            journal_path = root / "results/attempts.jsonl"
            if journal_path.exists():
                for line in journal_path.read_text(encoding="utf-8").splitlines():
                    entry = json.loads(line)
                    journal_rows[entry["key"]] = entry
                fig, ax = plt.subplots(figsize=(10, 6))
                for i, name in enumerate(names):
                    values = [
                        entry["latency_ms"]
                        for entry in journal_rows.values()
                        if entry.get("model") == name
                        and not entry.get("warmup")
                        and entry.get("status") == "ok"
                    ]
                    if values:
                        median, upper = np.quantile(values, [0.5, 0.95])
                        ax.plot([median, upper], [i, i], color="#666666", linewidth=2)
                        ax.scatter([median], [i], color="#0072B2", marker="o", s=55)
                        ax.scatter([upper], [i], color="#D55E00", marker="|", s=100)
                        ax.annotate(
                            f"n={len(values)}",
                            (upper, i),
                            xytext=(6, 5),
                            textcoords="offset points",
                            fontsize=8,
                        )
                    else:
                        ax.text(0, i, "N/A", va="center")
                ax.set(
                    yticks=range(len(names)),
                    yticklabels=names,
                    xlim=(0, None),
                    xlabel="End-to-end warm request latency (ms)",
                    title="Latency: circle = p50, tick = p95",
                )
                ax.invert_yaxis()
                ax.grid(axis="x", alpha=0.2)
                fig.subplots_adjust(left=0.26, right=0.92)
                save(
                    fig,
                    "04_llm_latency",
                    "Valid observed responses only; synthetic warmups excluded. p95 is a percentile, not a confidence interval.\n"
                    "Network and native transport constraints differ. Observed n is shown; coverage is reported separately.",
                )
            csv_rows = [
                {
                    "model": r["model"],
                    "complete": r["eligible_for_ranking"],
                    "macro_f1": r["mean_environment_macro_f1"],
                    "coverage": r["valid_of_planned"],
                }
                for r in rows
            ]
        else:
            rows = read(root / "results/retrieval_summary.json", [])
            if not rows:
                raise ValueError("No retrieval summary; do not invent chart data")
            names = [r["model"] for r in rows]
            fig, axes = plt.subplots(1, 3, figsize=(16, 6.5))
            for ax, column, label in zip(
                axes,
                ["recall_at_5", "mrr_at_5", "ndcg_at_5"],
                ["Recall@5", "MRR@5", "nDCG@5"],
                strict=True,
            ):
                bars(ax, names, [r[column] for r in rows], label, label)
            fig.subplots_adjust(left=0.17, right=0.98, wspace=1)
            save(
                fig,
                "01_embedding_retrieval",
                "166 code windows; 48 EN/VI queries form 24 paired intent groups. Native vector dimensions differ.\nThree query repeats are repeated measurements. BM25 is the concurrent lexical control; N/A is not zero.",
            )
            fig, ax = plt.subplots(figsize=(10, 6))
            plotted = False
            for i, row in enumerate(rows):
                if row["ndcg_at_5"] is not None and row.get("query_p50_ms") is not None:
                    ax.scatter(
                        row["query_p50_ms"],
                        row["ndcg_at_5"],
                        marker=["o", "s", "^", "D", "P", "X", "v"][i % 7],
                        s=65,
                        label=row["model"],
                    )
                    plotted = True
            ax.set(
                xlabel="Warm query embedding p50 (ms; network included)",
                ylabel="nDCG@5",
                ylim=(0, 1.05),
                title="Embedding quality and latency",
            )
            ax.grid(alpha=0.2)
            if plotted:
                ax.legend(loc="lower right", fontsize=8)
            save(
                fig,
                "02_embedding_latency",
                "Single-query timings after synthetic warmup. Query delay excluded; index time reported separately.\nNo cloud VRAM estimate or monetary cost inferred. BM25 has no embedding API latency.",
            )
            readers = read(root / "results/reader_summary.json", [])
            if readers:
                sources = list(dict.fromkeys(row["retriever"] for row in readers))
                engines = ["gpt-6-luna", "d3:9b"]
                matrix = [
                    [
                        next(
                            (
                                row["selected_relevant_of_planned"]
                                for row in readers
                                if row["reader"] == reader and row["retriever"] == source
                            ),
                            None,
                        )
                        for source in sources
                    ]
                    for reader in engines
                ]
                values = np.asarray([[np.nan if v is None else v for v in row] for row in matrix])
                fig, ax = plt.subplots(figsize=(13, 5))
                image = ax.imshow(
                    values,
                    vmin=0,
                    vmax=1,
                    aspect="auto",
                    cmap=matplotlib.colormaps["viridis"].with_extremes(bad="#dddddd"),
                )
                ax.set(
                    xticks=range(len(sources)),
                    xticklabels=sources,
                    yticks=range(2),
                    yticklabels=engines,
                    title="Fixed readers: selected passage contains the target declaration",
                )
                ax.tick_params(axis="x", rotation=35)
                for i in range(2):
                    for j in range(len(sources)):
                        v = values[i, j]
                        ax.text(
                            j,
                            i,
                            "N/A" if np.isnan(v) else f"{v:.2f}",
                            ha="center",
                            va="center",
                            color="white" if not np.isnan(v) and v < 0.65 else "black",
                        )
                fig.colorbar(image, ax=ax, label="Relevant selection / 48 planned queries")
                fig.subplots_adjust(left=0.12, right=0.90, bottom=0.35)
                fig.text(
                    0.02,
                    0.015,
                    "Oracle is an upper context control. This scores passage selection, not full code-QA correctness.\n"
                    "48 paired EN/VI queries from 24 intents; incomplete/unavailable reader groups remain N/A.",
                    fontsize=8,
                    va="bottom",
                )
                # Preserve room for labels by bypassing generic bottom adjustment.
                for extension in ["png", "svg"]:
                    path = output / ("03_fixed_readers." + extension)
                    fig.savefig(path, dpi=300, facecolor="white")
                    produced.append(
                        {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    )
                plt.close(fig)
            csv_rows = [
                {
                    k: r.get(k)
                    for k in [
                        "model",
                        "complete",
                        "coverage",
                        "recall_at_5",
                        "mrr_at_5",
                        "ndcg_at_5",
                        "query_p50_ms",
                        "query_p95_ms",
                    ]
                }
                for r in rows
            ]
        with (output / "comparison.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            writer.writerows(csv_rows)
    manifest = {
        "kind": kind,
        "inputs": input_hashes,
        "outputs": produced,
        "python": platform.python_version(),
        "matplotlib": matplotlib.__version__,
        "numpy": np.__version__,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "meaning": "Exploratory deployed-configuration comparison, not universal capability ranking",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--kind", choices=["llm", "embedding"], required=True)
    args = parser.parse_args()
    print(json.dumps(make(args.root, args.kind), indent=2))

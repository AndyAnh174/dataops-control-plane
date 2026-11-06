"""Render the two benchmark protocols; this figure contains no measured scores."""

import argparse
from pathlib import Path


def make(output):
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.fonttype": "none"}):
        fig, ax = plt.subplots(figsize=(14, 8))
        ax.set(xlim=(0, 1), ylim=(0, 1))
        ax.axis("off")
        ax.text(0.02, 0.96, "HCMUTE DataOps benchmark protocol", fontsize=20, weight="bold")
        ax.text(
            0.02,
            0.91,
            "Frozen inputs, explicit failures, separate tasks and auditable outputs",
            fontsize=12,
            color="#444444",
        )
        lanes = [
            (
                0.61,
                "A. Chat LLMs on log/build classification",
                "#0072B2",
                [
                    "Frozen redacted logs\n339 exported / 158 test\nProd 29 / dev 76 / CI 53",
                    "6 deployed LLM configs\n3 blocks on same cases\nIdentical prompt and labels",
                    "Strict output validation\nNo repair or auto retry\n"
                    "Invalid replies count as errors",
                    "Fixed-label macro F1\nCoverage and p50/p95\nHistorical Decision reference",
                ],
            ),
            (
                0.25,
                "B. Code embeddings and fixed downstream readers",
                "#009E73",
                [
                    "Frozen public code\n49 files / 166 windows\n24 intents in EN and VI",
                    "8 embedding configs\nNative vector dimensions\nBM25 keyword baseline",
                    "Exact cosine top 5\n48 queries / 3 repeats\nRecall, MRR and nDCG",
                    "Fixed GPT / D3 readers\nSame top 5; oracle control\n"
                    "Relevant passage selection",
                ],
            ),
        ]
        for y, title, color, labels in lanes:
            ax.text(0.02, y + 0.22, title, fontsize=13, weight="bold", color=color)
            for index, label in enumerate(labels):
                x = 0.02 + index * 0.25
                ax.add_patch(
                    FancyBboxPatch(
                        (x, y),
                        0.21,
                        0.17,
                        boxstyle="round,pad=0.01",
                        facecolor="#F6F8FA",
                        edgecolor=color,
                        linewidth=1.5,
                    )
                )
                ax.text(x + 0.105, y + 0.085, label, ha="center", va="center", fontsize=10)
                if index < 3:
                    ax.add_patch(
                        FancyArrowPatch(
                            (x + 0.222, y + 0.085),
                            (x + 0.238, y + 0.085),
                            arrowstyle="-|>",
                            mutation_scale=14,
                            color=color,
                        )
                    )
        ax.text(
            0.02,
            0.11,
            "Evidence: hashed inputs + execution source + per-case journals + metadata + charts",
            fontsize=11,
            weight="bold",
        )
        ax.text(
            0.02,
            0.05,
            "Limits: source-derived proxy labels; repeated cases are not independent samples.\n"
            "Code gold is provisional. Neither study establishes incident RCA or recovery safety.",
            fontsize=10,
            color="#444444",
        )
        fig.subplots_adjust(left=0.03, right=0.99, top=0.99, bottom=0.02)
        for extension in ("png", "svg"):
            fig.savefig(output / f"benchmark-method.{extension}", dpi=300, facecolor="white")
        plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    make(parser.parse_args().out)

"""Render the public Markdown evidence report and charts as a landscape A4 PDF."""

import argparse
import os
from pathlib import Path
from xml.sax.saxutils import escape


def export(source, figures, output):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Image,
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    linux_fonts = Path("/usr/share/fonts/truetype/dejavu")
    if (windows_fonts / "arial.ttf").exists():
        regular, bold = windows_fonts / "arial.ttf", windows_fonts / "arialbd.ttf"
    else:
        regular, bold = linux_fonts / "DejaVuSans.ttf", linux_fonts / "DejaVuSans-Bold.ttf"
    for name, path in [("ReportBody", regular), ("ReportBold", bold)]:
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily("ReportBody", normal="ReportBody", bold="ReportBold")
    page_width, page_height = landscape(A4)
    margin = 38
    usable_width = page_width - 2 * margin
    body = ParagraphStyle("Body", fontName="ReportBody", fontSize=10, leading=15, spaceAfter=10)
    heading = ParagraphStyle(
        "Heading", parent=body, fontName="ReportBold", fontSize=16, leading=21, spaceBefore=12
    )
    title = ParagraphStyle("Title", parent=heading, fontSize=23, leading=29, spaceAfter=18)
    cell = ParagraphStyle("Cell", parent=body, fontSize=8.5, leading=12, spaceAfter=0)
    story = []
    text = Path(source).read_text(encoding="utf-8").replace("—", "-").replace("–", "-")
    lines = text.splitlines()
    table_rows = []
    section_count = 0

    def flush_table():
        if not table_rows:
            return
        columns = len(table_rows[0])
        first = usable_width * (0.24 if columns > 4 else 0.23)
        widths = [first] + [(usable_width - first) / (columns - 1)] * (columns - 1)
        data = [[Paragraph(escape(value), cell) for value in row] for row in table_rows]
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F5F7FA")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#64748B")),
                ]
            )
        )
        story.extend([table, Spacer(1, 12)])
        table_rows.clear()

    for line in lines:
        if line.startswith("|"):
            if "---" not in line:
                table_rows.append([value.strip() for value in line.strip("|").split("|")])
            continue
        flush_table()
        if not line.strip():
            continue
        if line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), title))
        elif line.startswith("## "):
            if section_count:
                story.append(PageBreak())
            story.append(Paragraph(escape(line[3:]), heading))
            section_count += 1
        else:
            story.append(Paragraph(escape(line), body))
    flush_table()
    for figure in figures:
        path = Path(figure)
        if not path.is_file():
            raise FileNotFoundError(path)
        story.append(PageBreak())
        image = Image(str(path))
        scale = min(usable_width / image.imageWidth, 440 / image.imageHeight)
        image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
        story.append(KeepTogether([Paragraph(escape(path.stem.replace("_", " ")), heading), image]))
    story += [
        PageBreak(),
        Paragraph("Nguồn và tái lập", heading),
        Paragraph(
            "Source benchmark và protocol: "
            '<link href="https://github.com/AndyAnh174/dataops-control-plane/pull/8" '
            'color="#0072B2">AndyAnh174/dataops-control-plane - PR #8</link>. '
            "Các file JSON public giữ checksum đầu vào. ZIP riêng giữ từng case và "
            "source thực thi; biểu đồ là bản tổng hợp từ checkpoint đó.",
            body,
        ),
        Paragraph(
            "Tài liệu phương pháp: Scientific Agent Skills, Kassis và cộng sự (2026), "
            '<link href="https://doi.org/10.48550/arXiv.2609.00065" '
            'color="#0072B2">doi:10.48550/arXiv.2609.00065</link>. '
            "Hỗ trợ quy trình, không dùng làm nguồn điểm model.",
            body,
        ),
    ]

    def footer(canvas, document):
        canvas.setFont("ReportBody", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(margin, 20, "HCMUTE DataOps | benchmark ngày 06/11/2026")
        canvas.drawRightString(page_width - margin, 20, f"Trang {document.page}")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output),
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=28,
        bottomMargin=38,
        title="Báo cáo benchmark LLM và embedding DataOps",
        author="AndyAnh174",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--figures", nargs="*", default=[])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(export(args.source, args.figures, args.out))

## 技术交付物

### 数据驱动 PDF 报告生成

```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, Image, PageBreak
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from dataclasses import dataclass
from typing import List
import datetime


@dataclass
class BrandConfig:
    primary_color: str = "#1a56db"
    secondary_color: str = "#6b7280"
    font_family: str = "SourceHanSansSC"  # 思源黑体
    font_path: str = "/usr/share/fonts/SourceHanSansSC-Regular.ttf"
    logo_path: str = "assets/logo.png"


class ReportGenerator:
    """数据驱动的 PDF 报告生成器"""

    def __init__(self, brand: BrandConfig):
        self.brand = brand
        self._register_fonts()
        self.styles = self._build_styles()

    def _register_fonts(self):
        """注册中文字体 —— PDF 必须嵌入字体"""
        pdfmetrics.registerFont(TTFont(
            self.brand.font_family, self.brand.font_path
        ))

    def _build_styles(self):
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name='BrandTitle',
            fontName=self.brand.font_family,
            fontSize=24,
            textColor=HexColor(self.brand.primary_color),
            spaceAfter=12 * mm,
        ))
        styles.add(ParagraphStyle(
            name='BrandBody',
            fontName=self.brand.font_family,
            fontSize=10,
            leading=16,
            textColor=HexColor("#374151"),
        ))
        return styles

    def generate(self, data: dict, output_path: str):
        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            leftMargin=20*mm, rightMargin=20*mm,
            topMargin=25*mm, bottomMargin=20*mm,
        )

        elements = []

        # 封面
        elements.append(Image(self.brand.logo_path, width=40*mm, height=15*mm))
        elements.append(Spacer(1, 20*mm))
        elements.append(Paragraph(data["title"], self.styles["BrandTitle"]))
        elements.append(Paragraph(
            f"生成日期：{datetime.date.today().isoformat()}",
            self.styles["BrandBody"]
        ))
        elements.append(PageBreak())

        # 数据表格
        if "table_data" in data:
            elements.append(self._build_table(data["table_data"]))

        doc.build(elements, onFirstPage=self._header_footer,
                  onLaterPages=self._header_footer)
        return output_path

    def _build_table(self, table_data: dict):
        headers = table_data["headers"]
        rows = table_data["rows"]
        data = [headers] + rows

        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), self.brand.font_family),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BACKGROUND', (0, 0), (-1, 0), HexColor(self.brand.primary_color)),
            ('TEXTCOLOR', (0, 0), (-1, 0), HexColor("#ffffff")),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [HexColor("#f9fafb"), HexColor("#ffffff")]),
            ('GRID', (0, 0), (-1, -1), 0.5, HexColor("#e5e7eb")),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        return table

    def _header_footer(self, canvas, doc):
        canvas.setFont(self.brand.font_family, 8)
        canvas.setFillColor(HexColor(self.brand.secondary_color))
        canvas.drawString(
            20*mm, 10*mm,
            f"第 {doc.page} 页 | 机密文件"
        )
```

### 数据驱动 PPTX 幻灯片

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN


def generate_data_slide(prs: Presentation, title: str,
                        metrics: list[dict]):
    """生成数据指标卡片幻灯片"""
    slide_layout = prs.slide_layouts[6]  # 空白布局
    slide = prs.slides.add_slide(slide_layout)

    # 标题
    txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.3),
                                      Inches(9), Inches(0.8))
    tf = txBox.text_frame
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(28)
    p.font.bold = True
    p.font.color.rgb = RGBColor(0x1a, 0x56, 0xdb)

    # 指标卡片网格
    card_width = Inches(2.8)
    card_height = Inches(1.5)
    cols = 3
    start_x = Inches(0.5)
    start_y = Inches(1.5)
    gap = Inches(0.3)

    for i, metric in enumerate(metrics):
        col = i % cols
        row = i // cols
        x = start_x + col * (card_width + gap)
        y = start_y + row * (card_height + gap)

        # 卡片背景
        shape = slide.shapes.add_shape(
            1, x, y, card_width, card_height  # 1 = 圆角矩形
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(0xf3, 0xf4, 0xf6)
        shape.line.fill.background()

        # 指标值
        txBox = slide.shapes.add_textbox(
            x + Inches(0.2), y + Inches(0.2),
            card_width - Inches(0.4), Inches(0.8)
        )
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        p.text = str(metric["value"])
        p.font.size = Pt(32)
        p.font.bold = True
        p.alignment = PP_ALIGN.LEFT

        # 指标名称
        p2 = tf.add_paragraph()
        p2.text = metric["label"]
        p2.font.size = Pt(12)
        p2.font.color.rgb = RGBColor(0x6b, 0x72, 0x80)

    return slide
```

### 大数据量 Excel 流式写入

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def generate_large_report(data_iterator, output_path: str,
                          headers: list[str]):
    """
    流式生成大数据量 Excel
    使用 write_only 模式，内存占用恒定
    """
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("报告数据")

    # 样式定义
    header_font = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="1a56db", fill_type="solid")
    data_font = Font(name="微软雅黑", size=10)
    thin_border = Border(
        bottom=Side(style="thin", color="e5e7eb")
    )

    # 写入表头
    header_row = []
    for h in headers:
        from openpyxl.cell import WriteOnlyCell
        cell = WriteOnlyCell(ws, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        header_row.append(cell)
    ws.append(header_row)

    # 流式写入数据行
    row_count = 0
    for row_data in data_iterator:
        cells = []
        for value in row_data:
            cell = WriteOnlyCell(ws, value=value)
            cell.font = data_font
            cell.border = thin_border
            cells.append(cell)
        ws.append(cells)
        row_count += 1

    # 自动列宽（基于表头长度估算）
    for i, header in enumerate(headers, 1):
        col_letter = get_column_letter(i)
        ws.column_dimensions[col_letter].width = max(len(header) * 2 + 4, 12)

    wb.save(output_path)
    return {"rows": row_count, "path": output_path}
```

"""资料库的文件解析：把 CEO 上传的 PDF、Word 和文本文件变成可以切块的 Markdown 文本。

- PDF（pypdf）：每页一个"## 第 N 页"标题，检索结果能引用到页；去掉每页重复出现的页眉页脚和单独的页码行
- Word（python-docx）：标题样式转成 Markdown 标题，表格按行转成"单元格 | 单元格"
- 文本文件：原样读取
扫描版 PDF 提取不到文字时抛 NoText，入库时明确提示，不会默默变成空的。不做 OCR。
"""
import re
from collections import Counter
from pathlib import Path

from .chunking import TEXT_SUFFIXES

DOC_SUFFIXES = {".pdf", ".docx"}
LIBRARY_SUFFIXES = TEXT_SUFFIXES | DOC_SUFFIXES
PAGE_NUMBER = re.compile(r"^\s*(第\s*\d+\s*页|[-—\s]*\d+\s*[-—\s]*|\d+\s*/\s*\d+|page\s*\d+(\s*of\s*\d+)?)\s*$", re.I)


class LoadError(Exception):
    """文件解析失败（文件损坏、缺少解析库）。消息写给用户看。"""


class NoText(LoadError):
    """解析成功但没有文字（扫描版 PDF、只有图片的文档）。"""


def load_text(path: Path) -> str:
    """返回 Markdown 文本。PDF / Word 以外的文件按 UTF-8 文本读取。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf(path)
    elif suffix == ".docx":
        text = _docx(path)
    else:
        text = path.read_bytes().decode("utf-8", errors="replace")
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()
    if not re.sub(r"(?m)^#+ .*$", "", text).strip():
        raise NoText("没有提取到文字（可能是扫描版 PDF 或只有图片），这份资料检索不到。可以换成带文字的版本再上传")
    return text


def chunk_name(path: str) -> str:
    """切块时按什么格式切：PDF、Word 解析出来的是 Markdown。"""
    return path + ".md" if Path(path).suffix.lower() in DOC_SUFFIXES else path


def _pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise LoadError("解析 PDF 需要先安装：pip install -r requirements-kb.txt") from None
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise LoadError("PDF 有密码保护，无法读取") from None
        pages = [(p.extract_text() or "").replace("\r\n", "\n") for p in reader.pages]
    except LoadError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError) as e:
        raise LoadError(f"PDF 解析失败（文件可能损坏）：{e}") from None
    repeated = _repeated_edges(pages)
    out = []
    for no, page in enumerate(pages, 1):
        lines = [ln.rstrip() for ln in page.split("\n")]
        lines = [ln for ln in lines if ln.strip() and ln.strip() not in repeated and not PAGE_NUMBER.match(ln)]
        if lines:
            out.append(f"## 第 {no} 页\n\n" + "\n".join(lines))
    return "\n\n".join(out)


def _repeated_edges(pages: list[str]) -> set[str]:
    """页眉页脚：每页开头或结尾两行里，在一半以上的页（至少 3 页）重复出现的文字。"""
    if len(pages) < 3:
        return set()
    counts = Counter()
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        counts.update(set(lines[:2] + lines[-2:]))
    return {ln for ln, n in counts.items() if n >= max(3, len(pages) // 2) and len(ln) <= 80}


def _docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        raise LoadError("解析 Word 需要先安装：pip install -r requirements-kb.txt") from None
    try:
        document = docx.Document(str(path))
    except Exception as e:  # python-docx 对损坏文件抛的异常类型很杂
        raise LoadError(f"Word 文件解析失败（文件可能损坏，或者是旧的 .doc 格式）：{e}") from None
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    out = []
    for block in document.element.body.iterchildren():  # 按文档里的顺序遍历段落和表格
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(block, document)
            text = p.text.strip()
            if not text:
                continue
            level = _heading_level(p.style.name if p.style is not None else "")
            out.append(f"{'#' * level} {text}" if level else text)
        elif tag == "tbl":
            rows = []
            for row in Table(block, document).rows:
                cells = list(dict.fromkeys(c.text.strip().replace("\n", " ") for c in row.cells))  # 合并单元格会重复
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                out.append("\n".join(rows))
    return "\n\n".join(out)


def _heading_level(style: str) -> int:
    """"Heading 2" / "标题 2" / "Title" → Markdown 标题级别（最多 3 级，更深的当正文）。"""
    s = style.lower()
    if s in ("title", "标题"):
        return 1
    m = re.match(r"(heading|标题)\s*(\d)", s)
    return int(m.group(2)) if m and int(m.group(2)) <= 3 else 0

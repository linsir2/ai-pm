"""Office 文档（Word / Excel）的真解析：产出块，交给 `extract.LocalMaterialParser` 封装成信息包。

为什么单独一个模块：这两条路都要第三方库，且形状与"文本 / PDF / HTML"那三条不一样
（表格、工作表）。`extract.py` 只管**分派**与"抽不出就说抽不出"这条纪律。

三条规矩（与 `extract.py` 同一套）：
1. **按文档真实顺序**走（段落与表格交错，不把表格搬到末尾）；
2. 锚点是**人话**："第 3–7 段" / "表格 2（表头：角色、能否导出）" / "工作表「指标」第 1–12 行"；
3. 超长**截断并写明**（返回 `truncated`），不假装完整。

读不了（坏 zip / 改名文件 / 抽不出文字）一律抛 `UnsupportedMaterial`——由调用方补上文件名。
"""

import io
import re

from docx import Document as open_docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from pmstudio.application.dto import MaterialChunk
from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.infrastructure.tools.extract import MAX_MATERIAL_CHARS

#: 解析器身份（写进信息包，材料卡上看得见；换 docling 之后这一组会变）
DOCX_PARSER_ID = "python-docx"

#: 表格按行渲染时的列分隔符（**不假装有结构**：`MaterialTable` 至今没有消费者）
CELL_JOIN = " | "

#: 标题样式：`Heading 1` / `标题 1` / `Title` / `标题`（中文 Word 用的是后面这几种）
_HEADING_STYLE = re.compile(r"^(?P<name>heading|标题|title)\s*(?P<level>\d+)?$", re.IGNORECASE)


def docx_chunks(data: bytes) -> tuple[tuple[MaterialChunk, ...], bool]:
    """Word → 块（按文档真实顺序；标题起新块、表格独占一块）。

    返回 `(chunks, truncated)`。抽不出文字（空文档）→ `UnsupportedMaterial`。
    """

    document = _open(data)
    body_children = list(document.element.body.iterchildren())
    chunks: list[MaterialChunk] = []
    buffer: list[str] = []
    headings: list[tuple[int, str]] = []
    state = {"start": 1, "end": 1, "used": 0}
    truncated = False
    table_number = 0

    def flush() -> None:
        """把攒着的正文收成一块（空 buffer 什么都不做——收尾调用可以重复）。"""

        body = "\n".join(buffer).strip()
        if body:
            chunks.append(
                MaterialChunk(
                    chunk_id=f"{DOCX_PARSER_ID}#c{len(chunks):02d}",
                    ordinal=len(chunks),
                    text=body,
                    heading_path=tuple(item for _, item in headings),
                    anchor=_paragraph_anchor(state["start"], max(state["start"], state["end"])),
                )
            )
        buffer.clear()

    def keep(part: str) -> bool:
        """装得下就装上；装不下返回 `False`（调用方截断收尾）。"""

        if state["used"] + len(part) > MAX_MATERIAL_CHARS:
            return False
        state["used"] += len(part) + 1  # +1 是行与行之间的换行
        buffer.append(part)
        return True

    for index, child in enumerate(body_children, start=1):
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            level = _heading_level(style)
            if level is not None:
                # 标题起新块：先收上一块，再把标题压进章节链
                flush()
                headings = [item for item in headings if item[0] < level]
                headings.append((level, text))
                state["start"] = index
            elif not buffer:
                state["start"] = index
            if not keep(text):
                truncated = True
                break
            state["end"] = index
        elif child.tag == qn("w:tbl"):
            body, header = _table_lines(Table(child, document))
            if not body:
                continue
            # 表格独占一块：先收上一条正文，再把它按自己的锚点发出去
            flush()
            if state["used"] + len(body) > MAX_MATERIAL_CHARS:
                truncated = True
                break
            state["used"] += len(body) + 1
            table_number += 1
            chunks.append(
                MaterialChunk(
                    chunk_id=f"{DOCX_PARSER_ID}#c{len(chunks):02d}",
                    ordinal=len(chunks),
                    text=body,
                    heading_path=tuple(item for _, item in headings),
                    anchor=f"表格 {table_number}（表头：{header}）",
                )
            )

    # 收尾：正常走完、或者中途截断，**攒着的正文都要收成一块**（截断不等于把已读的丢掉）
    flush()

    if not chunks:
        raise UnsupportedMaterial("这份 Word 里没有可抽取的文字——先把文字版给我")
    return tuple(chunks), truncated


def _open(data: bytes):
    """坏文件（不是 zip / 半截）报"读不了"，不许把第三方异常漏到 HTTP 层。"""

    try:
        return open_docx(io.BytesIO(data))
    except Exception as error:  # noqa: BLE001 —— 第三方解析器什么都可能抛
        raise UnsupportedMaterial(
            f"这份 Word 读不了（{type(error).__name__}）——换一份、或另存成 .md / .pdf"
        ) from error


def _heading_level(style_name: str) -> int | None:
    match = _HEADING_STYLE.match(style_name.strip())
    if match is None:
        return None
    return int(match.group("level") or 1)


def _paragraph_anchor(start: int, end: int) -> str:
    return f"第 {start} 段" if start == end else f"第 {start}–{end} 段"


def _table_lines(table: Table) -> tuple[str, str]:
    """表格 →（按行渲染的正文, 表头）。空表 → `("", "")`（不产生块）。"""

    rows: list[str] = []
    header = ""
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if not any(cells):
            continue
        if not header:
            header = "、".join(item for item in cells if item)
        rows.append(CELL_JOIN.join(cells))
    return "\n".join(rows), header

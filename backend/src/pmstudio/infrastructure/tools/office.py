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
from datetime import datetime

import openpyxl
from docx import Document as open_docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from pmstudio.application.dto import MaterialChunk
from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.infrastructure.tools.limits import MAX_MATERIAL_CHARS, part_label, parts_of

#: 解析器身份（写进信息包，材料卡上看得见；换 docling 之后这一组会变）
DOCX_PARSER_ID = "python-docx"
XLSX_PARSER_ID = "openpyxl"

#: 表格按行渲染时的列分隔符（**不假装有结构**：`MaterialTable` 至今没有消费者）
CELL_JOIN = " | "

#: 标题样式：`Heading 1` / `标题 1` / `Title` / `标题`（中文 Word 用的是后面这几种）
_HEADING_STYLE = re.compile(r"^(?P<name>heading|标题|title)\s*(?P<level>\d+)?$", re.IGNORECASE)

#: 公式没算过（openpyxl 拿不到缓存值）时给用户的一句实话
UNCACHED_FORMULA_HINT = (
    "有公式没有算过的值（那几格先按空算）——在 Excel 里打开、另存一次就能带上结果"
)


def docx_chunks(data: bytes) -> tuple[tuple[MaterialChunk, ...], bool]:
    """Word → 块（按文档真实顺序；标题起新块、表格独占一块）。

    返回 `(chunks, truncated)`。抽不出文字（空文档）→ `UnsupportedMaterial`。
    """

    document = _open(data)
    body_children = list(document.element.body.iterchildren())
    chunks: list[MaterialChunk] = []
    buffer: list[str] = []
    headings: list[tuple[int, str]] = []
    state = {"start": 1, "used": 0}
    truncated = False
    table_number = 0

    def flush() -> None:
        """把攒着的正文收成块（空 buffer 什么都不做——收尾调用可以重复）。"""

        body = "\n".join(buffer).strip()
        if body:
            # 攒着的正文可能超宽（一个本节里几千字没有小标题）：切，每片 ≤ 块宽，
            # 段号光标随"每片占几行"推进，锚点仍精确
            for part, consumed in parts_of(body):
                chunks.append(
                    MaterialChunk(
                        chunk_id=f"{DOCX_PARSER_ID}#c{len(chunks):02d}",
                        ordinal=len(chunks),
                        text=part,
                        heading_path=tuple(item for _, item in headings),
                        anchor=_paragraph_anchor(
                            state["start"], state["start"] + consumed - 1
                        ),
                    )
                )
                state["start"] += consumed
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
            anchor = f"表格 {table_number}（表头：{header}）"
            # 一张大表就是一个逻辑片：切；锚点单位是"表格 N"，比片粗，用尾标分辨
            for part_number, (part, _) in enumerate(parts_of(body)):
                chunks.append(
                    MaterialChunk(
                        chunk_id=f"{DOCX_PARSER_ID}#c{len(chunks):02d}",
                        ordinal=len(chunks),
                        text=part,
                        heading_path=tuple(item for _, item in headings),
                        anchor=anchor + part_label(part_number),
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


def xlsx_chunks(data: bytes) -> tuple[tuple[MaterialChunk, ...], bool, tuple[str, ...]]:
    """Excel → 块（**一个工作表一块**），返回 `(chunks, truncated, warnings)`。

    - 行渲染成 `a | b | c`（空单元格留空；整行空的跳过）；
    - 锚点是"工作表「指标」第 1–12 行"；`heading_path` 写工作表名（提示词里能看出是哪张表）；
    - 空工作表跳过；整簿都空 → `UnsupportedMaterial`；
    - **公式**：值取 openpyxl 的缓存结果（`data_only=True`）。缓存没有（这份表生成之后
      从没被 Excel 打开过）时那格是空的——照实说一句，别让人以为这格本来就没内容。

    为什么 Excel 比 Word 多一个 `warnings` 出口：Word 抽不出东西就是抽不出（报错），
    Excel 的"格是空的"有两种含义（本来空 / 公式没算过），只有这里分得清。
    """

    values = _open_workbook(data, data_only=True)
    formulas = _open_workbook(data, data_only=False)
    chunks: list[MaterialChunk] = []
    used = 0
    truncated = False
    uncached_formulas = 0

    for sheet in values.worksheets:
        lines: list[str] = []
        first_row: int | None = None
        mirror = formulas[sheet.title]
        for offset, row in enumerate(sheet.iter_rows()):
            number = (sheet.min_row or 1) + offset
            cells = [_cell_text(cell.value) for cell in row]
            if any(
                cell.value is None and mirror[cell.coordinate].data_type == "f"
                for cell in row
            ):
                uncached_formulas += 1
            while cells and cells[-1] == "":
                cells.pop()
            if not cells:
                continue
            line = CELL_JOIN.join(cells)
            if used + len(line) > MAX_MATERIAL_CHARS:
                truncated = True
                break
            used += len(line) + 1
            if first_row is None:
                first_row = number
            lines.append(line)
        if lines:
            # 一个工作表曾是一个逻辑片（2000 行 → 1 块 / 44,682 字，直接超掉整个预算）。
            # 行本身就是换行，所以按行切；行号光标随"每片占几行"推进，每片有精确行范围。
            cursor = first_row or 1
            for part, consumed in parts_of("\n".join(lines)):
                chunks.append(
                    MaterialChunk(
                        chunk_id=f"{XLSX_PARSER_ID}#c{len(chunks):02d}",
                        ordinal=len(chunks),
                        text=part,
                        heading_path=(sheet.title,),
                        anchor=_sheet_anchor(sheet.title, cursor, cursor + consumed - 1),
                    )
                )
                cursor += consumed
        if truncated:
            break

    if not chunks:
        raise UnsupportedMaterial("这份 Excel 里没有可抽取的文字——先给我有内容的表")
    warnings = (UNCACHED_FORMULA_HINT,) if uncached_formulas else ()
    return tuple(chunks), truncated, warnings


def _open_workbook(data: bytes, *, data_only: bool):
    """坏文件（不是 zip / 半截）报"读不了"，不许把第三方异常漏到 HTTP 层。"""

    try:
        return openpyxl.load_workbook(io.BytesIO(data), data_only=data_only)
    except Exception as error:  # noqa: BLE001 —— 第三方解析器什么都可能抛
        raise UnsupportedMaterial(
            f"这份 Excel 读不了（{type(error).__name__}）——换一份、或另存成 .md / .pdf"
        ) from error


def _cell_text(value: object) -> str:
    """单元格 → 文本。数字去掉没意义的 `.0`（Excel 里 `3` 读出来是 `3.0`）。"""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value).strip()


def _sheet_anchor(title: str, first_row: int, last_row: int) -> str:
    rows = f"第 {first_row} 行" if first_row == last_row else f"第 {first_row}–{last_row} 行"
    return f"工作表「{title}」{rows}"


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

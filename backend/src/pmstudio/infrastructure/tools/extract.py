"""材料解析：文本 / PDF / HTML / Word（+ Excel）几条**真路径**，产出信息包（`ParsedMaterial`）。

它**不盖章**（没有 material_id / project_id）——那是存储层的事（`MaterialStore.put`）。
换 docling 的时候，只需要再写一个实现了同样端口的类，组装点换一行。

三条规矩：
1. **抽不出文字就说抽不出**（抛 `UnsupportedMaterial`），不返回空包；
2. **切块**：按行/按段累积到 `CHUNK_CHARS`，标题行起新块，每块带人话锚点；
3. **超长材料截断并写明**（`truncated` + `warnings`），不假装完整。

Word / Excel 那种"按文档结构切"的活在同目录的 `office.py` 里——那边只产块，
分派与"抽不出就说抽不出"这条纪律留在这儿（**这一处**是唯一的入口）。
"""

import re
from importlib import metadata
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.infrastructure.tools.limits import CHUNK_CHARS, MAX_MATERIAL_CHARS
from pmstudio.infrastructure.tools.office import DOCX_PARSER_ID, docx_chunks

TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml")
HTML_SUFFIXES = (".html", ".htm")
DOCX_SUFFIXES = (".docx",)

TEXT_PARSER_ID = "local-text"
HTML_PARSER_ID = "bs4-html"
PDF_PARSER_ID = "pdfminer"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

#: 标题行：Markdown `#`、`一、`、`1.2 `、`第 3 章` 都算（认错了最多多切一块，无害）
_HEADING = re.compile(
    r"^(#{1,6}\s+\S.*"
    r"|\d+(?:\.\d+)*[.、]\s*\S.*"
    r"|[一二三四五六七八九十]+、\s*\S.*"
    r"|第[一二三四五六七八九十百\d]+[章节][、.．]?\s*\S.*)$"
)


def _package_version(distribution: str) -> str:
    try:
        return f"{distribution} {metadata.version(distribution)}"
    except metadata.PackageNotFoundError:  # pragma: no cover - 装上了就一定拿得到
        return f"{distribution} unknown"


def _is_heading(line: str) -> bool:
    return bool(_HEADING.match(line.strip()))


def _line_anchor(start: int, end: int) -> str:
    return f"第 {start} 行" if start == end else f"第 {start}–{end} 行"


def _heading_level(line: str) -> int:
    stripped = line.strip()
    if stripped.startswith("#"):
        return len(stripped) - len(stripped.lstrip("#"))
    return 1


def chunk_text(text: str, *, parser_id: str) -> tuple[MaterialChunk, ...]:
    """按行切块：标题行起新块，累积到 `CHUNK_CHARS` 也起新块。纯函数。"""

    lines = text.splitlines()
    chunks: list[MaterialChunk] = []
    buffer: list[str] = []
    start_line = 1
    headings: list[tuple[int, str]] = []

    def width() -> int:
        return sum(len(item) for item in buffer)

    def flush(end_line: int) -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            ordinal = len(chunks)
            chunks.append(
                MaterialChunk(
                    chunk_id=f"{parser_id}#c{ordinal:02d}",
                    ordinal=ordinal,
                    text=body,
                    heading_path=tuple(item for _, item in headings),
                    anchor=_line_anchor(start_line, max(start_line, end_line)),
                )
            )
        buffer = []

    for number, line in enumerate(lines, start=1):
        heading = _is_heading(line)
        if buffer and (heading or width() >= CHUNK_CHARS):
            flush(number - 1)
        if not buffer:
            start_line = number
        if heading:
            level = _heading_level(line)
            headings = [item for item in headings if item[0] < level]
            headings.append((level, line.strip()))
        buffer.append(line)
    flush(len(lines))
    return tuple(chunks)


def _paragraphs(page: str) -> list[str]:
    return [part for part in re.split(r"\n\s*\n", page) if part.strip()]


def chunk_pages(pages: list[str] | tuple[str, ...], *, parser_id: str) -> tuple[MaterialChunk, ...]:
    """按页切块（PDF 走这条）：锚点写页号；一页里段落多就顺带标"第几段"。"""

    chunks: list[MaterialChunk] = []
    for page_number, page in enumerate(pages, start=1):
        paragraphs = _paragraphs(page)
        for part_number, paragraph in enumerate(paragraphs, start=1):
            body = paragraph.strip()
            if not body:
                continue
            ordinal = len(chunks)
            anchor = (
                f"第 {page_number} 页"
                if part_number == 1
                else f"第 {page_number} 页（第 {part_number} 段）"
            )
            chunks.append(
                MaterialChunk(
                    chunk_id=f"{parser_id}#c{ordinal:02d}",
                    ordinal=ordinal,
                    text=body,
                    heading_path=(),
                    anchor=anchor,
                )
            )
    return tuple(chunks)


class LocalMaterialParser:
    """本地真解析：文本按 UTF-8、PDF 走 pdfminer、HTML 走 bs4+lxml、Word 走 python-docx；
    其余明确拒绝（**不静默存空**）。"""

    def identities(self) -> frozenset[tuple[str, str]]:
        """我能产出四种身份（文本 / PDF / HTML / Word）——换 docling 之后这一组就全变了。"""

        return frozenset(
            {
                (TEXT_PARSER_ID, _package_version("pmstudio")),
                (HTML_PARSER_ID, f"beautifulsoup4 {metadata.version('beautifulsoup4')}"),
                (PDF_PARSER_ID, _package_version("pdfminer.six")),
                (DOCX_PARSER_ID, _package_version("python-docx")),
            }
        )

    def parse(self, *, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf" or media_type == "application/pdf":
            return self._from_pdf(filename, media_type, data)
        if suffix in HTML_SUFFIXES or media_type == "text/html":
            return self._from_html(filename, media_type, data)
        if suffix in DOCX_SUFFIXES or media_type == DOCX_MEDIA_TYPE:
            return self._from_docx(filename, media_type, data)
        if suffix in TEXT_SUFFIXES or media_type.startswith("text/"):
            return self._from_text(filename, media_type, data)
        raise UnsupportedMaterial(
            f"不支持的材料类型：{filename}（{media_type}）"
            "——先把内容另存成 .md / .txt / .pdf / .docx（老 .doc 也请另存为 .docx）",
            filename=filename,
            media_type=media_type,
        )

    def _from_text(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        text, truncated = _cap_text(data.decode("utf-8", errors="replace").strip())
        return self._pack(
            filename=filename,
            media_type=media_type,
            data=data,
            text=text,
            truncated=truncated,
            parser_id=TEXT_PARSER_ID,
            parser_version=_package_version("pmstudio"),
            chunks=chunk_text(text, parser_id=TEXT_PARSER_ID),
        )

    def _from_html(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        try:
            soup = BeautifulSoup(data, "lxml")
        except Exception as error:  # noqa: BLE001 —— 同上：lxml 对畸形输入也可能抛
            raise UnsupportedMaterial(
                f"这份网页读不了（{type(error).__name__}）——换一份、或另存成文本再给我",
                filename=filename,
            ) from error
        for tag in soup(["script", "style"]):
            tag.decompose()
        text, truncated = _cap_text(soup.get_text("\n", strip=True).strip())
        return self._pack(
            filename=filename,
            media_type=media_type or "text/html",
            data=data,
            text=text,
            truncated=truncated,
            parser_id=HTML_PARSER_ID,
            parser_version=f"beautifulsoup4 {metadata.version('beautifulsoup4')}",
            chunks=chunk_text(text, parser_id=HTML_PARSER_ID),
        )

    def _from_docx(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        """Word：python-docx 按**文档真实顺序**切块（段落与表格交错，表格独占一块）。

        `office` 那层不知道文件名，所以在这里把文件名补进错误详情——界面要说清是哪一份。
        """

        try:
            chunks, truncated = docx_chunks(data)
        except UnsupportedMaterial as error:
            raise UnsupportedMaterial(
                error.message, **{**error.details, "filename": filename}
            ) from error
        return self._pack(
            filename=filename,
            media_type=media_type or DOCX_MEDIA_TYPE,
            data=data,
            text="\n".join(chunk.text for chunk in chunks),
            truncated=truncated,
            parser_id=DOCX_PARSER_ID,
            parser_version=_package_version("python-docx"),
            chunks=chunks,
        )

    def _from_pdf(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        """PDF：pdfminer 抽文字，按页切块（锚点写第几页）。"""

        try:
            raw = pdf_extract_text(BytesIO(data))
        except Exception as error:  # noqa: BLE001 —— 第三方解析器什么都可能抛
            # 坏文件（半截 / 加密 / 根本不是 PDF）要报"读不了"，**不许漏成 500**：
            # 真跑踩到过——半截 PDF 会把 pdfminer 的 PSEOF 直接顶到 HTTP 层。
            raise UnsupportedMaterial(
                f"这份 PDF 读不了（{type(error).__name__}）——换一份、或另存成文本再给我",
                filename=filename,
            ) from error
        # pdfminer 用 \f 分页；末尾的空页丢掉
        pages = [page for page in raw.split("\f")]
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        pages, truncated = _cap_pages(pages)
        text = "\n".join(page.strip() for page in pages if page.strip()).strip()
        return self._pack(
            filename=filename,
            media_type=media_type or "application/pdf",
            data=data,
            text=text,
            truncated=truncated,
            parser_id=PDF_PARSER_ID,
            parser_version=_package_version("pdfminer.six"),
            chunks=chunk_pages(pages, parser_id=PDF_PARSER_ID),
        )

    def _pack(
        self,
        *,
        filename: str,
        media_type: str,
        data: bytes,
        text: str,
        truncated: bool,
        parser_id: str,
        parser_version: str,
        chunks: tuple[MaterialChunk, ...],
    ) -> ParsedMaterial:
        stripped = text.strip()
        if not stripped or not chunks:
            raise UnsupportedMaterial(
                f"这份材料没有可抽取的文字：{filename}（扫描件？先把文字版给我）",
                filename=filename,
                media_type=media_type,
            )
        warnings: list[str] = []
        if truncated:
            warnings.append(f"材料太长，只保留前 {MAX_MATERIAL_CHARS} 字（已截断）")
        return ParsedMaterial(
            origin="upload",
            title=filename,
            source_ref=f"attachment:{filename}",
            media_type=media_type,
            bytes_len=len(data),
            chunks=chunks,
            parser_id=parser_id,
            parser_version=parser_version,
            truncated=truncated,
            warnings=tuple(warnings),
        )


def _cap_text(text: str) -> tuple[str, bool]:
    """文字上限：**先截再切块**，所以信息包里永远不会超。"""

    if len(text) <= MAX_MATERIAL_CHARS:
        return text, False
    return text[:MAX_MATERIAL_CHARS], True


def _cap_pages(pages: list[str]) -> tuple[list[str], bool]:
    """PDF 按页收，凑到上限为止（整页保留，页内的字照旧切块）。"""

    kept: list[str] = []
    used = 0
    for page in pages:
        if used + len(page) > MAX_MATERIAL_CHARS and kept:
            return kept, True
        kept.append(page)
        used += len(page)
    return kept, False

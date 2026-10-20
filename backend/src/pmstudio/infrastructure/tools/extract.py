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

import codecs
import re
from importlib import metadata
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.infrastructure.tools.limits import (
    CHUNK_CHARS,
    MAX_MATERIAL_CHARS,
    cap_text,
    part_label,
    parts_of,
)
from pmstudio.infrastructure.tools.office import (
    DOCX_PARSER_ID,
    XLSX_PARSER_ID,
    docx_chunks,
    xlsx_chunks,
)

TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml")
HTML_SUFFIXES = (".html", ".htm")
DOCX_SUFFIXES = (".docx",)
XLSX_SUFFIXES = (".xlsx",)

#: 材料太长被截断时写进信息包的那句话（几条解析路共用同一句，界面与提示词都照它说）
TRUNCATED_WARNING = "材料太长，只保留前 {limit} 字（已截断）"

TEXT_PARSER_ID = "local-text"
HTML_PARSER_ID = "bs4-html"
PDF_PARSER_ID = "pdfminer"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 标题行：Markdown `#`、`一、`、`1.2 `、`第 3 章` 都算（认错了最多多切一块，无害）
_HEADING = re.compile(
    r"^(#{1,6}\s+\S.*"
    r"|\d+(?:\.\d+)*[.、]\s*\S.*"
    r"|[一二三四五六七八九十]+、\s*\S.*"
    r"|第[一二三四五六七八九十百\d]+[章节][、.．]?\s*\S.*)$"
)

#: HTML 正文抽取的算法档位。**换算法就 +1**：`html_parser_version` 会跟着变，
#: 既有 HTML 与抓取材料因此被判"该重解析"——否则新旧两种质量在界面上分不出来。
HTML_EXTRACT_REVISION = 1

#: 站点噪音标签：语义上确定不是正文的那些。`<header>` **故意不在内**（见 `html_text`）。
#: 也不按 `class`/`id` 关键词剥（"宁留不删"）——实测博客页残留的侧栏只有约 20 字，
#: 在 4000 字预算里占比可忽略，而类名黑名单会误删正文且要随站点形态不断补。
#:
#: **试过并撤掉的一整级**：`readability-lxml`（Firefox Reader View 的那套统计抽取）。
#: 三个真实页面实测增益 1–6%（docs.python.org 19710→18478，httpbin 3596→3568），
#: 而它会**静默整块丢掉**它认为不是正文的节点——短页面上丢的是唯一内容，
#: 且要靠三四条守门判据去猜它丢得对不对（见 `04 §I` 的取舍记录）。
#: 语义标签剥离是确定性的，噪声的大头（nav / aside / footer）已经被它拿走；
#: 剩下的那 1–6% 由本轮新增的选块粗排补上（命中本轮输入的块自然排在前面）。
#: 结论：为 1–6% 的收益背一个会丢内容的依赖，不划算。
CHROME_TAGS = (
    "script",
    "style",
    "nav",
    "aside",
    "footer",
    "form",
    "noscript",
    "iframe",
    "svg",
    "button",
)


def _package_version(distribution: str) -> str:
    try:
        return f"{distribution} {metadata.version(distribution)}"
    except metadata.PackageNotFoundError:  # pragma: no cover - 装上了就一定拿得到
        return f"{distribution} unknown"


#: 一句实话：按 GB18030 兜底读的时候要说清，免得用户以为乱码是原文
GB18030_NOTE = "这份材料不是 UTF-8，按 GB18030 读的（有乱码就另存为 UTF-8）"


def decode_text(data: bytes) -> tuple[str, str | None]:
    """字节 → 文字，并告诉调用方"是不是按 GB18030 兜的底"（兜了才给一句话）。

    **没有 `errors="replace"` 这一档**：那会把一份 GBK 的中文材料读成乱码且一声不吭
    （实测 `"权限系统：谁能看报表？"` → `'ȫȫϵͳ'`，而 `warnings` 是空的）。读错比读不了
    更坏——读不了会 422 指路，读错会静默污染提示词。这是本模块第一条纪律
    "抽不出文字就说抽不出"在编码上的样子。

    为什么只认 UTF-8 与 GB18030：前者是现在的事实标准，后者是国标全集（GBK / GB2312
    都是它的子集）、stdlib 里现成，**且严格解码不可能猜错**。不引 `charset_normalizer`
    一类的探测器：它可能猜错，而猜错的表现和今天一模一样——静默的乱码；为一个不存在
    的收益多背一个新依赖，不划算。
    """

    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), None
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gb18030"), GB18030_NOTE
    except UnicodeDecodeError as error:
        raise UnsupportedMaterial(
            "这份材料读不了：编码既不是 UTF-8 也不是 GB18030——另存成 UTF-8 再给我"
        ) from error


def html_parser_version() -> str:
    """`bs4-html` 这一档的解析器身份。

    上传 `.html` 与**抓网页**必须写同一个——否则抓来的网页会被 `needs_reparse` 判成
    "旧解析器读的"（同一份实现、两个入口，身份只能有一份）。

    **算法档位（`HTML_EXTRACT_REVISION`）是故意写进去的**：换了正文抽取算法，这条串就变，
    于是既有 HTML/抓取材料全部被判"该重解析"（原始字节都还在，点一次即可）。
    不写档位的后果是新旧材料两种质量且界面上看不出区别——那正是 `needs_reparse`
    这一机制要防的事。
    """

    return f"bs4-{HTML_EXTRACT_REVISION}+beautifulsoup4 {metadata.version('beautifulsoup4')}"


def html_text(data: bytes) -> tuple[str, str | None]:
    """HTML →（正文, `<title>` 或 None）。上传 `.html` 与抓网页**共用这一条**。

    剥掉语义确定的站点噪音标签（`CHROME_TAGS`）。`<header>` 刻意不在内——它既可能是站点
    导航也可能是正文的标题块，剥它可能丢内容；**宁留不删**：留着的噪音看得见，删错的
    内容看不见。同样不按 `class`/`id` 关键词剥（`CHROME_TAGS` 注释里有实测依据）。

    正文一律原样保留：这里**不做主内容抽取**。试过 `readability-lxml`，三个真实页面
    增益只有 1–6%，而它会静默丢内容（见 `CHROME_TAGS` 注释）。噪声的大头已经被语义标签
    拿走，剩下那点由选块粗排（`select_chunks(query=...)`）解决——**同一个动作不请两把刀**。

    `<title>` 总在正文第一行：模型该知道自己读的是哪一页，`test_fetch.py` 钉着这条。
    抽不出正文时返回 `("", title)`——**不在这里抛**：报错要带上是哪个文件/哪个网址，
    那只有调用方知道（上传有文件名、抓取有最终地址，两条路的既有报错都不一样）。

    畸形输入抛 `UnsupportedMaterial`（调用方补文件名或网址）。
    """

    try:
        soup = BeautifulSoup(data, "lxml")
    except Exception as error:  # noqa: BLE001 —— lxml 对畸形输入也可能抛
        raise UnsupportedMaterial(
            f"这份网页读不了（{type(error).__name__}）——换一份、或另存成文本再给我"
        ) from error
    for tag in soup(CHROME_TAGS):
        tag.decompose()
    # `<title>` 先从树里抽走：否则"整页只有标题和导航"的页面会被当成有正文
    # （标题在 `<head>` 里，`get_text` 会把它一起念出来）
    node = soup.title
    title = node.get_text(strip=True) if node is not None else ""
    if node is not None:
        node.extract()
    text = _flatten_with_headings(soup)
    if not text:
        return "", (title or None)
    return (f"{title}\n{text}" if title and title not in text else text), (title or None)


#: 网页标题层级 → markdown 前缀的级数（`h1` → `#`）。**和上传 .md 同构**：
#: `chunk_text` 的 `_HEADING` 正则认的就是这些形状。
_HEADING_LEVELS = {f"h{level}": level for level in range(1, 7)}


def _flatten_with_headings(soup) -> str:  # type: ignore[no-untyped-def]
    """把页面拍成文本，**标题行带 markdown 前缀**（`h2` → `## 标题`）。

    ## 为什么（2026-10-19 实测定的）

    原来直接 `soup.get_text("\\n", strip=True)`，`<h1>`–`<h6>` 被拍成**普通行**，
    于是网页材料的 `heading_path` **基本恒空**——章节名只以普通文本存在于**那一个块**里，
    长章节切开后**后几块搜不到节名**（`index_source` 的 docstring 点名的问题：
    "长节被切成多块时，后几块的正文里没有节名"）。

    加 markdown 前缀后，`chunk_text` 的 `_HEADING` 正则自然认出来 →
    `heading_path` 有值，**零新依赖**，且与「上传 .md」**同构**
    （`.md` 的 `# 标题` 就是这么进 `heading_path` 的）。

    ## 怎么加（只动标题，其余一字不改）

    **把标题元素替换成带前缀的纯文本**，再走原来那条 `get_text` 拍平——
    所以裸 `div` 菜单之类"没有语义标签的站点噪音"**照旧留在正文里**
    （`test_a_menu_div_without_semantic_tags_stays` 钉着这条取舍：
    「宁留不删」——留着的噪音看得见，删错的内容看不见）。

    标题的**正文也保留**（带前缀）——它仍是可搜的内容。
    """

    for element in soup.find_all(list(_HEADING_LEVELS)):
        text = element.get_text(" ", strip=True)
        if text:
            level = _HEADING_LEVELS[element.name]
            element.replace_with(f"{'#' * level} {text}")
    return soup.get_text("\n", strip=True).strip()


def _is_heading(line: str) -> bool:
    return bool(_HEADING.match(line.strip()))


def _line_anchor(start: int, end: int) -> str:
    return f"第 {start} 行" if start == end else f"第 {start}–{end} 行"


def _heading_level(line: str) -> int:
    stripped = line.strip()
    if stripped.startswith("#"):
        return len(stripped) - len(stripped.lstrip("#"))
    return 1


def chunk_text(
    text: str, *, parser_id: str, max_chars: int = CHUNK_CHARS
) -> tuple[MaterialChunk, ...]:
    """按行切块：标题行起新块，累积到 `max_chars` 也起新块。纯函数。

    `max_chars` 默认 `CHUNK_CHARS`（上传材料那条路）；联网结果那条路传
    `WEB_CHUNK_CHARS`（400）——理由见 `limits.py` 里那个常量的注释。
    """

    lines = text.splitlines()
    chunks: list[MaterialChunk] = []
    buffer: list[str] = []
    start_line = 1
    headings: list[tuple[int, str]] = []

    def width() -> int:
        return sum(len(item) for item in buffer)

    def flush() -> None:
        nonlocal buffer, start_line
        body = "\n".join(buffer).strip()
        if body:
            # 一个逻辑片可能超宽（一整个长行、几千字没有小标题的正文）：切，且每片 ≤ 块宽。
            # 行号光标随"每片占几行"推进，所以切完每片仍有自己精确的锚点。
            for part, consumed in parts_of(body, max_chars=max_chars):
                chunks.append(
                    MaterialChunk(
                        chunk_id=f"{parser_id}#c{len(chunks):02d}",
                        ordinal=len(chunks),
                        text=part,
                        heading_path=tuple(item for _, item in headings),
                        anchor=_line_anchor(start_line, start_line + consumed - 1),
                    )
                )
                start_line += consumed
        buffer = []

    for number, line in enumerate(lines, start=1):
        heading = _is_heading(line)
        if buffer and (heading or width() >= max_chars):
            flush()
        if not buffer:
            start_line = number
        if heading:
            level = _heading_level(line)
            headings = [item for item in headings if item[0] < level]
            headings.append((level, line.strip()))
        buffer.append(line)
    flush()
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
            anchor = (
                f"第 {page_number} 页"
                if part_number == 1
                else f"第 {page_number} 页（第 {part_number} 段）"
            )
            # 一段可能长得离谱（法务条款、说明书）：同样切。锚点单位是"段"，比片粗，
            # 所以多片用尾标分辨。
            for index, (part, _) in enumerate(parts_of(body)):
                chunks.append(
                    MaterialChunk(
                        chunk_id=f"{parser_id}#c{len(chunks):02d}",
                        ordinal=len(chunks),
                        text=part,
                        heading_path=(),
                        anchor=anchor + part_label(index),
                    )
                )
    return tuple(chunks)


class LocalMaterialParser:
    """本地真解析：文本按 UTF-8、PDF 走 pdfminer、HTML 走 bs4+lxml、Word 走 python-docx；
    其余明确拒绝（**不静默存空**）。"""

    def identities(self) -> frozenset[tuple[str, str]]:
        """我能产出五种身份（文本 / PDF / HTML / Word / Excel）——换 docling 之后这一组就全变了。"""

        return frozenset(
            {
                (TEXT_PARSER_ID, _package_version("pmstudio")),
                (HTML_PARSER_ID, html_parser_version()),
                (PDF_PARSER_ID, _package_version("pdfminer.six")),
                (DOCX_PARSER_ID, _package_version("python-docx")),
                (XLSX_PARSER_ID, _package_version("openpyxl")),
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
        if suffix in XLSX_SUFFIXES or media_type == XLSX_MEDIA_TYPE:
            return self._from_xlsx(filename, media_type, data)
        if suffix in TEXT_SUFFIXES or media_type.startswith("text/"):
            return self._from_text(filename, media_type, data)
        raise UnsupportedMaterial(
            f"不支持的材料类型：{filename}（{media_type}）"
            "——先把内容另存成 .md / .txt / .pdf / .docx / .xlsx（老 .doc / .xls 也请另存）",
            filename=filename,
            media_type=media_type,
        )

    def _from_text(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        # 编码探测在解析最前面：GBK 的 txt/csv 是国内最常见的遗留材料，读不懂要说读不懂
        text, encoding_note = decode_text(data)
        text, truncated = cap_text(text.strip())
        return self._pack(
            filename=filename,
            media_type=media_type,
            data=data,
            text=text,
            truncated=truncated,
            parser_id=TEXT_PARSER_ID,
            parser_version=_package_version("pmstudio"),
            chunks=chunk_text(text, parser_id=TEXT_PARSER_ID),
            extra_warnings=(encoding_note,) if encoding_note else (),
        )

    def _from_html(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        try:
            text, _ = html_text(data)
        except UnsupportedMaterial as error:
            raise UnsupportedMaterial(error.message, filename=filename) from error
        if not text.strip():
            # 抽不出正文：**在这里**报，因为只有这里知道是哪份文件
            raise UnsupportedMaterial(
                f"这份网页没有可抽取的文字：{filename}"
                "（可能是脚本渲染的页面，也可能整页都是导航和页脚——先把正文复制下来）",
                filename=filename,
            )
        text, truncated = cap_text(text)
        return self._pack(
            filename=filename,
            media_type=media_type or "text/html",
            data=data,
            text=text,
            truncated=truncated,
            parser_id=HTML_PARSER_ID,
            parser_version=html_parser_version(),
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

    def _from_xlsx(self, filename: str, media_type: str, data: bytes) -> ParsedMaterial:
        """Excel：openpyxl 一个工作表一块（值取缓存结果；公式没算过的那几格照实说）。"""

        try:
            chunks, truncated, warnings = xlsx_chunks(data)
        except UnsupportedMaterial as error:
            raise UnsupportedMaterial(
                error.message, **{**error.details, "filename": filename}
            ) from error
        return self._pack(
            filename=filename,
            media_type=media_type or XLSX_MEDIA_TYPE,
            data=data,
            text="\n".join(chunk.text for chunk in chunks),
            truncated=truncated,
            parser_id=XLSX_PARSER_ID,
            parser_version=_package_version("openpyxl"),
            chunks=chunks,
            extra_warnings=warnings,
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
        extra_warnings: tuple[str, ...] = (),
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
            warnings.append(TRUNCATED_WARNING.format(limit=MAX_MATERIAL_CHARS))
        warnings.extend(extra_warnings)
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

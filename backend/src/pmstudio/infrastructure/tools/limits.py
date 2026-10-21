"""解析阶段的上限（**唯一一份**）：块宽与单份材料的抽字上限。

为什么单独一个文件：`extract.py`（文本 / PDF / HTML）与 `office.py`（Word / Excel）都要用，
而且两边**必须是同一个数**——各写一份就是第二份真相源。

`cap_blocks` 也住在这儿，理由同上，而且更硬：**常量与保证它的那个函数必须在同一个地方**。
`extract` 与 `office` 相互 import（一个为了分派、一个为了 `MAX_MATERIAL_CHARS`）只靠加载顺序
侥幸成立；把执行者放到两个消费方都要 import 的这份文件里，这个环顺势就断了。
"""

#: 一个块的目标宽度（字）。中文按字算。
#:
#: **2026-10-21 从 500 降到 400**（批 7）：喂给嵌入模型的是
#: `标题 + 章节链 + 正文`（`sqlite/tokenize.index_text`），而
#: `bge-small-zh-v1.5` 的上限是 **512 token**——那是**模型契约**（BAAI 官方：
#: 超过 512 直接截），而且**截断是静默的**。
#: 实测：块宽 500 时块尾部对向量**完全不可见**（尾部加独特标记词，向量余弦 1.000000）。
#:
#: 联网那条路早就是 400（当时单列一个 `WEB_CHUNK_CHARS`），**上传/抓网页漏了**——
#: 现在两条路共用这一个数字，`WEB_CHUNK_CHARS` 删除（两个同值常量 = 第二个真相源）。
#: 400 + 标题/章节链（~60）≈ 460 token，留得下；**再兜一层** `embedding.py` 的
#: token 闸门（超长标题/深章节链仍可能顶出去）。
CHUNK_CHARS = 200

#: 单份材料抽出的文字上限：超了截断（提示词预算是另一层，这道防线在解析这一步）
MAX_MATERIAL_CHARS = 200_000

#: **联网材料**每份最多写多少块的向量。
#:
#: 为什么要有预算：整页入库让单份联网材料从 4 块涨到 21–167 块，
#: 全量向量化一要 15 秒/轮（49ms/块 × 315 块）——现状只要 2.2 秒。
#:
#: **取值 24 的依据**（实测 2026-10-20，冻结语料 15 份材料 / 45 条词面查询 +
#: 14 条手写改述，扫描 N = 0/8/12/16/24/40/全量）：
#:
#: | N | 词面命中 | 名次均 | 改述命中 | 每轮耗时 |
#: |---|---|---|---|---|
#: | 0（无向量） | 38 | 2.16 | 9 | 0s |
#: | 8 | 35 | 2.26 | 9 | 2.0s |
#: | 12 | 37 | 1.78 | 6 | 2.9s |
#: | **24** | **37** | **1.84** | **11** | **5.9s** |
#: | 40 | 38 | 1.68 | 11 | 9.8s |
#: | 全量 | 39 | 1.82 | 13 | 15.4s |
#:
#: 联网材料（`web_search`）**每份最多索引多少字的正文**。`None` 那种不限在
#: `vector_index.vector_char_budget` 里处理。
#:
#: ## 为什么量纲是「字」而不是「块」（2026-10-21，批 11）
#:
#: 「块数」是**实现细节**：块宽一改，「前 N 块」覆盖的**字数**就跟着腰斩。
#: 实测（C 口径语料 15 份真实网页整页，162238 字）：
#:
#: | 块宽 | 预算「前 24 块」→ 覆盖 |
#: |---|---|
#: | 400 | 41% |
#: | 200 | **27%** ← 批 10 把块宽降到 200 之后 |
#:
#: 而网页的**尾部不是低密度内容**（实测 55–85% 区间全是实质段落）→ 覆盖不足 = 检索漏。
#: **预算的量纲必须与块宽解耦**，否则每改一次块宽就要重调一次预算。
#:
#: ## 这个数怎么来的
#:
#: 9600 = 旧口径（块宽 400 × 24 块）的**正确翻译**：量纲换了，量级不变。
#: 覆盖与召回的实测曲线见 `scripts/vector_coverage_bench.py`（批 11）。
#:
#: **只限联网材料**：上传（`upload`）与抓网页（`web_fetch`）是用户精挑要精读的材料，
#: 一刀切会改掉批 5/6a 已交付的语义召回能力——那是越界。
WEB_VECTOR_CHARS = 9600

#: 句末标点：单行超宽时，宁可在这儿下刀也不从句子中间砍。
#: **公开**（2026-10-21 批 10）：现在有两个消费方——`limits._cut_one_line`（补救切分）
#: 与 `extract.chunk_text`（块内按句累积，主路径）。
SENTENCE_ENDS = "。！？；!?;"


def part_label(index: int) -> str:
    """同一个逻辑片被切成多片时，第 `index` 片的尾标。

    只在**单位比片粗**的两处用：PDF 的一段、Word 的一张表格。它们的锚点单位本来就是
    "第 3 页（第 2 段）" / "表格 2（表头：…）"——一个单位里切出几片，除了尾标没法分辨。
    行/工作表那两条路不用它：那里的行号光标能逐片推进，锚点本来就精确，加了反而是噪声。
    """

    return "" if index == 0 else f"（第 {index + 1} 部分）"


def cap_text(text: str) -> tuple[str, bool]:
    """把文字截到 `MAX_MATERIAL_CHARS`。返回（截后文字, 截没截）。

    **先截再切块**，所以信息包里永远不会超。

    为什么住这儿（而不是 `extract.py`）：上限常量与保证它的函数必须在同一个地方
    ——本文档开头那条原则。`extract.py`（上传/抓网页）与 `tavily.py`（联网）
    两条路径都要用；而联网路径此前**完全没有上限**（整页入库后个别站点能到几十万字）。
    """

    if len(text) <= MAX_MATERIAL_CHARS:
        return text, False
    return text[:MAX_MATERIAL_CHARS], True


def parts_of(text: str, *, max_chars: int = CHUNK_CHARS) -> tuple[tuple[str, int], ...]:
    """把一个可能超宽的逻辑片切成 ≤ `CHUNK_CHARS` 的若干片，并报**每片占了几行**。

    为什么报行数：锚点是 `MaterialChunk` 的既有契约（"第 12–40 行"——引用与界面跳转都靠它）。
    一个逻辑片超宽时要切成多片，若让它们共用原锚点，用户会看到 93 个块全写着
    "工作表「明细」第 1–2001 行"——**锚点就废了**。调用方拿 `占几行` 自己推进行号光标，
    于是每片的锚点精确对应它真正覆盖那几行。

    行数是"这一片含几个换行 + 1"，所以**切在换行处时完全精确**；只有"一整行自己就超宽、
    被硬切成几片"这一种情况下，后几片会与前一片记同一行号——那种行本来就是退化的
    （一个单元格写了 4000 字），可接受。

    短输入原样返回一片，正常路径上一个原子都不多切——这条函数只在超宽时才动手。

    下刀的优先序（三级，每一级都为"切完还读得通"）：
    ① **换行**——一行是一个天然语义单位，切在换行处不伤句子；
    ② **句末标点**——单行本身超宽时（长表格单元格、压缩 JSON 挤成一行）退而求其次；
    ③ **硬切**——前两级都无处可切（一整行没有标点）才砍。

    为什么必须有③：`CHUNK_CHARS` 是**不变式**，不是"尽量"。没有③，一个 4 万字的单行
    又会变回一整块——Excel 就这么炸过：2000 行切成 **1 块 / 44,682 字**，
    然后 `select_chunks` 第一个块就超 4000 字预算，选中 0 块，
    用户勾了材料，提示词里写"（这一轮没有外部材料）"。
    """

    if len(text) <= max_chars:
        return ((text, text.count("\n") + 1),)

    parts = _pack(_cut(text, max_chars), max_chars)
    return tuple((part, part.count("\n") + 1) for part in parts)


def _cut(text: str, max_chars: int) -> list[str]:
    """按"行优先、单行内句末优先、最后硬切"把文本拆成原子（每个原子 ≤ `CHUNK_CHARS`）。"""

    atoms: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_chars:
            atoms.append(line)
        else:
            atoms.extend(_cut_one_line(line, max_chars))
    return atoms


def _pack(atoms: list[str], max_chars: int) -> list[str]:
    """把原子贪心装进 ≤ `CHUNK_CHARS` 的块，行间用换行接回。"""

    blocks: list[str] = []
    current: list[str] = []
    width = 0
    for atom in atoms:
        cost = len(atom) + (1 if current else 0)  # 1 是行间那个换行
        if current and width + cost > max_chars:
            blocks.append("\n".join(current))
            current, width = [], 0
            cost = len(atom)
        current.append(atom)
        width += cost
    if current:
        blocks.append("\n".join(current))
    return blocks


def _cut_one_line(line: str, max_chars: int) -> tuple[str, ...]:
    """单行超宽：先在窗口内找**最后一个**句末标点下刀；隔得太远就直接按窗口宽度砍。"""

    parts: list[str] = []
    rest = line
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind(mark) for mark in SENTENCE_ENDS) + 1
        if cut <= max_chars // 2:  # 最近的句末在窗口前半段，等它就砍得太碎了
            cut = max_chars
        parts.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        parts.append(rest)
    return tuple(parts)

"""解析阶段的上限（**唯一一份**）：块宽与单份材料的抽字上限。

为什么单独一个文件：`extract.py`（文本 / PDF / HTML）与 `office.py`（Word / Excel）都要用，
而且两边**必须是同一个数**——各写一份就是第二份真相源。

`cap_blocks` 也住在这儿，理由同上，而且更硬：**常量与保证它的那个函数必须在同一个地方**。
`extract` 与 `office` 相互 import（一个为了分派、一个为了 `MAX_MATERIAL_CHARS`）只靠加载顺序
侥幸成立；把执行者放到两个消费方都要 import 的这份文件里，这个环顺势就断了。
"""

#: 一个块的目标宽度（字）。中文按字算，500 字约一屏。
CHUNK_CHARS = 500

#: **联网结果**的块宽（字）。为什么比 `CHUNK_CHARS` 小（2026-10-19 实测定的）：
#: 向量模型 `bge-small-zh-v1.5` 的上限是 **512 token**，中文约 1 字 1 token；
#: 而喂进去的是「标题 + 章节链 + 正文」，标题与章节链也要占 token。
#: 实测超限的**尾部对向量完全不可见**（embed(700字) 与 embed(前510字) 余弦
#: 1.000000；尾部加独特标记词，向量完全不变）。
#: 400 + 标题/路径（~60）≈ 460 token，留得下。
WEB_CHUNK_CHARS = 400

#: 单份材料抽出的文字上限：超了截断（提示词预算是另一层，这道防线在解析这一步）
MAX_MATERIAL_CHARS = 200_000

#: 句末标点：单行超宽时，宁可在这儿下刀也不从句子中间砍
_SENTENCE_ENDS = "。！？；!?;"


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
        cut = max(window.rfind(mark) for mark in _SENTENCE_ENDS) + 1
        if cut <= max_chars // 2:  # 最近的句末在窗口前半段，等它就砍得太碎了
            cut = max_chars
        parts.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        parts.append(rest)
    return tuple(parts)

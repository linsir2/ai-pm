"""`05-DDD架构.md §8` 的目录图必须与磁盘一致（2026-10-04 · M7 ③）。

## 为什么要有这条

`harness/` 这一层搬了四次（M1 / M2 / M3 / M4），**`05 §8` 一次都没跟上**。审计时逐项核对，
那张图里：

- **4 个文件写了但不存在**：`infrastructure/llm/litellm_gateway.py`（M4 搬走）、
  `infrastructure/llm/parsers.py`、`api/dtos.py`（真名 `api/schemas.py`）、
  `infrastructure/graph/nodes/`（节点全在 `graph.py` 里）；
- **扩展名自相矛盾**：`seats/definitions/*.yaml`，而真实是 `.toml`；
- **漏了整层与 12+ 个真实模块**：`harness/`、`domain/memory/`、`application/agents/`、
  `infrastructure/tools/`、`application/context.py`、`assembly.py`、`errors.py`、`events.py`、
  `repair.py`、`narrowing.py`、`deliverable.py`、`postgres/model_calls.py`。

一张写着不存在文件的架构图，比没有图更坏：读的人会照着它去找，找不到就以为代码丢了。

## 双向：为什么不能只查一个方向

- **只查"文档提到的都存在"** → 挡住"图上写了幽灵文件"；
- **只查"存在的都写到了"** → 挡住"搬了新东西没进图"。

这次漂移**两个方向都有**（幽灵文件 + 漏掉整层），所以两个方向都要。

代价要说清：新增模块时必须改 `§8`，否则这里会红。**那是故意的**——
架构图的维护成本就是把架构维护一遍，那本来就该做。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # tests/architecture → 仓库根
PACKAGE = REPO / "backend" / "src" / "pmstudio"
SECTION = REPO / ".codex" / "docs" / "设计" / "05-DDD架构.md"

#: §8 里那些以 `` ``` `` 围起来的目录图块
_BLOCK = re.compile(r"```\n(.*?)```", re.DOTALL)

#: 扩展名白名单：只查这些（`.py` 是模块，`.sql`/`.toml`/`.md` 是数据文件）。
#: 不查的（`.yaml`）正是 §8 写错的那个——它压根不存在。
_SUFFIXES = (".py", ".sql", ".toml", ".md")

#: §8 提到、但**不属于源码树**的东西（文档自身）。它们不参与存在性检查。
#: **不包含 `frontend/` 与 `tests/`**：那两行要靠下面"走出源码树就重置"的判定处理——
#: 早先把它们放进这个过滤器，结果 `frontend/` 被滤掉、上下文没重置，
#: 于是 `frontend/ React…` 那行里的 "React" 被挂到了上一个目录 `bootstrap/` 底下。
_NOT_SOURCE = ("05-DDD架构.md", "docs/", ".codex/")


def _section_text() -> str:
    text = SECTION.read_text(encoding="utf-8")
    start = text.index("## 8. 目录与依赖方向")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


#: 只认"看起来像路径"的记号：ASCII 字母数字与 `._*{}-`，最多一层 `*/`。
#: 这样正文里的中文（"文档区 / 闭环区"）与箭头注释（"← LangGraph"）不会被当成路径。
_PATHY = re.compile(r"^[A-Za-z0-9_.*-]+(/[A-Za-z0-9_.*-]+)*/?$")


def _tokens_in(line: str) -> list[str]:
    cleaned = line.replace("│", " ").replace("├──", " ").replace("└──", " ")
    return [
        token
        for token in (raw.strip("`(),") for raw in cleaned.split())
        if token and not token.startswith("*") and _PATHY.match(token)
        and not any(marker in token for marker in _NOT_SOURCE)
    ]


def _path_tokens() -> set[str]:
    """§8 代码块里提到的**完整源码路径**。

    图是树形的、文件名是裸的（`authoring/` 后面直接跟 `document.py blocks.py …`），
    所以按行扫描并维护目录上下文。层级由**缩进**判定：

    - `├── domain/`（行首无 `│`）→ 顶层，`top` 与 `current` 都换成它；
    - `│   ├── authoring/`（行首有 `│`）→ `current = top + 它`。

    **约定**（守卫与文档的契约）：一行至多一个目录记号、放在行首、子目录缩进一级。
    写坏约定不会报错，只会让路径悄悄解析到错的父目录下——所以
    `test_the_diagram_uses_one_directory_per_line` 盯着它。
    """

    resolved: set[str] = set()
    top = ""
    current = ""
    for block in _BLOCK.findall(_section_text()):
        for line in block.splitlines():
            depth_two = line.lstrip().startswith("│")
            tokens = _tokens_in(line)
            for token in tokens:
                if token.endswith("/"):
                    name = token.rstrip("/")
                    if not depth_two and not (PACKAGE / name).is_dir():
                        # 走到 `frontend/` / `tests/` 那两行了——它们不是源码树，
                        # 且正文里的"文档区 / 闭环区"带斜杠，别当成路径。
                        top = current = ""
                        continue
                    current = f"{top}{name}/" if depth_two else f"{name}/"
                    if not depth_two:
                        top = current
                    resolved.add(current)
                elif current:
                    # `current` 为空说明已经走出源码树（到 frontend/tests 那两行），
                    # 那里的裸词（"React"、"architecture"）不是路径。
                    resolved.add(f"{current}{token}")
    return resolved


def test_the_diagram_uses_one_directory_per_line():
    """**每行至多一个目录记号，且在行首**——上面那个解析器靠这条约定工作。

    写坏约定不会报错，只会让某些路径解析到错的父目录下——那比没有守卫更坏。
    这条把约定本身变成可执行的。
    """

    offenders: list[str] = []
    for block in _BLOCK.findall(_section_text()):
        for line in block.splitlines():
            tokens = _tokens_in(line)
            directories = [t for t in tokens if t.endswith("/")]
            if len(directories) > 1 or (directories and tokens[0] != directories[0]):
                offenders.append(line.strip())

    assert offenders == [], f"这些行有多个目录记号，或目录记号不在行首：{offenders}"


def test_every_path_the_diagram_mentions_really_exists():
    """**文档提到的路径必须存在**——挡住"图上的幽灵文件"。"""

    missing = sorted(
        token
        for token in _path_tokens()
        # `*` 是数据文件的通配（`definitions/*.toml`）：至少要有一个匹配
        if not (PACKAGE / token.rstrip("/")).exists()
        and not (PACKAGE / token).exists()
        and not list(PACKAGE.glob(token))
    )

    assert missing == [], (
        f"§8 提到但磁盘上没有：{missing}。"
        "搬了文件忘了改图，或者图上写的是旧名字"
    )


def test_the_diagram_mentions_every_real_module():
    """**磁盘上的模块必须在图里**——挡住"搬了新东西没进图"。

    只比文件名（`round.py`），不比全路径：图是按目录分组的，列的是文件名。
    这样图读起来还是好读的，而守卫照样能抓到"某个模块完全没被提到"。
    """

    section = _section_text()
    undocumented = sorted(
        path.name
        for path in PACKAGE.rglob("*.py")
        if path.name != "__init__.py" and path.name not in section
    )

    assert undocumented == [], f"这些模块 §8 完全没提到：{undocumented}"


def test_the_diagram_mentions_every_real_directory():
    """**每个真实目录都要在图里**——这次漂移里漏掉的是整层（`harness/`），
    只查文件名的话 `harness/model/failure.py` 里的 `failure.py` 有可能在别处出现过，
    整层漏掉反而抓不住。所以目录单独查一遍。"""

    section = _section_text()
    missing = sorted(
        path.name
        for path in PACKAGE.rglob("*")
        if path.is_dir() and path.name not in ("__pycache__",) and path.name not in section
    )

    assert missing == [], f"这些目录 §8 完全没提到：{missing}"


def test_the_diagram_does_not_claim_a_wrong_extension():
    """`.yaml` 是 §8 写错的（真实是 `.toml`）——这类"自相矛盾"靠逐项核对才看得见，
    所以钉死：席位定义只能是 `.toml`。"""

    section = _section_text()

    assert "definitions/*.yaml" not in section, "席位定义是 TOML（stdlib tomllib 读的）"
    assert "definitions/*.toml" in section, "图里要写明席位定义是 .toml"


def test_the_harness_layer_is_in_the_diagram():
    """`harness/` 是 M1–M4 搬出来的整层，四次搬迁都没进图。单独钉一条。"""

    section = _section_text()

    assert "harness/" in section, "§8 没有 harness/ 这一层"
    for module in ("contracts", "envelope", "structured", "normalize", "failure", "litellm"):
        assert module in section, f"§8 没提 harness 的 {module}"
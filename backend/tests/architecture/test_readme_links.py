"""README 的链接守卫。

**为什么要有**：README 是对外的第一印象，而**烂掉的图片链接是肉眼可见的**。
三条判据：
1. 引用的每张截图**文件真实存在**（否则 GitHub 上是裂图）；
2. 引用的每个文档路径**真实存在**（否则读者照着找会以为代码丢了）；
3. README **不是**设计文档的第二份抄写（那会漂——项目明令禁止双真相源）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "README.md"
SHOTS = ROOT / "docs" / "reports" / "agent-resume" / "screenshots"


def _readme() -> str:
    assert README.is_file(), "README.md 不存在"
    return README.read_text(encoding="utf-8")


def test_the_readme_exists_and_is_not_empty() -> None:
    text = _readme()
    assert len(text) > 3000, "README 太短——它是对外的第一页"
    assert "PM Studio" in text


def test_every_screenshot_the_readme_references_exists() -> None:
    """引用了却不存在的截图 = GitHub 上的裂图。"""

    text = _readme()
    referenced = set(re.findall(r"!\[[^\]]*\]\(([^)]+\.png)\)", text))
    assert referenced, "README 里一张截图都没有——这是对外的第一印象"
    for rel in sorted(referenced):
        assert (ROOT / rel).is_file(), f"README 引用了不存在的截图：{rel}"


def test_the_screenshot_folder_is_actually_populated() -> None:
    """截图目录不能是空的（空目录 = README 里的图全是裂的）。"""

    shots = list(SHOTS.glob("*.png"))
    assert len(shots) >= 3, f"截图只有 {len(shots)} 张，太少"


def test_every_document_the_readme_links_exists() -> None:
    """引用的文档路径必须真实存在。"""

    text = _readme()
    # 只查仓库内的相对路径（跳过 http 链接与锚点）。
    referenced = {
        rel
        for rel in re.findall(r"\]\(([^)#\s]+)\)", text)
        if not rel.startswith(("http://", "https://", "#"))
    }
    missing = [rel for rel in sorted(referenced) if not (ROOT / rel).exists()]
    assert not missing, f"README 引用了不存在的路径：{missing}"


def test_the_readme_does_not_duplicate_the_design_docs() -> None:
    """README 是**入口**，不是设计文档的第二份。

    判据：README 里不出现设计文档的**章节标题行**（那才是抄写）。
    ⚠️ 必须按"行"判，不能按裸子串——README 里可以有"设计系统：…的决策记录"
    这种**描述**（指向 DESIGN.md），但不能有 `## 决策记录` 这个**标题本身**。
    并且长度有上限——超了说明在往里塞内容。
    """

    text = _readme()
    headers = {
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("## ")
    }
    for heading in ("## 8. 目录与依赖方向", "## 1. 一轮的粒度", "## 决策记录"):
        assert heading not in headers, f"README 里出现了设计文档的章节标题：{heading}"
    assert len(text.splitlines()) < 400, "README 太长——它该是入口，不是第二份设计文档"


def test_the_readme_links_the_ai_context_file() -> None:
    """`AGENTS.md` 是这个仓给 AI 的上下文入口——README 必须指向它。"""

    assert "AGENTS.md" in _readme()

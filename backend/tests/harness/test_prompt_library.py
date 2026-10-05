"""提示词库（2026-10-04 · M5 从 `infrastructure/llm/prompt_library.py` 搬来）。

**为什么它是 harness 的东西**：读 `.md`、用 `$slot` 填槽、少给槽位就报错——
这整套是"提示词装配"的机制，与产品写什么提示词无关（内容是数据，留在原处）。

**这条模块原先在 infrastructure，却实现着 harness 的协议**
（`harness/model/contracts.py` 的 `PromptLibrary`）——那是个既有的错位，M5 顺手纠正：
实现该和它的协议住在一起。

**M5 的新增覆盖**：原先只测了 `analyzer` 一个提示词能渲染，**另外 4 个没人验过**。
搬目录不产生 import 错误，只在读 `.md` 时炸——所以"5 个都能渲染"这条必须在。
"""

from string import Template

import pytest

from pmstudio.harness.prompt import PROMPTS_DIR, FilePromptLibrary

#: 搬之前目录里就有这 5 个。少了任何一个，说明搬的时候漏了数据文件。
EXPECTED_PROMPTS = (
    "analyzer",
    "discussion_cards",
    "discussion_say",
    "inquiry",
    "integrator",
    "memory_curator",
    "opinion_dispositions",
    "seat_review",
)


def test_the_prompts_directory_sits_next_to_the_module_that_reads_it():
    """**路径跟随资源**：目录由模块自己算，不许调用方各自重算。

    和 `test_registry.py` 里那条同源：M5 之前有 5 处各自重算（`container.py`、
    `test_llm.py`、`test_analyzer.py`、`test_catalog.py`、`test_seats_catalog.py`），
    搬目录时只有碰巧用到的那一处会红，其余**静默指向空目录**。
    """

    from pathlib import Path

    import pmstudio.harness.prompt as prompt_module

    assert PROMPTS_DIR == Path(prompt_module.__file__).parent / "prompts"
    assert PROMPTS_DIR.is_dir(), f"{PROMPTS_DIR} 不存在：搬目录时漏了提示词目录"


def test_every_prompt_on_disk_is_there_and_all_of_them_render():
    """**5 个 `.md` 全渲染**——M5 指定的必测项。

    槽位**不在测试里写死**，而是从模板自己发现的（`Template.get_identifiers()`）：
    写死的话，某个提示词新加一个槽位，这条测试就会因为"多了一个没给的槽位"而红，
    而那正是它应该提醒的事（新槽位忘了在调用方给值 = 渲染时炸）。
    反过来，用发现式就不会因为提示词改了槽位名而误报。

    这里真正验的是：**每个提示词的每个槽位都能被填上，不会漏**。
    """

    library = FilePromptLibrary(PROMPTS_DIR)
    on_disk = sorted(path.stem for path in PROMPTS_DIR.glob("*.md"))

    assert on_disk == sorted(EXPECTED_PROMPTS), "提示词目录的内容变了，确认一下是有意为之"

    for name in on_disk:
        template = Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))
        slots = {slot: f"<{slot}>" for slot in template.get_identifiers()}

        rendered = library.render(name, **slots)

        assert rendered.strip(), f"{name} 渲染出空串"
        # 每个槽位的值都真的落进去了（没落进去 = substitute 静默跳过 = 提示词少了一段）
        for slot, value in slots.items():
            assert value in rendered, f"{name} 的 ${slot} 没被填进去"


def test_missing_slot_raises_instead_of_leaving_it_blank():
    """少给槽位**要炸**，不许静默留空——留空的提示词会让模型"自己编"，而这一轮看起来还成功。"""

    library = FilePromptLibrary(PROMPTS_DIR)

    with pytest.raises(KeyError):
        library.render("analyzer", document_state="# 文档")


def test_an_unknown_prompt_name_raises_and_names_the_path():
    """写错提示词名要立刻炸，且报错要带上那个路径，否则得到一个不存在的文件名。"""

    library = FilePromptLibrary(PROMPTS_DIR)

    with pytest.raises(FileNotFoundError) as caught:
        library.render("nope")

    assert "nope.md" in str(caught.value)


def test_the_library_satisfies_the_harness_protocol():
    """实现与协议住在同一层之后，这条从"错位"变成"名副其实"。"""

    from pmstudio.harness.model.contracts import PromptLibrary

    assert isinstance(FilePromptLibrary(PROMPTS_DIR), PromptLibrary)


def test_render_is_cached_but_substitution_is_not():
    """模板原文缓存，**替换**每次都做——否则第二次调用会返回上一次填好的串。

    这条钉住"缓存的是原文不是成品"：提示词模板读一次，但每轮都要用自己的槽位填。
    """

    library = FilePromptLibrary(PROMPTS_DIR)
    template = Template((PROMPTS_DIR / "memory_curator.md").read_text(encoding="utf-8"))
    slots = {slot: f"<{slot}>" for slot in template.get_identifiers()}

    first = library.render("memory_curator", **slots)
    again = library.render("memory_curator", **{**slots, "round_digest": "第二轮"})

    assert first != again
    assert "<round_digest>" in first
    assert "第二轮" in again

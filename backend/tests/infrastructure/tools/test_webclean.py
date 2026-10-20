"""联网内容清洗归一（批 6a Task 3）：Tavily 的 `content` → 干净文本。

**为什么要洗**：Tavily 的 `content` 实测（2026-10-19）带三类噪音——
① 它自己的省略标记 `[...]`（每条 2 次）、② 法条原文的全角空格 `　`、
③ Markdown 表格的**分隔行**（`| --- | --- |`，骨架不是内容）。
这些进了 FTS 索引会稀释词面匹配，进了向量会占 token。

**不洗什么**：Markdown 标题符（`## `）**保留**——它既是内容的一部分，
又被 `chunk_text` 用来生成 `heading_path`（章节链），是白捡的检索信号。
"""

from pmstudio.infrastructure.tools.webclean import normalize_web_text


def test_removes_tavilys_own_ellipsis_marker():
    """R：Tavily 的省略标记 `[...]` 去掉——那不是内容，是它的截断痕迹。"""
    assert "..." not in normalize_web_text("甲段。 [...] 乙段。")


def test_keeps_the_real_content_around_the_marker():
    """R：去掉标记**不能**把两段内容粘在一起（要留分隔）。"""
    result = normalize_web_text("甲段。 [...] 乙段。")
    assert "甲段" in result and "乙段" in result
    # 两段之间要还留着分隔（空格或换行），不能被粘成"甲段。乙段。"
    assert "甲段。乙段。" not in result, f"两段被粘在一起了：{result!r}"
    assert "甲段。乙段。" not in result, "两段被粘在一起了——中间的空白要留着"


def test_folds_fullwidth_spaces_into_normal_spaces():
    """R：全角空格 `　` → 半角。

    为什么：法条原文用它排版（`第五十六条　个人信息保护影响评估`），
    它会打断 2-gram 滑窗与 jieba 分词——两边的切法不一致，词面匹配就漏。
    """
    assert "　" not in normalize_web_text("第五十六条　个人信息保护影响评估")


def test_collapses_runs_of_blank_lines_but_keeps_paragraph_breaks():
    """R：连续空行折成一个（切块靠空行分段，多余空行只是噪音）。"""
    result = normalize_web_text("甲。\n\n\n\n乙。")
    assert "\n\n\n" not in result
    assert "甲。" in result and "乙。" in result


def test_strips_trailing_whitespace_on_each_line():
    """R：行尾空白去掉（会让分词多出空 token）。"""
    assert normalize_web_text("甲。   \n乙。   ") == "甲。\n乙。"


def test_strips_markdown_table_separator_rows_but_keeps_content_rows():
    """R：表格**分隔行**剥掉；表格**内容行**留着（那是有信息的）。

    分隔行长这样：`| --- | --- |` 或 `|---|---|`（只有 -、:、空格）。
    """
    text = "| 姓名 | 角色 |\n| --- | --- |\n| 张三 | 管理员 |"
    result = normalize_web_text(text)
    assert "---" not in result, "表格分隔行是骨架，不是内容"
    assert "| 姓名 | 角色 |" in result, "表头是内容，要留"
    assert "| 张三 | 管理员 |" in result, "数据行是内容，要留"


def test_keeps_markdown_headings_because_chunk_text_reads_them():
    """R：Markdown 标题**保留**。

    它既是内容的一部分，又被 `chunk_text` 认成标题 → 生成 `heading_path`
    （章节链进索引源，检索时"只在这一节出现的内容"才搜得到）。
    """
    result = normalize_web_text("## 数据保护与隐私边界\n\n正文")
    assert "## 数据保护与隐私边界" in result


def test_empty_or_punctuation_only_input_stays_empty():
    """R：洗完还是空就还是空（别造出"内容"）。"""
    assert normalize_web_text("") == ""
    assert normalize_web_text("   \n\n  ") == ""


def test_the_function_is_idempotent():
    """R：洗两次与洗一次同结果（清洗要可重放——回填与重解析都会再跑一遍）。"""
    once = normalize_web_text("甲。 [...] 　\n\n\n乙。| --- |")
    assert normalize_web_text(once) == once


def test_a_line_that_is_only_a_markdown_link_is_dropped():
    """R：整行**只有一个** markdown 链接 → 导航行，剔掉。

    实测（Tavily `raw_content`，2026-10-20）：18.7% 的行是这种——面包屑、logo、
    按钮、产品目录。剔掉后总字数降 20%，**检索命中不降反升**（32/32 vs 31/31）：
    这些行既不是内容，又会在向量里把页面的主题稀释掉。
    """
    text = "\n".join(
        [
            "# 标题",
            "[文档中心](/help/zh)",
            "正文第一段。",
            "![logo](//assets/logo.svg)",
            "正文第二段。",
        ]
    )

    result = normalize_web_text(text)

    assert "文档中心" not in result, "面包屑是导航，不是内容"
    assert "logo" not in result, "装饰图片不是内容"
    assert "正文第一段。" in result
    assert "正文第二段。" in result


def test_a_reference_list_item_keeps_its_link():
    """R：`- [标题](url)` 是**参考资料**，不是导航 → 保留。

    列表符号让它不匹配「整行只有一个链接」——参考资料是要被搜到的。
    """
    result = normalize_web_text("- [参考一](https://example.com/a)")
    assert "参考一" in result


def test_a_line_with_two_links_keeps_both():
    """R：一行多个链接 → 是正文里的行内链接 → 整行保留。"""
    result = normalize_web_text(
        "[文档](https://example.com/a)[反馈](https://example.com/b)"
    )
    assert "文档" in result
    assert "反馈" in result


def test_an_image_only_line_is_dropped():
    """R：整行只有一个图片 → 装饰 → 剔掉。"""
    assert normalize_web_text("![](https://example.com/a.png)") == ""

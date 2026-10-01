"""把上一次的报文错误翻译成"给模型看的一句纠错指引"（重装时才用）。

两条规矩：

1. **重装必须带纠错指引**：拿同一句提示词再撞一堵墙是 `learnings.md` L13 定性过的失败模式。
2. **不复制模型写出的内容片段**（`raw_span`、正文、选项措辞一个都不回喂），只说"第几条、
   哪个字段、要求是什么"。把写错的那串原样喂回去，等于给它一个可以照抄的模板——
   `（隐含于上下文…）` 这类说明被填进 `raw_span` 过两次（L20）。
"""

from pmstudio.application.errors import PacketParseError

#: 说不上具体是哪一种时的兜底话术
GENERIC = "请按上面的硬规矩重新切一遍，只输出 JSON Schema 要求的那个对象。"

#: `kind` 缺失/写错时给的具体话术（P11 C3：默认值不再兜底，判据必须递到它眼前）
_KIND_GUIDANCE = (
    "kind 只能取 gap / conflict / decision：**两侧立场互斥**"
    "（有人说过 A、有人说不能 A）→ conflict；"
    "只是**还没定**（谁都没表过态）→ gap；要用户在两个方案里挑一个 → decision"
)


def repair_note(error: PacketParseError) -> str:
    """上一次的 `PacketParseError` → 第二段提示词（纯函数，可离线测）。"""

    lines = [f"上一次的报文没通过校验：{error.message}"]
    details = error.details
    if "claim_id" in details:
        position = details.get("position")
        where = (
            f"第 {position} 条主张（{details['claim_id']}）"
            if isinstance(position, int)
            else f"主张 {details['claim_id']}"
        )
        lines.append(
            f"{where}的 raw_span 在用户输入里找不到对应的连续原话。"
            "raw_span 必须逐字取自用户输入、而且连续出现；"
            "这条主张在原话里没有落点，就不要切它。"
        )
    elif "seat_id" in details:
        lines.append(
            "席位提名里有目录里没有的 id。seat_id 只能从上面的席位目录里原样照抄，"
            "连 `seat.` 前缀一起（目录里写的是 `seat.tech_approach`，就写 `seat.tech_approach`）。"
        )
    elif "overflow" in details:
        # 超限是**可修复的错**：只说"哪一包、给了几条、上限几条、按什么优先级删"。
        # 放在 errors 分支之前：errors 只会列出字段路径，说不出"删到几条"。
        container, got, limit = details["overflow"]  # type: ignore[misc]
        lines.append(
            f"你这一次给了 {got} 条 {container}，上限是 {limit} 条。"
            f"请精简、合并或删到 {limit} 条以内：同一件事只说一次，"
            "优先保留 s5 功能字段（name / one_liner / goal / audience / exceptions / acceptance）"
            "与已经定下来的验收标准，删掉重复与推测。"
        )
    elif "packet_shape" in details:
        # 形状错比字段路径更根本（字段路径会只报一句"(根)"，模型看了也不知道欠什么）。
        shape = details["packet_shape"] or {}
        keys = "、".join(shape.get("expected_keys", ())) or "（见下面的 schema）"
        lines.append(
            f"你返回的是**{shape.get('got', '未知形状')}**，而我要的是一个**对象**："
            f"顶层只允许这些键——{keys}；每个键的值才是数组。"
            '例如：{"questions": [ … ], "proposals": [ … ]}。不要只输出里面那个数组。'
        )
    elif "discarded" in details:
        # 真跑 F17：`opinions[1]` 是一个占位符字符串。它不是"内容错"（那一"条"没有内容可判），
        # 是形状错——与超限同一套话术口径：说清**哪一条**、**该是什么形状**。
        container, index = details["discarded"]  # type: ignore[misc]
        lines.append(
            f"{container} 里**第 {index} 条不是对象**：这一项必须是对象（形状见下面的 schema），"
            "不能是字符串或数字。没内容就不要写占位符——要么给一条真内容，"
            "要么把这一项删掉（有问题就把它写成**对应容器里**的一条正经内容）。"
        )
    elif "errors" in details:
        lines.append("过不了 schema 的字段（逐条改）：\n" + _schema_guidance(details["errors"]))
    elif "offset" in details:
        lines.append(
            "返回的内容不是一个完整的 JSON 对象：只输出那个对象，"
            "不要解释文字、不要代码块围栏。"
        )
    else:
        lines.append(GENERIC)
    return "\n".join(lines)


#: pydantic 的错误类型 → 一句"这里该是什么形状"（模型看得懂才改得对）
_TYPE_GUIDANCE = {
    "list_type": "这里要是数组；没有内容就写 []（不要写 null）",
    "string_type": "这里要是字符串",
    "dict_type": "这里要是对象",
    "model_type": "这里要是对象",
    "missing": "是必填字段，必须给",
    "literal_error": "取值只能从枚举里挑",
    "int_type": "这里要是整数",
    "bool_type": "这里要是 true / false",
}


def _schema_guidance(errors: object, *, limit: int = 4) -> str:
    """`errors()` → 逐条"哪个字段 + 该是什么形状"（最多 `limit` 条）。

    只报字段路径（旧写法）模型看不出欠什么：真跑里它把 `evidence_refs` 写成 `null`、
    把 `form` 写成空，收到"字段路径"之后第二次照错（P8 口径）。
    """

    if not isinstance(errors, list):
        return "字段名与取值都只能取 schema 里列出的那些。"
    lines: list[str] = []
    for item in errors[:limit]:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc", ())
        path = ".".join(str(part) for part in loc) or "(根)"
        hint = _TYPE_GUIDANCE.get(str(item.get("type")), "")
        if loc and str(loc[-1]) == "kind":
            # 题目的 kind 是"要它做判断"的字段：光说"必填"它只会随便挑一个（P11 C3）
            hint = f"{hint}；{_KIND_GUIDANCE}" if hint else _KIND_GUIDANCE
        expected = (item.get("ctx") or {}).get("expected")
        if expected:
            hint = f"{hint}（允许：{expected}）" if hint else f"允许：{expected}"
        lines.append(f"- {path}：{hint or str(item.get('msg', ''))}")
    return "\n".join(lines) or "字段名与取值都只能取 schema 里列出的那些。"

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
    elif "errors" in details:
        lines.append(
            f"过不了 schema 的字段：{_schema_paths(details['errors'])}。"
            "字段名与取值都只能取 schema 里列出的那些。"
        )
    elif "offset" in details:
        lines.append(
            "返回的内容不是一个完整的 JSON 对象：只输出那个对象，"
            "不要解释文字、不要代码块围栏。"
        )
    else:
        lines.append(GENERIC)
    return "\n".join(lines)


def _schema_paths(errors: object, *, limit: int = 5) -> str:
    """pydantic 的 `errors()` → `claims.2.label` 这种字段路径（最多列 `limit` 条）。"""

    if not isinstance(errors, list):
        return "(未知字段)"
    paths = [
        ".".join(str(part) for part in (item.get("loc", ()) if isinstance(item, dict) else ()))
        or "(根)"
        for item in errors[:limit]
    ]
    return "、".join(paths) or "(未知字段)"

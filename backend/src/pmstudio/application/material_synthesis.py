"""文本材料的公共信封合成（M8 探索与 M9 讨论共用）。

`candidate_material`（`exploration.py:107`）里"组装 `ParsedMaterial` + 单 chunk +
`text/plain`"那几行，各材料来源都要写一遍——抽出来之后调用方只说
"来源 / 标题 / 引用 / 正文 / 锚点"这五样，信封形状一处说了算。

去重键 `material_sha(namespace, source_ref)`：命名空间隔离（文件哈希 / 探索变体 /
讨论变体三者互不顶掉），同命名空间内同引用稳定。
"""

import hashlib

from pmstudio.application.dto import MaterialChunk, ParsedMaterial


def material_sha(namespace: str, source_ref: str) -> str:
    """材料去重的变体键：`sha256("{namespace}:{source_ref}")`。"""

    return hashlib.sha256(f"{namespace}:{source_ref}".encode()).hexdigest()


def synthesize_text_material(
    *,
    origin: str,
    title: str,
    source_ref: str,
    text: str,
    anchor: str,
    parser_id: str,
    parser_version: str,
) -> tuple[ParsedMaterial, bytes]:
    """一段现成的文本合成一份材料草稿（不经过文件解析器）：单 chunk，序号 0。"""

    content = text.encode("utf-8")
    parsed = ParsedMaterial(
        origin=origin,
        title=title,
        source_ref=source_ref,
        media_type="text/plain",
        bytes_len=len(content),
        chunks=(
            MaterialChunk(
                chunk_id=source_ref,
                ordinal=0,
                text=text,
                heading_path=(),
                anchor=anchor,
            ),
        ),
        parser_id=parser_id,
        parser_version=parser_version,
    )
    return parsed, content

"""C15 Citation：引用必须能定位，形状只定义一次。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import CitationTargetType
from pmstudio.contracts.models.citation import Citation


def test_block_citation_must_carry_target_version() -> None:
    """I6 的记忆失效判定靠它：现在版本比它大，就说明引用内容被改过。"""
    citation = Citation(
        target_type=CitationTargetType.BLOCK,
        target_id="blk_1",
        target_version=3,
    )
    assert citation.target_version == 3
    with pytest.raises(ValidationError):
        Citation(target_type=CitationTargetType.BLOCK, target_id="blk_1")


@pytest.mark.parametrize(
    "target_type",
    [
        CitationTargetType.MEMORY,
        CitationTargetType.MATERIAL,
        CitationTargetType.IDEA,
        CitationTargetType.USER_INPUT,
        CitationTargetType.CLAIM,
    ],
)
def test_non_block_citation_rejects_target_version(target_type: CitationTargetType) -> None:
    with pytest.raises(ValidationError):
        Citation(target_type=target_type, target_id="x_1", target_version=2)


def test_target_id_must_be_present() -> None:
    with pytest.raises(ValidationError):
        Citation(target_type=CitationTargetType.MEMORY, target_id="")


def test_quote_is_optional() -> None:
    citation = Citation(target_type=CitationTargetType.USER_INPUT, target_id="rnd_1")
    assert citation.quote is None
    assert citation.target_version is None


def test_all_six_target_types_are_usable() -> None:
    """六档都要能构造，少一档就说明契约被削过了。"""
    for target_type in CitationTargetType:
        version = 1 if target_type is CitationTargetType.BLOCK else None
        Citation(target_type=target_type, target_id="x_1", target_version=version)


def test_target_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Citation(target_type=CitationTargetType.BLOCK, target_id="blk_1", target_version=0)

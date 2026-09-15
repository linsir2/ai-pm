"""C11 Trace：一轮用了什么，含被裁掉的上下文与主张留痕。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import CitationTargetType, ClaimKind, ContextBlockSource
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.trace import ClaimDigest, Trace
from pmstudio.contracts.skeleton.board import ContextBlock, ContextRegion

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _digest() -> ClaimDigest:
    return ClaimDigest(
        packet_id="pkt_1",
        claim_id="clm_1",
        role_id="reg_role_delivery",
        statement="交付物必须包含接口文档",
        kind=ClaimKind.PROPOSAL,
        citations=(Citation(target_type=CitationTargetType.BLOCK, target_id="blk_1", target_version=1),),
    )


def test_field_set_matches_contract() -> None:
    assert set(Trace.model_fields) == {
        "trace_id",
        "round_id",
        "context_used",
        "tools_called",
        "final_version_id",
        "claims",
    }


def test_context_used_keeps_what_was_dropped() -> None:
    kept = ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_1", priority=10)
    dropped = ContextBlock(source=ContextBlockSource.MATERIAL, ref="mat_1", priority=1, credibility="low")
    trace = Trace(
        trace_id="trc_1",
        round_id="rnd_1",
        context_used=ContextRegion(blocks=(kept,), dropped=(dropped,), assembled_at=NOW),
    )
    assert len(trace.context_used.blocks) == 1
    assert trace.context_used.dropped[0].ref == "mat_1"


def test_round_that_wrote_nothing_has_no_final_version() -> None:
    trace = Trace(
        trace_id="trc_1",
        round_id="rnd_1",
        context_used=ContextRegion(assembled_at=NOW),
    )
    assert trace.final_version_id is None
    assert trace.tools_called == ()
    assert trace.claims == ()


def test_claim_digest_field_set_matches_contract() -> None:
    """主张要留痕，出口是 trace——否则"这个分歧怎么来的"事后答不上来。"""
    assert set(ClaimDigest.model_fields) == {
        "packet_id",
        "claim_id",
        "role_id",
        "statement",
        "kind",
        "citations",
    }


def test_claim_digest_requires_every_field() -> None:
    digest = _digest()
    assert digest.kind is ClaimKind.PROPOSAL
    with pytest.raises(ValidationError):
        ClaimDigest(
            packet_id="pkt_1",
            claim_id="clm_1",
            role_id="reg_role_delivery",
            statement="  ",
            kind=ClaimKind.PROPOSAL,
        )


def test_tools_called_is_a_list_of_tool_ids() -> None:
    trace = Trace(
        trace_id="trc_1",
        round_id="rnd_1",
        context_used=ContextRegion(assembled_at=NOW),
        tools_called=("reg_tool_web_search", "reg_tool_retrieval"),
    )
    assert trace.tools_called == ("reg_tool_web_search", "reg_tool_retrieval")

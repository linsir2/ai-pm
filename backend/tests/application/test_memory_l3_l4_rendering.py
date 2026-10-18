"""L4 + L3 应用层：build_round_digest、render_digests、render_inspirations_index。"""


from pmstudio.application.context import (
    DIGEST_LIMIT,
    L4_CAPACITY,
    build_round_digest,
    render_digests,
    render_inspirations_index,
)
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from pmstudio.domain.shared.target import Target


def _proposal(op: str = "add", section_id: str = "s5", feature_slug: str | None = "group-buying",
              field: str | None = "one_liner", text: str = "社区团购平台") -> Proposal:
    target = Target.body(
        section_id=section_id,
        feature_slug=FeatureSlug(feature_slug) if feature_slug else None,
        field=field,
    )
    return Proposal(
        proposal_id=f"p-{section_id}-{field or 'none'}",
        entry_id=f"e-{section_id}-{field or 'none'}",
        op=op,
        target=target,
        text=text,
        origin=Origin(type=OriginType.USER, refs=(EvidenceId("E1"),)),
    )


class TestBuildRoundDigest:
    """build_round_digest: 从已确认提案构建摘要（纯函数）。"""

    def test_empty_proposals(self):
        digest = build_round_digest([], "r1", "p1")
        assert digest.round_id == "r1"
        assert digest.sections_touched == ()
        assert digest.features_addressed == ()
        assert digest.key_outcomes == ()

    def test_extracts_sections(self):
        proposals = [
            _proposal(section_id="s1", feature_slug=None, field=None, text="背景"),
            _proposal(
                section_id="s5",
                feature_slug="group-buying",
                field="one_liner",
                text="社区团购",
            ),
        ]
        digest = build_round_digest(proposals, "r1", "p1")
        assert set(digest.sections_touched) == {"s1", "s5"}

    def test_extracts_features(self):
        proposals = [
            _proposal(section_id="s5", feature_slug="group-buying"),
            _proposal(section_id="s5", feature_slug="presale"),
        ]
        digest = build_round_digest(proposals, "r1", "p1")
        assert set(digest.features_addressed) == {"group-buying", "presale"}

    def test_extracts_outcomes(self):
        proposals = [_proposal(text="社区团购平台"), _proposal(op="replace", text="预售模式")]
        digest = build_round_digest(proposals, "r1", "p1")
        assert len(digest.key_outcomes) == 2
        assert any("社区团购" in o for o in digest.key_outcomes)
        assert any("预售" in o for o in digest.key_outcomes)

    def test_outcomes_limit_5(self):
        proposals = [_proposal(text=f"条目{i}") for i in range(10)]
        digest = build_round_digest(proposals, "r1", "p1")
        assert len(digest.key_outcomes) <= 5
        assert digest.outcomes_total == 10

    def test_outcomes_total_is_full_count(self):
        digest = build_round_digest([_proposal(text="甲"), _proposal(text="乙")], "r1", "p1")
        assert digest.outcomes_total == 2


class TestRenderDigests:
    """render_digests: 渲染摘要列表。"""

    def test_empty(self):
        assert render_digests(() ) == ""

    def test_single_digest(self):
        d = RoundDigest(
            round_id="r1", project_id="p1",
            summary="触及 s1、s5，确认 2 条",
            sections_touched=("s1", "s5"),
            key_outcomes=("定位=社区团购",),
        )
        text = render_digests((d,))
        assert "r1" in text
        assert "s1" in text
        assert "社区团购" in text

    def test_contains_round_digests_header(self):
        d = RoundDigest(round_id="r1", project_id="p1", summary="摘要")
        text = render_digests((d,))
        assert "<round-digests>" in text
        assert "</round-digests>" in text

    def test_notes_the_round_limit_only_at_capacity(self):
        """只说"只带最近 N 轮"这个**限制**；不知道有没有更早，就不虚报。"""
        d = RoundDigest(round_id="r1", project_id="p1", summary="摘要")

        assert "只带最近 3 轮摘要" not in render_digests((d,))
        text = render_digests((d, d, d))
        assert "只带最近 3 轮摘要" in text

    def test_notes_omitted_outcomes(self):
        """摘要自己只留前 5 条成果，也要照实说。"""
        d = RoundDigest(
            round_id="r1",
            project_id="p1",
            summary="摘要",
            key_outcomes=tuple(f"成果{i}" for i in range(5)),
            outcomes_total=10,
        )
        text = render_digests((d,))
        assert "共 10 条" in text
        assert "只带了前 5 条" in text

    def test_over_capacity_notes_the_older_rounds_wait_for_review(self):
        """R：L4 超水位照实说"更早的待复核"。

        调用点已不再预截断（要让渲染层看得到总数），所以截断必须落在渲染层这里；
        否则 50 轮摘要会整份进提示词，而"只带最近 3 轮"这句就成了假话。
        """
        digests = tuple(
            RoundDigest(round_id=f"r{i:03d}", project_id="p1", summary="摘要")
            for i in range(L4_CAPACITY + 1)
        )
        text = render_digests(digests)

        assert f"### r{DIGEST_LIMIT - 1:03d}" in text
        assert f"### r{DIGEST_LIMIT:03d}" not in text
        assert f"更早的 {L4_CAPACITY + 1 - DIGEST_LIMIT} 轮待复核" in text

    def test_below_capacity_does_not_mention_waiting_for_review(self):
        """没超水位不多话（水位不是常驻噪音）。"""
        digests = tuple(
            RoundDigest(round_id=f"r{i}", project_id="p1", summary="摘要")
            for i in range(DIGEST_LIMIT)
        )
        text = render_digests(digests)

        assert "只带最近 3 轮摘要" in text
        assert "待复核" not in text


class TestRenderInspirationsIndex:
    """render_inspirations_index: 渲染灵感索引。"""

    def test_empty(self):
        assert render_inspirations_index(()) == ""

    def test_limit_5(self):
        items = tuple(
            InspirationItem(
                item_id=f"ins-{i}", project_id="p1",
                origin=InspirationOrigin.DISCUSSION, statement=f"灵感{i}"
            )
            for i in range(10)
        )
        text = render_inspirations_index(items)
        # 只渲染前 5 条
        assert "灵感0" in text
        assert "灵感4" in text
        # 第 6 条不渲染
        assert "灵感5" not in text

    def test_limit_5_notes_omitted(self):
        items = tuple(
            InspirationItem(
                item_id=f"ins-{i}", project_id="p1",
                origin=InspirationOrigin.DISCUSSION, statement=f"灵感{i}"
            )
            for i in range(10)
        )
        text = render_inspirations_index(items)
        assert "共 10 条" in text
        assert "只带了前 5 条" in text

    def test_format_includes_origin(self):
        items = (
            InspirationItem(
                item_id="ins-1", project_id="p1",
                origin=InspirationOrigin.DISCUSSION, statement="团长流失率15%"
            ),
        )
        text = render_inspirations_index(items)
        assert "discussion" in text
        assert "团长流失率15%" in text

    def test_contains_header(self):
        items = (
            InspirationItem(
                item_id="ins-1", project_id="p1",
                origin=InspirationOrigin.DISCUSSION, statement="灵感"
            ),
        )
        text = render_inspirations_index(items)
        assert "<inspirations-index>" in text
        assert "</inspirations-index>" in text

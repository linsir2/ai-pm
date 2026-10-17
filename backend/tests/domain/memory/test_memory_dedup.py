"""记忆机械去重（记忆 P2）：只做**完全重复**，相似度不做。"""

from pmstudio.domain.memory.memory import normalized_text


class TestNormalizedText:
    def test_whitespace_is_ignored(self):
        assert normalized_text("不要  做\n多租户") == normalized_text("不要做多租户")

    def test_trailing_punctuation_is_ignored(self):
        assert normalized_text("不要做多租户。") == normalized_text("不要做多租户")

    def test_leading_and_trailing_only(self):
        # 中间的问号是内容的一部分，不许剥
        assert normalized_text("先定范围？再谈排期") != normalized_text("先定范围再谈排期")

    def test_full_width_and_half_width_punctuation_are_the_same(self):
        assert normalized_text("不要做多租户，") == normalized_text("不要做多租户,")

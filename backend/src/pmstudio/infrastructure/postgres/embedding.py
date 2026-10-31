"""向量适配（检索阶段 2）：**模型只在这一处**，换模型/换库只改这里 + `container.py` 一行。

`fastembed` 用 ONNX（无 torch），`BAAI/bge-small-zh-v1.5` = 512 维 / MIT / ~90MB，
**懒加载**：不碰检索的进程（绝大多数测试与 CLI）永远不下载模型、不载 onnxruntime。
"""

import logging
from collections.abc import Sequence
from typing import Protocol

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DIMENSIONS = 512

#: 嵌入模型的 **token 上限**（**模型契约**，不是配置项）。
#:
#: BAAI 官方明确：超过 512 会被**直接截断**（`FlagEmbedding#370`）——而 fastembed
#: 的截断是**静默的**（它读 `tokenizer_config` 的 `model_max_length`，Python API
#: 不暴露每次调用的上限）。所以**上限必须在自家层留一道闸**：喂进去的文本
#: 超了窗口，尾部对向量**完全不可见**（实测 2026-10-21：尾部加独特标记词，
#: 向量余弦 1.000000）。
#:
#: 它与 `MODEL_NAME` 同处一个文件（换模型时连同改）——上限是模型的事。
MODEL_MAX_TOKENS = 512

#: 留给特殊 token（`[CLS]`/`[SEP]`）与未知的余量。
EMBED_TOKEN_MARGIN = 32

#: 喂给模型的**文本**最多多少 token（含特殊 token）。
EMBED_INPUT_MAX_TOKENS = MODEL_MAX_TOKENS - EMBED_TOKEN_MARGIN

#: **向量口径**（模型 + 喂进去的文本形态）——变了旧向量就是脏的。
#:
#: 口径串的组成：`<模型名>/<文本形态>`。文本形态 `natural` = 标题+章节链+正文
#: **原样不分词**（2026-10-19 从词袋串改过来：分词串把自然语言退化成词袋，
#: bge 的语义优势被扔掉一半，实测改述类 recall@5 85% → 95%）。
#:
#: 这个串存在库的 `vector_index_meta` 表里；profile 变更时清空
#: `material_chunks.embedding` 并重建 HNSW（见 `vector_index.ensure_vector_profile`）
#: ——否则新旧两种口径的向量会混在一张表里，检索结果不可复现。
EMBED_PROFILE = f"{MODEL_NAME}/natural"


class Embedder(Protocol):
    dimensions: int

    def embed_many(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


class FastEmbedEmbedder:
    def __init__(
        self,
        model_name: str = MODEL_NAME,
        *,
        lazy_load: bool = True,
        local_files_only: bool = False,
    ) -> None:
        self.dimensions = DIMENSIONS
        self._model = None
        self._model_name, self._lazy = model_name, lazy_load
        self._local_files_only = local_files_only
        #: 本批里被裁掉的 token 数与条数（**按批聚合后告警一次**，不逐条刷屏）
        self._dropped_tokens = 0
        self._dropped_count = 0
        if not self._lazy:
            self._ensure()

    def _ensure(self) -> None:
        if self._model is None:
            # 延迟 import：不碰检索就不付 onnxruntime 的启动成本
            from fastembed import TextEmbedding

            options = {"local_files_only": True} if self._local_files_only else {}
            self._model = TextEmbedding(model_name=self._model_name, **options)
        return self._model

    def embed_many(self, texts):
        model = self._ensure()
        fitted = [self._fit_to_window(text) for text in texts]
        if self._dropped_count:
            logger.warning(
                "向量输入超出模型窗口（%d token）：本批 %d 条被裁，合计裁掉 %d token",
                MODEL_MAX_TOKENS,
                self._dropped_count,
                self._dropped_tokens,
            )
            self._dropped_tokens = 0
            self._dropped_count = 0
        return [tuple(float(x) for x in vector) for vector in model.embed(fitted)]

    def _fit_to_window(self, text: str) -> str:
        """把输入裁到模型窗口内（**这是唯一真正把文本送进模型的地方**）。

        为什么裁的是**尾部**：`index_text` 的拼装顺序是
        `标题 + 章节链 + 正文` → 尾部先被牺牲的是**正文的尾部**，留下
        "这段是关于什么的"（定位信号）——细节还能靠词面检索兜底
        （与 "The Power of Noise" 同向：语义侧保主题，词面兜细节）。

        **不静默**：裁了要留 warning（静默降级比失败更贵）——但**按批聚合**，
        否则一轮几百块的向量写入会刷出几百行一样的告警（告警一多就等于没有）。
        """
        tokenizer = self._tokenizer()
        if tokenizer is None:
            # 拿不到分词器就**不裁**（不能盲裁），但要说出来——不能静默。
            logger.warning("拿不到嵌入模型的 tokenizer，向量输入未按窗口检查")
            return text
        trimmed, dropped = fit_to_window(text, tokenizer)
        if dropped:
            self._dropped_tokens += dropped
            self._dropped_count += 1
        return trimmed

    def _tokenizer(self):  # type: ignore[no-untyped-def]
        """fastembed 的分词器（`TextEmbedding.model.tokenizer`）。

        拿不到就返回 `None`——调用方据此**不裁**但留告警（不能盲裁，也不能静默）。
        """
        model = self._ensure()
        return getattr(getattr(model, "model", None), "tokenizer", None)


def fit_to_window(text: str, tokenizer, *, limit: int = EMBED_INPUT_MAX_TOKENS) -> tuple[str, int]:
    """把文本裁到 `limit` 个 token 以内（**按 token 边界**，不切碎词）。

    返回 `(裁后文本, 被裁掉的 token 数)`；没裁时是 `(原文本, 0)`。

    为什么用**二分**而不是"取前 N 个 token 再 decode"：前者要假设特殊 token 的个数
    （`[CLS]`/`[SEP]`），而不同模型不一样；二分是**实测**裁完还剩多少 token，
    不靠假设。
    """
    ids = tokenizer.encode(text).ids
    if len(ids) <= limit:
        return text, 0
    low, high = 1, len(ids) - 1
    best = 1
    while low <= high:
        middle = (low + high) // 2
        candidate = tokenizer.decode(ids[:middle])
        if len(tokenizer.encode(candidate).ids) <= limit:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return tokenizer.decode(ids[:best]), len(ids) - best

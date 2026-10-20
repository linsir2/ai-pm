"""向量适配（检索阶段 2）：**模型只在这一处**，换模型/换库只改这里 + `container.py` 一行。

`fastembed` 用 ONNX（无 torch），`BAAI/bge-small-zh-v1.5` = 512 维 / MIT / ~90MB，
**懒加载**：不碰检索的进程（绝大多数测试与 CLI）永远不下载模型、不载 onnxruntime。
"""

from collections.abc import Sequence
from typing import Protocol

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DIMENSIONS = 512

#: **向量口径**（模型 + 喂进去的文本形态）——变了旧向量就是脏的。
#:
#: 口径串的组成：`<模型名>/<文本形态>`。文本形态 `natural` = 标题+章节链+正文
#: **原样不分词**（2026-10-19 从 `tokens` 改过来：分词串把自然语言退化成词袋，
#: bge 的语义优势被扔掉一半，实测改述类 recall@5 85% → 95%）。
#:
#: 这个串存在库的 `vector_index_meta` 表里；启动时**对不上就清表重算**
#: （见 `database._create_vector_table`）——否则新旧两种口径的向量会混在一张表里，
#: 检索结果不可复现。
EMBED_PROFILE = f"{MODEL_NAME}/natural"


class Embedder(Protocol):
    dimensions: int

    def embed_many(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str = MODEL_NAME, *, lazy_load: bool = True) -> None:
        self.dimensions = DIMENSIONS
        self._model = None
        self._model_name, self._lazy = model_name, lazy_load
        if not self._lazy:
            self._ensure()

    def _ensure(self) -> None:
        if self._model is None:
            # 延迟 import：不碰检索就不付 onnxruntime 的启动成本
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed_many(self, texts):
        model = self._ensure()
        return [tuple(float(x) for x in vector) for vector in model.embed(list(texts))]

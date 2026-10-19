"""向量适配（检索阶段 2）：**模型只在这一处**，换模型/换库只改这里 + `container.py` 一行。

`fastembed` 用 ONNX（无 torch），`BAAI/bge-small-zh-v1.5` = 512 维 / MIT / ~90MB，
**懒加载**：不碰检索的进程（绝大多数测试与 CLI）永远不下载模型、不载 onnxruntime。
"""

from collections.abc import Sequence
from typing import Protocol

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DIMENSIONS = 512


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

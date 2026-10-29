"""启动后的**材料维护**：重切过时块 + 补算 profile 变更后清空的向量（批 7，2026-10-21）。

## 为什么要有

块宽从 500 降到 400（为了让向量看得见块尾部，见 `embedding.py` 的 token 闸门）之后，
**已入库的上传/抓网页材料**的块结构过时了：它们的块是 500 宽切的，向量输入仍然超窗、
尾部仍然看不见。原始字节在库（`materials.content`）→ **重切不需要网络、不需要用户重传**。

## 三条纪律

① **不跟 web 生命周期绑**：由独立的一次性 job（`scripts/maintenance_worker.py`，直连 PG）
   触发；backend 启动时**不再**跑维护；
② **幂等**：重切后解析器档位对上 → 不再需要重切（再跑一次不重做）；
③ **已被编号引用的材料一个字都不动**——编号指着它里面的块，改块 = 改指编号（`P19` 冻结）。

## 为什么住在 `bootstrap/`

它要**按材料逐个设 owner 作用域**（`infrastructure.postgres.owner_scope`）——
应用层不许 import 基础设施（架构守卫），而后台任务没有请求上下文、没有身份。
维护任务天生跨账号（每个 owner 的材料都要按新块宽重切），所以它读材料时
**不带 owner 过滤**（与 `optional_owner` 的"查询本身就要包含全局行"同一性质，
templates 是先例）——只读，写仍然按 owner 盖章。
"""

import asyncio
import logging

from pmstudio.application.material_refs import ORIGIN_WEB_RESEARCH
from pmstudio.domain.shared.ids import UserId

logger = logging.getLogger(__name__)

#: 联网材料的来源标记——**它们没有整页字节**（`content` blob 只是块文本拼接），
#: 重解析不了，只能靠"再次搜到同一 URL 时就地升级"（见 `SubmitOpening._keep`）。
WEB_ORIGIN = "web_search"


class MaterialMaintenance:
    """把块结构已过时的材料按当前解析器重切、重算向量。"""

    def __init__(
        self,
        *,
        connection,
        parser,
        store,
        vectors,
        owner_scope,
        index=None,
    ) -> None:
        self._connection = connection
        self._parser = parser
        self._store = store
        self._vectors = vectors
        self._owner_scope = owner_scope
        self._index = index
        self._task: asyncio.Task | None = None

    # ── 生命周期（`api.app.BackgroundResource` 的形状）────────────────────────

    async def start(self) -> None:
        """起后台任务跑一遍（**不阻塞启动**）。"""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            await self.run_once()
        except asyncio.CancelledError:  # pragma: no cover —— 关停时正常
            raise
        except Exception as error:  # noqa: BLE001 —— 维护失败不许影响启动
            logger.warning("材料维护任务失败（已忽略）: %s", error)

    # ── 干活 ──────────────────────────────────────────────────────────────

    async def run_once(self) -> int:
        """跑一遍，返回**重切了几份**（可单独调用，便于测试与手动触发）。"""
        current = self._parser.identities()
        referenced_by_project: dict[str, set[str]] = {}
        refreshed = 0
        for material_id, owner_id, project_id, origin in self._store.list_materials_for_reparse():
            if origin in (WEB_ORIGIN, ORIGIN_WEB_RESEARCH):
                # 联网材料拿不回整页字节 → 重解析不了（保持旧样，由"再次搜到"时升级）；
                # 研究来源是**研究那一刻的快照**（`DeleteMaterial` 那边一律冻结），
                # 解析器升档也不能在这里悄悄改掉它。
                continue
            packet = self._store.get(material_id, owner=owner_id)
            if packet is None:
                continue
            identity = (packet.parsed.parser_id, packet.parsed.parser_version)
            if identity in current:
                continue  # 已是当前档位 → 不重切（幂等）
            if project_id not in referenced_by_project:
                referenced_by_project[project_id] = set(
                    self._index.material_references(project_id) if self._index else ()
                )
            if material_id in referenced_by_project[project_id]:
                continue  # 已被编号引用 → 不动（编号指着它里面的块）
            try:
                if self._reparse(material_id, owner_id, packet):
                    refreshed += 1
            except Exception as error:  # noqa: BLE001 —— 一份坏了不连累别的
                logger.warning("材料 %s 重解析失败（跳过）: %s", material_id, error)
        reembedded = self._vectors.reembed_missing()
        if refreshed:
            logger.info("材料维护：重切了 %d 份", refreshed)
        if reembedded:
            logger.info("材料维护：重嵌入了 %d 份", reembedded)
        return refreshed

    def _reparse(self, material_id: str, owner_id: str, packet) -> bool:
        raw = self._store.raw_bytes(material_id, owner=owner_id)
        if raw is None:
            return False
        parsed = self._parser.parse(
            filename=packet.parsed.title,
            media_type=packet.parsed.media_type,
            data=raw,
        )
        token = self._owner_scope.set_owner_scope(UserId(owner_id))
        try:
            # `replace_chunks` 只换块与解析器身份（id / 去重键 / 原始字节 / 标题都不动）
            self._store.replace_chunks(material_id, parsed)
        finally:
            self._owner_scope.clear_owner_scope(token)
        # 旧向量对应旧块 → 作废后重算（预算规则与写入同一套）
        self._vectors.drop(material_id)
        self._vectors.write(material_id)
        return True

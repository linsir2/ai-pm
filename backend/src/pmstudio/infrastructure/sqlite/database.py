"""SQLite 连接：建库、建表、WAL。单用户单进程，一个连接就够。"""

import json
import logging
import sqlite3
from pathlib import Path

from pmstudio.infrastructure.sqlite.embedding import EMBED_PROFILE
from pmstudio.infrastructure.sqlite.tokenize import index_source, index_text

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: 向量口径在 `vector_index_meta` 里的键名。
PROFILE_KEY = "embed_profile"
#: 11 = 删 observations.sections/features/create
#:     at（死字段销账）。
#: 12 = 模板库：`templates` 表（CREATE TABLE IF NOT EXISTS 自建）+
#:     `documents.template` 列（老库 ALTER 补）。
#: 13 = 多用户改造：13 张表补 `owner_id` + 存量回填给首 admin + NULL 熔断。
#: 14 = L2 观察回填 `created_at`（v11 当死字段删过，这里再加回来并让它当排序依据）。
USER_VERSION = 15

#: v13 起带 `owner_id` 的表：12 张项目域表 + `templates`（裁决 A）。
#: `templates` 的 builtin 行 owner 留 NULL（出厂模板人人可见）——回填与熔断都跳过它们。
#: `users` / `sessions` / `model_calls` 不加列（身份本身不带归属、留样 join 查）。
OWNER_TABLES: tuple[str, ...] = (
    "documents",
    "document_versions",
    "rounds",
    "evidence_ledgers",
    "materials",
    "material_chunks",
    "explorations",
    "discussions",
    "project_memory",
    "observations",
    "round_digests",
    "inspirations",
    "templates",
)

#: `CREATE TABLE IF NOT EXISTS` 补不了**已有**表的列，所以老库要靠这里的 `ALTER TABLE`（幂等）。
#: 留样表本身是可丢的（`02` §5.3：要清就 DELETE），但**不主动删数据**。
ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "documents": (
        ("template", "TEXT NOT NULL DEFAULT '{}'"),
        ("owner_id", "TEXT"),
    ),
    "document_versions": (("owner_id", "TEXT"),),
    "rounds": (("owner_id", "TEXT"),),
    "evidence_ledgers": (("owner_id", "TEXT"),),
    "materials": (("owner_id", "TEXT"),),
    "material_chunks": (("owner_id", "TEXT"),),
    "explorations": (("owner_id", "TEXT"),),
    "discussions": (("owner_id", "TEXT"),),
    "project_memory": (("owner_id", "TEXT"),),
    "observations": (
        ("owner_id", "TEXT"),
        # v11 把这一列当死字段删了；v14 再加回来——`recent()` 的"最新"判据改成它。
        # 老行补的是空串，迁移**不猜**时间：空着的那批接着由下面 `migrate()` 里
        # "按轮次回填"那条收拾（补它自己那一轮的真实时刻），回填不到的才留 `''`，
        # 仓储把空时间排最后，不当它是最新。
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
    ),
    "round_digests": (("owner_id", "TEXT"),),
    "inspirations": (("owner_id", "TEXT"),),
    "templates": (("owner_id", "TEXT"),),
    "model_calls": (
        ("attempt", "INTEGER NOT NULL DEFAULT 1"),
        ("ok", "INTEGER NOT NULL DEFAULT 1"),
        ("error", "TEXT"),
    ),
    # 90 天绝对上限的锚点，是加会话表**之后**才补的列（`schema.sql` 的
    # `CREATE TABLE IF NOT EXISTS` 补不了已有表）。跑过早期版本的库缺它 → 登录第一条
    # INSERT 就炸 → 登录必 500。老行在下面 `migrate()` 里回填，不留 NULL。
    "sessions": (("created_at", "TEXT"),),
}


#: **WAL-reset bug**（`sqlite.org/wal.html §11`）：一个从 3.7.0（2010）就在的数据竞争
#: ——一个连接在 checkpoint、另一个连接重置了 WAL 并写入，后续 checkpoint 会**跳过
#: 一批已提交事务 → 数据库损坏**。
#:
#: 触发条件三条全中才会发生：① WAL 模式 ② 同一文件 ≥2 个**跨线程/跨进程**连接
#: ③ 两者同时写或 checkpoint。**② 正是多 worker 的定义**——`container.py` 每个
#: worker 一条连接。
#:
#: **修复在 3.51.3（2026-03-13）**，另有两条分支的 backport：**3.44.6 / 3.50.7**。
#: 判定**必须按分支走**，不能用版本号大小比较：`min((3,50,7))` 会把 3.51.1 误判成
#: 已修（3.51.1 > 3.50.7），而 3.51 分支要到 3.51.3 才修。
#: 已修复的版本**区间**（闭区间）。backport 只落在 3.50.7 与 3.44.6 两个版本上，
#: **不是**从那个版本起一直到 3.51 之前——3.45 ~ 3.49 与 3.43 及更早的分支都没有
#: backport，全都仍然中招。
#:
#: 3.51.3 起是主修复版，之后的版本（含 3.52 / 3.53）都带修复。
_WAL_RESET_FIXED_RANGES = (
    ((3, 51, 3), None),      # None = 没有上界
    ((3, 50, 7), (3, 50, 99)),
    ((3, 44, 6), (3, 44, 99)),
)


def wal_reset_vulnerable(version: str) -> bool:
    """链接的 SQLite 是否落在 WAL-reset 的受影响区间内。

    **解析不了就当中招**（返回 True）——把未知当成安全是"看起来正常"的静默失效。
    """

    try:
        parts = tuple(int(part) for part in version.strip().split("."))
        if len(parts) < 3:
            return True
    except (AttributeError, ValueError):
        return True
    current = parts[:3]
    # **按分支走**：每条分支有自己的修复点。写成"对三个修复点逐个比大小"会跨分支
    # 误判——`(3,51,1) >= (3,50,7)` 成立，于是把 3.51.1 判成已修，而 3.51 分支
    # 要到 3.51.3 才修。先确定落在哪条分支，再比那一条的修复点。
    # **按区间比，不能只比大小**。两种错法都踩过：
    #   · 「对三个修复点逐个比大小」→ `(3,51,1) >= (3,50,7)` 成立，
    #     把 3.51.1 判成已修，而 3.51 分支要到 3.51.3 才修；
    #   · 「3.44.6 是分界」→ 把 3.45~3.49 全吞成已修，可那些分支**没有** backport。
    for low, high in _WAL_RESET_FIXED_RANGES:
        if current >= low and (high is None or current <= high):
            return False
    return current >= (3, 7, 0)


#: 已就 WAL 模式发过一次警告之后置位。**按状态转换打、不按发生次数打**
#: ——形状照抄 `redis_bus._mark_leg_down`（每条连接都警告会把日志刷爆：
#: 4 个 worker 各自建连接，逐连接警告就是刷屏）。
_wal_reset_warned = False


def _warn_wal_reset_risk(journal_mode: str = "wal") -> None:
    """链接的 SQLite 有 WAL-reset 风险 → 打一条**把四件事说清**的警告。

    仓库已有一条明写的规矩（`assert_owners_backfilled` 的文档串）：错误文案
    **必须点名要配的变量 / 要改的东西**。这里同理，四件事：
      ① 现在是什么版本、受影响到哪个版本为止
      ② 触发条件（本仓多 worker 正好满足②）
      ③ 官方严重度——发生率 ≤ SSD 故障率，**不是紧急事件**，不夸大
      ④ **真修只能换一个捆绑了新 SQLite 的解释器**——本仓改不了，
         而且 `pysqlite3-binary` 捆绑的 3.51.1 同样中招（本机实测）
    """

    global _wal_reset_warned
    # **先验"真的在 WAL 模式"**：触发条件之一就是 WAL，没开就不该喊。
    # 这一步不是多余的假设——`PRAGMA journal_mode` 可能抢不到锁而没切成。
    if str(journal_mode).lower() != "wal":
        return
    version = sqlite3.sqlite_version
    if not wal_reset_vulnerable(version):
        # 版本已经修好 → 清标志。**恢复要清**是 redis_bus 立下的规矩：
        # 不清的话进程跑几天之后那次偶发中招就再也看不见了。
        _wal_reset_warned = False
        return
    if _wal_reset_warned:
        return
    _wal_reset_warned = True
    logger.warning(
        "链接的 SQLite %s 落在 WAL-reset bug 的受影响区间内（3.7.0 – 3.51.2，"
        "修复在 3.51.3；backport 只有 3.44.6 与 3.50.7 两条分支）。"
        "触发条件三条：WAL 模式 + 同一库上 ≥2 个**跨进程**连接 + 两者同时写或 checkpoint"
        "——多 worker 正好满足第二条。官方严重度：发生率 ≤ SSD 故障率，**不是紧急事件**。"
        "本仓改不了它（Python 的 sqlite3 链接的是解释器自带的 libsqlite3，"
        "pyproject.toml 加依赖无效；pysqlite3-binary 捆绑的 3.51.1 同样中招）。"
        "**真修只能换一个捆绑了 ≥3.51.3 的 SQLite 的解释器**（生产镜像里 pin 住）。",
        version,
    )


def _enable_wal(connection: sqlite3.Connection) -> None:
    """切到 WAL。**多进程下这一步本身会争用**（高并发 P4 真跑抓到的）。

    `PRAGMA journal_mode = WAL` 要短暂持排他锁，而它**不吃 `timeout=10` 的
    busy 等待**——两个 worker 同时启动、都在一个新库上执行，直接
    `database is locked`，**两个都起不来**。

    判据很直接：**目标达成就行**。切完之后再读一次，如果已经是 `wal` 就没事；
    真的切不过去（别的原因）才抛。
    """
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        mode = "wal"
    except sqlite3.OperationalError:
        current = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(current).lower() != "wal":
            raise
        mode = str(current).lower()
        # 已经在 WAL 模式（只是这次 pragma 抢不到锁）——按它现在的模式判
    _warn_wal_reset_risk(mode)


def connect(db_path: Path, *, embedder=None) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 显式 `timeout=10.0`（多用户改造 §0.9）：Python 默认 5.0 是隐式的，写出来是为了
    # "并发写等锁最多等多久"这件事有个可见的数；单 worker + 短事务是前提，见 `scripts/dev.py`。
    connection = sqlite3.connect(
        db_path, isolation_level=None, check_same_thread=False, timeout=10.0
    )
    connection.row_factory = sqlite3.Row
    _enable_wal(connection)
    connection.execute("PRAGMA foreign_keys = ON")
    # **先补列、再建 schema**（多用户改造 Task 5）：`schema.sql` 里有
    # `CREATE INDEX ... ON <表>(owner_id)`，老库那一刻还没这列——`CREATE TABLE IF NOT
    # EXISTS` 补不了已有表的列，只有 `migrate()` 的 `ALTER TABLE` 能。放在 schema 之后
    # 建索引就会在老库上炸「no such column: owner_id」。新库两边都幂等，不受影响。
    #
    # 向量扩展在这里（`migrate` 之前）加载，随后**由代码建 vec0 表**：建表必须在
    # `migrate()` 的向量回填之前，否则老库首次升级补不上（要等第二次启动）。
    # 载不了就记一行 warning，检索侧退回纯 FTS5（见 `_create_vector_table`）。
    load_sqlite_vec(connection)
    _create_vector_table(connection)
    migrate(connection, embedder=embedder)
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.execute(f"PRAGMA user_version = {USER_VERSION}")
    return connection


def _create_vector_table(connection: sqlite3.Connection) -> bool:
    """载入扩展后建 `material_chunks_vec`（vec0 虚拟表）。**建不成就返回 False。**

    为什么不由 `schema.sql` 建：那张表需要 `sqlite-vec` 扩展先在连接里注册模块，
    而 `executescript` 不带条件——模块不在时整份 schema 执行直接炸，后端起不来。
    这里独立出来，扩展缺失就跳过这一张表，其余形状照常迁移（降级到纯 FTS5）。

    **口径对不上就清表重算**（2026-10-19）：向量口径 = 模型 + 喂进去的文本形态，
    记在 `vector_index_meta` 表里。口径变了（换模型、或把分词串改成自然语言），
    **旧向量就是脏的**——留着一半旧口径一半新口径的表，检索结果不可复现。
    这里 DROP 掉表，随后 `migrate()` 的幂等回填会把全部块重算一遍
    （ONNX ~20ms/块，单用户几百块 = 几秒，一次性）。
    """

    if not load_sqlite_vec(connection):
        return False
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS vector_index_meta ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = connection.execute(
            "SELECT value FROM vector_index_meta WHERE key = ?", (PROFILE_KEY,)
        ).fetchone()
        stored = str(row["value"]) if row is not None else None
        if stored is not None and stored != EMBED_PROFILE:
            logger.warning("向量口径变了（%s → %s），清空向量表重算", stored, EMBED_PROFILE)
            connection.execute("DROP TABLE IF EXISTS material_chunks_vec")
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS material_chunks_vec USING vec0("
            " chunk_id TEXT PRIMARY KEY,"
            " embedding FLOAT[512])"
        )
        connection.execute(
            "INSERT INTO vector_index_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PROFILE_KEY, EMBED_PROFILE),
        )
    except Exception as error:  # noqa: BLE001 —— 建表失败同样降级，不炸启动
        logger.warning("向量索引表建不出来，检索退回纯 FTS5：%s", error)
        return False
    return True


def load_sqlite_vec(connection: sqlite3.Connection) -> bool:
    """把 `sqlite-vec` 载进这条连接。**载不了就返回 False**——调用方据此降级，
    绝不因此炸 `connect()`（"缺配置说缺，不是假装有"）。"""

    try:
        import sqlite_vec
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        return True
    except Exception as error:  # noqa: BLE001 —— 扩展/平台问题一律降级
        logger.warning("sqlite-vec 载入失败，检索退回纯 FTS5：%s", error)
        return False


#: 老库要删的列（销账死字段）。`ALTER TABLE ... DROP COLUMN` 要求 SQLite ≥ 3.35。
#: `observations.created_at` 不在删名单里——v14 把它捡回来当排序依据（见 `ADDED_COLUMNS`）。
DROPPED_COLUMNS: dict[str, tuple[str, ...]] = {
    "observations": ("relevance_count", "last_accessed", "sections", "features"),
    "inspirations": ("grounding", "context"),
}


def migrate(connection: sqlite3.Connection, *, embedder=None) -> None:
    """把老库补到当前形状：加列、删列、不丢业务数据、可重复跑。

    `embedder` 只给向量回填用：`None` = 只做形状迁移（离线安全；调用方全不传时就是它）；
    传了才补 `material_chunks_vec`。真跑里容器传 `FastEmbedEmbedder`，测试里传假实现。
    """

    for table, columns in ADDED_COLUMNS.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            continue  # 表还没建（schema.sql 会按新形状建），不用补
        for name, definition in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    for table, columns in DROPPED_COLUMNS.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            continue  # 表还没建，无需删
        for name in columns:
            if name in existing:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {name}")

    # 补完列再填值：`sessions.created_at` 是绝对上限的锚点，NULL 会让 `GetMe` 的
    # datetime 减法炸成 500。老行拿 `last_seen_at` 顶——那时能拿到的最接近建会话的时刻。
    # 幂等：只碰 NULL，新会话由 INSERT 自己写。老库可能压根还没建会话表（`schema.sql`
    # 会按新形状建，那时新表自带 NOT NULL 的 created_at），所以先问表在不在。
    if any(
        row["name"] == "sessions"
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    ):
        connection.execute(
            "UPDATE sessions SET created_at = last_seen_at"
            " WHERE created_at IS NULL AND last_seen_at IS NOT NULL"
        )

    # pre-v11 的库 `observations.created_at` 是**可空**列并留着 NULL，v14 又把它捡回来当
    # `recent()` 的排序判据——"无时间"于是有两种存法，而 NULL 那一种在排序里排**最前**
    # （`(NULL = '')` 求值为 NULL，ASC 里 NULL 领先），正好把意图做反。统一填 `''`：
    # 迁移**不猜**这些观察的时间，空串就是诚实的"没有"。幂等：只碰 NULL。
    # v14 给存量行补 `created_at` 时同样是空串，下面那条按轮次回填接着收。
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observations'"
    ).fetchone() is not None:
        connection.execute("UPDATE observations SET created_at = '' WHERE created_at IS NULL")

    # 存量观察回填时间：取**它自己那一轮**的 `rounds.created_at`。这一列一直在记，只是
    # v14 才被捡回来当排序键——空着等于白扔库里已有的信息，而 `recent()` 对存量行也因此
    # 完全无效（全是空串时仍按 `id DESC` 排，`obs-9` 照样压着 `obs-10`）。
    # 补的是**轮次时刻**（那批观察真正产生的时刻），不是迁移时刻：拿 now 填是撒谎。
    # `EXISTS` 兜住"轮次已不在"：子查询会给 NULL，写进 `NOT NULL` 列会炸——那类行留 `''`，
    # 排序本来就把空值沉底，留着无害。幂等：只碰仍是 `''` 的行。
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observations'"
        ).fetchone()
        is not None
        and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rounds'"
        ).fetchone()
        is not None
    ):
        connection.execute(
            "UPDATE observations SET created_at = ("
            " SELECT r.created_at FROM rounds r WHERE r.round_id = observations.round_id)"
            " WHERE created_at = ''"
            " AND EXISTS (SELECT 1 FROM rounds r WHERE r.round_id = observations.round_id)"
        )

    _backfill_chunk_index(connection)
    _backfill_chunk_vectors(connection, embedder=embedder)


def _backfill_chunk_vectors(connection: sqlite3.Connection, *, embedder=None) -> None:
    """给"升级前入库的块"补向量（检索阶段 2）。**幂等**：只补缺的那些。

    与 FTS 回填同一条"补缺"口径（`_backfill_chunk_index`）：`embedder=None` 就整段跳过
    ——形状迁移本身不许依赖模型下载；真跑由调用方显式传，测试传假实现。
    """

    if embedder is None:
        return
    if not load_sqlite_vec(connection):
        return
    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'material_chunks'"
    ).fetchone()
    has_vec = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'material_chunks_vec'"
    ).fetchone()
    if has_table is None or has_vec is None:
        return
    missing = connection.execute(
        "SELECT c.chunk_id, c.material_id, c.text, c.heading_path, c.ordinal,"
        " m.title AS title, m.origin AS origin"
        " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
        " WHERE c.chunk_id NOT IN (SELECT chunk_id FROM material_chunks_vec)"
    ).fetchall()
    if not missing:
        return
    # **与 `SqliteVectorIndex.write` 同一套预算规则**（否则启动回填会把预算外的块
    # 全补上，预算形同虚设）。判据用**绝对 ordinal**，不是"缺的那些里的第几个"——
    # 一份 12 块的联网材料若前 8 块已有向量，缺的是 9–12，它们**不该**被补。
    #
    # 预算的量纲是**字数**（2026-10-21 批 11），所以边界要按材料把**全部块**查出来
    # 算累计字数（`blocks_within_budget`）——只看"缺的那些"算不出边界。
    from pmstudio.infrastructure.sqlite.vector_index import (
        blocks_within_budget,
        vector_char_budget,
    )

    origin_of = {str(row["material_id"]): str(row["origin"]) for row in missing}
    limits: dict[str, int | None] = {}
    for material_id, origin in origin_of.items():
        char_budget = vector_char_budget(origin)
        if char_budget is None:
            limits[material_id] = None
            continue
        all_rows = connection.execute(
            "SELECT ordinal, text FROM material_chunks WHERE material_id = ? ORDER BY ordinal",
            (material_id,),
        ).fetchall()
        limits[material_id] = blocks_within_budget(all_rows, char_budget)
    missing = [
        row
        for row in missing
        if limits[str(row["material_id"])] is None
        or int(row["ordinal"]) < int(limits[str(row["material_id"])])
    ]
    if not missing:
        return
    from sqlite_vec import serialize_float32

    texts = [
        index_text(
            title=row["title"],
            heading_path=json.loads(row["heading_path"]),
            text=row["text"],
        )
        for row in missing
    ]
    try:
        vectors = embedder.embed_many(texts)
    except Exception as error:  # noqa: BLE001 —— 模型下载/推理失败降级，不阻断启动
        logger.warning("向量回填失败，检索退回纯 FTS5：%s", error)
        return
    for row, vector in zip(missing, vectors, strict=True):
        connection.execute(
            "INSERT OR REPLACE INTO material_chunks_vec (chunk_id, embedding)"
            " VALUES (?, ?)",
            (row["chunk_id"], serialize_float32(list(vector))),
        )


def _backfill_chunk_index(connection: sqlite3.Connection) -> None:
    """给"升级前入库的块"补全文索引（检索 P1）。**幂等**：只补缺的那些，重复跑不动。

    为什么要它：`material_chunks_fts` 是随本次升级新增的；老库里的块没有索引条目，
    不补的话那些材料**永远搜不到**。分词在应用层（jieba），所以不能纯 SQL 干——
    逐条选出"没有 FTS 条目"的块，分词后补写。表/索引不存在时（极老的库）什么都不做。
    """

    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'material_chunks_fts'"
    ).fetchone()
    if has_table is None:
        return
    missing = connection.execute(
        "SELECT c.chunk_id, c.material_id, c.text, c.heading_path, m.title AS title"
        " FROM material_chunks c JOIN materials m ON m.material_id = c.material_id"
        " WHERE c.chunk_id NOT IN (SELECT chunk_id FROM material_chunks_fts)"
    ).fetchall()
    for row in missing:
        connection.execute(
            "INSERT INTO material_chunks_fts (chunk_id, material_id, tokens) VALUES (?, ?, ?)",
            (
                row["chunk_id"],
                row["material_id"],
                index_source(
                    title=row["title"],
                    heading_path=json.loads(row["heading_path"]),
                    text=row["text"],
                ),
            ),
        )


def backfill_owners(connection: sqlite3.Connection) -> None:
    """存量行补归属：只填 NULL（幂等），owner = 首个 admin。

    没 admin 就什么都不做——留给 `assert_owner_coverage` 熔断报错，而不是瞎填一个。
    `templates` 的 builtin 行（builtin=1）永不填：出厂模板人人可见。
    """

    admin = connection.execute(
        "SELECT user_id FROM users WHERE role = 'admin' LIMIT 1"
    ).fetchone()
    if admin is None:
        return
    admin_id = admin["user_id"]

    # 存量全归首 admin（§0.6：单用户时代的项目本就是这个人的），逐表直填、只碰 NULL。
    for table in OWNER_TABLES:
        if table == "templates":
            # builtin 行是出厂模板，人人可见——不认领。
            connection.execute(
                "UPDATE templates SET owner_id = ? WHERE owner_id IS NULL AND builtin = 0",
                (admin_id,),
            )
        else:
            connection.execute(
                f"UPDATE {table} SET owner_id = ? WHERE owner_id IS NULL", (admin_id,)
            )


def assert_owners_backfilled(connection: sqlite3.Connection) -> None:
    """任一内容表还有 NULL owner 就拒绝启动：缺归属的内容不该悄悄漏出去。

    由组装点（`build_container`）在"建 users → 种首 admin → `backfill_owners`"之后调用，
    **不在 `connect()` 里**——否则存量库会在 admin 种子落库之前先熔断，永远升不上来。
    `templates` 只查自建行——builtin 行留 NULL 是规矩，不是漏网。

    熔断时**必须给出可执行的下一步**（多用户改造 fix wave）：真正的原因是
    "存量内容需要归属，但库里没有管理员账号可归属"，而 `backfill_owners` 找不到 admin
    就静默不动。曾经这里写的是"先回填再启动"——可本仓根本没有手动回填命令，
    照着做的人只会卡死。现在点名要配的两个变量，配好重启即由组装点自动回填。
    """

    orphans_by_table: dict[str, int] = {}
    for table in OWNER_TABLES:
        where = (
            "owner_id IS NULL AND builtin = 0"
            if table == "templates"
            else "owner_id IS NULL"
        )
        orphans = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}"
        ).fetchone()[0]
        if orphans:
            orphans_by_table[table] = orphans
    if not orphans_by_table:
        return

    detail = "、".join(f"{table} {n} 行" for table, n in orphans_by_table.items())
    has_admin = (
        connection.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        is not None
    )
    if not has_admin:
        raise RuntimeError(
            f"存量内容没有归属（{detail} NULL owner），而库里还没有管理员账号可归属。"
            "请在 backend/.env 里配 PMSTUDIO_FIRST_ADMIN_USER 与 "
            "PMSTUDIO_FIRST_ADMIN_PASSWORD，重启后会自动把这些存量归到该管理员名下。"
        )
    raise RuntimeError(f"{detail} NULL owner，先回填再启动")

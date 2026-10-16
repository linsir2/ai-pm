"""并发写冒烟（多用户改造 Task 9 · `§0.9`）：证明 `connect(timeout=10.0)` 的 busy 等待生效。

**为什么每线程各开一条连接**：生产是"单 worker、全 async"（`scripts/dev.py`），并发请求
在 SQLite 层表现为**多条写序列抢同一把库锁**（各自连接/事务）。用一个共享
`sqlite3.Connection` 从多线程并发写**不合法**——`check_same_thread=False` 只是解除
"跨线程"拦截，不提供线程安全，也测不出真实争用。所以这里每线程一条连接，那才等价于
"多个请求各自拿连接写"。断言两条：十个并发写全成功、且**一个 `database is locked` 都不抛**。
"""

import threading
import time
from datetime import UTC, datetime

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.shared.ids import DocumentId, ProjectId, UserId
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.repositories import SqliteDocumentRepository
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

THREADS = 10


def _write_one(connection, index: int, barrier: threading.Barrier, errors: list) -> None:
    """一个线程 = 一次"写不同项目"的请求：等齐后抢锁写自己那条。"""

    owner = UserId(f"u-conc-{index}")
    try:
        documents = SqliteDocumentRepository(connection)
        barrier.wait(timeout=10)  # 十个尽量同时抢写锁，制造真实争用
        with SqliteUnitOfWork(connection):
            documents.save(
                Document.new(
                    document_id=DocumentId(f"doc-conc-{index}"),
                    project_id=ProjectId(f"p-conc-{index}"),
                    title=f"并发项目 {index}",
                    now=datetime.now(UTC),
                ),
                owner=owner,
            )
    except BaseException as exc:  # noqa: BLE001 —— 收集到主线程断言，别让线程静默吞异常
        errors.append(exc)


def _new_document(index: int) -> Document:
    return Document.new(
        document_id=DocumentId(f"doc-{index}"),
        project_id=ProjectId(f"p-{index}"),
        title=f"项目 {index}",
        now=datetime.now(UTC),
    )


def test_ten_concurrent_writes_to_different_projects_all_succeed(tmp_path):
    """十线程并发写十个不同项目：全部成功落库，无 `database is locked` 抛出。"""

    db_path = tmp_path / "conc.db"
    database.connect(db_path).close()  # 主线程先建库（schema/migrate），避免并发首建竞态

    connections = [database.connect(db_path) for _ in range(THREADS)]
    barrier = threading.Barrier(THREADS)
    errors: list[BaseException] = []
    threads = [
        threading.Thread(
            target=_write_one,
            args=(connections[index], index, barrier, errors),
            name=f"writer-{index}",
        )
        for index in range(THREADS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for connection in connections:
        connection.close()

    assert errors == [], f"并发写抛异常（busy 未等待）：{errors!r}"

    check = database.connect(db_path)
    try:
        rows = check.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        owners = {
            row[0] for row in check.execute("SELECT owner_id FROM documents").fetchall()
        }
    finally:
        check.close()
    assert rows == THREADS
    assert owners == {f"u-conc-{index}" for index in range(THREADS)}


def test_write_waits_for_lock_then_succeeds(tmp_path):
    """另一连接握写锁 1s：本连接的写**等**它（而非立即抛 `database is locked`）后成功。

    直接钉住"等锁"语义：`timeout=10` 会落成 `busy_timeout=10000`；若为 0，
    被占锁时立刻抛错，`elapsed` 会趋近 0——这里要求实测等待 ≥ 0.8s。

    （`holder` 的 `BEGIN IMMEDIATE` 与释放线程的 `COMMIT` 跨线程用同一连接：**合法**是
    因为 `database.connect` 固定 `check_same_thread=False`，不是靠"两操作时序不重叠"。）
    """

    db_path = tmp_path / "lock.db"
    holder = database.connect(db_path)
    waiter = database.connect(db_path)
    try:
        assert waiter.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        holder.execute("BEGIN IMMEDIATE")  # 先占住写锁

        holder_done = threading.Event()

        def _release() -> None:
            time.sleep(1.0)
            holder.execute("COMMIT")
            holder_done.set()

        releaser = threading.Thread(target=_release, name="lock-holder")
        releaser.start()

        start = time.perf_counter()
        try:
            with SqliteUnitOfWork(waiter):
                SqliteDocumentRepository(waiter).save(
                    _new_document(99), owner=UserId("u-waiter")
                )
        finally:
            releaser.join()
        elapsed = time.perf_counter() - start

        assert holder_done.is_set(), "主握锁线程没跑完"
        assert elapsed >= 0.8, f"没等锁（{elapsed:.2f}s），说明 busy_timeout 没生效"
    finally:
        holder.close()
        waiter.close()

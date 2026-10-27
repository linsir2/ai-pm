"""并发写冒烟：十个线程各持一条 PostgreSQL 连接写不同项目，全部成功。

生产每个 worker 一条连接（PgBouncer transaction mode），并发写不再靠 SQLite 的
库锁排队；这里钉的是"多连接同时写不同行不会互相失败"，以及数据确实各归各的 owner。
"""

import os
import threading
from datetime import UTC, datetime

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.shared.ids import DocumentId, ProjectId, UserId
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.repositories import SqliteDocumentRepository
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork

THREADS = 10


def _write_one(connection, index: int, barrier: threading.Barrier, errors: list) -> None:
    owner = UserId(f"u-conc-{index}")
    try:
        documents = SqliteDocumentRepository(connection)
        barrier.wait(timeout=10)
        with PgUnitOfWork(connection):
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


def test_ten_concurrent_writes_to_different_projects_all_succeed():
    url = os.environ["PMSTUDIO_DIRECT_DATABASE_URL"]
    connections = [connect(url) for _ in range(THREADS)]
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

    assert errors == [], f"并发写抛异常：{errors!r}"

    check = connect(url)
    try:
        row = check.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        assert row is not None and row["n"] == THREADS
        owners = {
            row["owner_id"]
            for row in check.execute("SELECT owner_id FROM documents").fetchall()
        }
    finally:
        check.close()
    assert owners == {f"u-conc-{index}" for index in range(THREADS)}

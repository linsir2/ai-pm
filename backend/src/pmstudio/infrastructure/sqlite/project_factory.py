"""建项目：空骨架 + 空账本，一个事务。**只能造空文档**（改文档仍只有 committer 一条路）。"""

import sqlite3
from datetime import datetime

from pmstudio.application.ports import IdGenerator
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.ids import DocumentId, ProjectId
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


class SqliteProjectFactory:
    def __init__(self, connection: sqlite3.Connection, *, ids: IdGenerator) -> None:
        self._connection = connection
        self._ids = ids

    def create(self, *, project_id: ProjectId, title: str, now: datetime) -> DocumentId:
        document_id = DocumentId(self._ids.new("doc"))
        documents = SqliteDocumentRepository(self._connection)
        evidences = SqliteEvidenceRepository(self._connection)
        with SqliteUnitOfWork(self._connection):
            documents.save(
                Document.new(
                    document_id=document_id, project_id=project_id, title=title, now=now
                )
            )
            evidences.save(EvidenceLedger.empty(project_id))
        return document_id

"""真实scripts/eval.run_one→runner→PG/DTO→check，只有外部模型回包预置。"""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from scripts.eval import run_one

from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.shared.ids import EntryId, ProjectId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.application.test_node4_eval import without_card_scope_warning
from tests.support.builders import NOW, SEAT
from tests.support.postgres import open_direct


@pytest.mark.parametrize("mode,expected", [
    ("mechanical_only", True), ("scope_and_mechanical", True),
    ("missing_card_scope", False), ("missing_top_scope", False),
])
async def test_eval_script_consumes_real_pg_warning_views(make_app, monkeypatch, capfd,
                                                        mode, expected):
    scope = "s1" if mode == "mechanical_only" else "s5"
    claims = {"basis": "读了基线正文", "claims": [{
        "text": "用户修订正文", "raw_span": "用户修订正文", "label": "need",
        "zone": "body", "section_id": scope,
    }], "seats": [{"seat_id": SEAT, "why": "核验正文"}]}
    packet = {"questions": [], "list_items": [], "proposals": [{
        "op": "replace", "entry_id": "old#01", "target": {"zone": "body", "section_id": "s1"},
        "text": "新正文", "origin_type": "user", "evidence_refs": ["E1", "E2"],
    }]}
    app, gateway = make_app(
        replies={"claims": json.dumps(claims, ensure_ascii=False),
                 "integration": json.dumps(packet, ensure_ascii=False)},
        seat_replies={SEAT: json.dumps({"basis": "核验正文", "opinions": []})},
    )
    services = app.state.services
    create_project = services.create_project
    project_id = None

    async def create_seeded_project(*, title):
        nonlocal project_id
        project = await create_project(title=title)
        project_id = ProjectId(project.project_id)
        with open_direct() as connection, PgUnitOfWork(connection):
            evidence = SqliteEvidenceRepository(connection)
            ledger = evidence.get_for_update(project_id)
            old_eid = ledger.record(kind=EvidenceKind.MANUAL, text="旧正文",
                                source=EvidenceSource(), now=NOW)
            evidence.save(ledger)
            documents = SqliteDocumentRepository(connection)
            document = documents.get(project_id)
            document.apply((WriteInstruction("seed", EntryId("old#01"), WriteOp.ADD,
                                            Target.body("s1"), "旧正文", (old_eid,)),),
                           now=NOW, summary="测试基线")
            documents.save(document)
        return project

    complete = gateway.complete

    async def change_during_integration(request):
        if request.purpose == "integration":
            # 改真正PG当前头，模型提案仍签实际输入v2；不直接捏造stale warning。
            assert project_id is not None
            with open_direct() as connection, PgUnitOfWork(connection):
                documents = SqliteDocumentRepository(connection)
                document = documents.get(project_id)
                old = document.locate_entry(EntryId("old#01")).entry
                document.apply((WriteInstruction("concurrent", old.entry_id, WriteOp.REPLACE,
                                                Target.body("s1"), "期间已变", old.refs),),
                               now=NOW, summary="模拟生成期间改文")
                documents.save(document)
        return await complete(request)

    monkeypatch.setattr(gateway, "complete", change_during_integration)
    runner = services.runner
    projected = []

    async def transport(command):
        view = await runner.run(command)
        if view.draft is not None:
            projected.append(view)
            if mode == "missing_card_scope":
                view = without_card_scope_warning(view)
            elif mode == "missing_top_scope":
                view = replace(view, advisories=())
        return view

    services = replace(services, create_project=create_seeded_project,
                       runner=SimpleNamespace(run=transport))
    ok, crash = await run_one(services, {"name": mode, "note": "warning传播验证",
                                         "input": "用户修订正文"})
    assert crash == ""
    assert len(projected) == 1
    view = projected[0]
    card = view.draft.proposals[0]
    assert card.base_version_no == 2
    assert card.before.text == "旧正文"
    assert card.requires_review and "proposal_stale" in card.warning
    if mode == "mechanical_only":
        assert view.advisories == () and "不在本轮去处里" not in card.warning
    else:
        assert "不在本轮去处里" in card.warning and len(view.advisories) == 1
    assert ok is expected
    output = capfd.readouterr().out
    assert f"[{'PASS' if expected else 'FAIL'}] outside_scope_items_are_labeled" in output
    with open_direct() as connection:
        document = SqliteDocumentRepository(connection).get(project_id)
        assert document.current_version.version_no == 3
        assert document.locate_entry(EntryId("old#01")).entry.text == "期间已变"

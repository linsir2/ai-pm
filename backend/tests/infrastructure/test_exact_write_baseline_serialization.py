import pytest

from pmstudio.application import assembly
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import DraftId
from pmstudio.infrastructure.postgres.repositories import SqliteRoundRepository
from pmstudio.infrastructure.postgres.serialization import proposal_from_json, proposal_to_json
from tests.domain.test_exact_write_locations import proposal
from tests.support.builders import ROUND_ID, make_round
from tests.support.postgres import open_direct


def test_new_baseline_and_legacy_none_survive_real_round_repository(pg_test_database):
    fresh = proposal()
    payload = proposal_to_json(fresh)
    assert payload["base_version_no"] is None
    payload["base_version_no"] = 2
    stamped = proposal_from_json(payload)
    assert stamped.base_version_no == 2
    del payload["base_version_no"]
    legacy = proposal_from_json(payload)
    assert legacy.base_version_no is None
    round_ = make_round()
    round_.integrate(draft=assembly.draft_of(ROUND_ID, DraftId("d1"), (stamped,), ()))
    with open_direct() as connection:
        row = connection.execute("SELECT current_schema() AS s").fetchone()
        assert row["s"] == pg_test_database.schema
        repository = SqliteRoundRepository(connection)
        repository.save(round_)
        restored = repository.get(ROUND_ID)
        assert restored.draft.proposals[0].base_version_no == 2
        assert proposal_to_json(restored.draft.proposals[0])["base_version_no"] == 2
        connection.execute(
            "UPDATE rounds SET payload = payload #- '{draft,proposals,0,base_version_no}' "
            "WHERE round_id = %s", (str(ROUND_ID),),
        )
        assert repository.get(ROUND_ID).draft.proposals[0].base_version_no is None


@pytest.mark.parametrize("base", [0, -2, True, "3", 2.5])
def test_serialized_baseline_is_validated_without_coercion(base):
    payload = proposal_to_json(proposal())
    payload["base_version_no"] = base
    with pytest.raises(ShapeError):
        proposal_from_json(payload)

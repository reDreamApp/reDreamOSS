"""The ORM's CHECK constraints (mirrored in migrations/0001_initial.sql) hold
under SQLite too, and the stage list follows the AI side's wire contract."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from redream_ai.models import Stage
from redream_api.db.models._types import STAGES
from redream_api.db.models.chat import Chat
from redream_api.db.models.dream import Dream


def test_the_stage_checks_allow_exactly_the_wire_stages():
    assert STAGES == tuple(stage.value for stage in Stage)


@pytest.mark.parametrize(
    "row",
    [
        lambda user_id: Chat(user_id=user_id, language="fr"),
        lambda user_id: Chat(user_id=user_id, current_stage="done"),
        lambda user_id: Dream(user_id=user_id, title="t", original_dream="d", source="import"),
        lambda user_id: Dream(user_id=user_id, title="t", original_dream="d", language="fr"),
        lambda user_id: Dream(user_id=user_id, title="t", original_dream="d", intensity=11),
    ],
)
async def test_a_value_outside_the_checks_is_refused(db, fake_user, row):
    db.add(row(fake_user.id))
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        await db.commit()


async def test_a_manual_dream_without_language_passes_the_checks(db, fake_user):
    db.add(Dream(user_id=fake_user.id, title="t", original_dream="d"))
    await db.commit()


async def test_a_dream_with_an_unknown_source_is_a_422(client):
    r = await client.post(
        "/api/v1/dreams", json={"title": "t", "original_dream": "d", "source": "import"}
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("intensity", [-1, 11, 2**31])
async def test_a_dream_intensity_outside_0_to_10_is_a_422(client, intensity):
    r = await client.post(
        "/api/v1/dreams", json={"title": "t", "original_dream": "d", "intensity": intensity}
    )
    assert r.status_code == 422, r.text

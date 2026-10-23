"""GET /progress/stats — counts for the home tiles."""

from __future__ import annotations


async def _dream(client, *, nightmare: bool, rewritten: str | None) -> None:
    r = await client.post(
        "/api/v1/dreams",
        json={
            "title": "A dream",
            "original_dream": "Something happened.",
            "is_nightmare": nightmare,
        },
    )
    assert r.status_code == 201, r.text
    if rewritten is not None:
        r = await client.put(
            f"/api/v1/dreams/{r.json()['id']}", json={"rewritten_dream": rewritten}
        )
        assert r.status_code == 200, r.text


async def test_stats_count_rewritten_dreams_without_claims(client) -> None:
    await _dream(client, nightmare=True, rewritten="A calm ending.")
    await _dream(client, nightmare=False, rewritten="A new ending.")
    await _dream(client, nightmare=True, rewritten=None)

    r = await client.get("/api/v1/progress/stats")

    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["dreams_logged"] == 3
    assert stats["dreams_rewritten"] == 2
    # The old field claimed treatment; it is gone from the API.
    assert "nightmares_treated" not in stats

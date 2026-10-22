"""Dreams CRUD basics."""

from __future__ import annotations


async def test_create_list_get_update_delete_dream(client) -> None:
    # Create
    r = await client.post(
        "/api/v1/dreams",
        json={
            "title": "Forest dream",
            "original_dream": "Walking under tall trees in moonlight.",
            "intensity": 4,
            "is_nightmare": False,
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    dream_id = created["id"]
    assert created["title"] == "Forest dream"
    assert created["intensity"] == 4

    # List
    r = await client.get("/api/v1/dreams")
    assert r.status_code == 200
    assert any(d["id"] == dream_id for d in r.json())

    # Get
    r = await client.get(f"/api/v1/dreams/{dream_id}")
    assert r.status_code == 200
    assert r.json()["id"] == dream_id

    # Update
    r = await client.put(
        f"/api/v1/dreams/{dream_id}",
        json={"title": "Updated forest dream", "rewritten_dream": "Now bright and warm."},
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["title"] == "Updated forest dream"
    assert updated["rewritten_dream"] == "Now bright and warm."

    # Delete
    r = await client.delete(f"/api/v1/dreams/{dream_id}")
    assert r.status_code == 204

    r = await client.get(f"/api/v1/dreams/{dream_id}")
    assert r.status_code == 404


async def test_dream_create_rejects_rehearsal_output_fields(client) -> None:
    r = await client.post(
        "/api/v1/dreams",
        json={
            "title": "x",
            "original_dream": "y",
            "rehearsal_audio_url": "https://example.com/x.mp3",
        },
    )
    assert r.status_code == 422

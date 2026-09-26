import pytest

from spotify_to_ytmusic.sync.backup import (
    FORMAT,
    export_library,
    restore_library,
    validate_backup,
)
from tests.sync.test_engine import FakeLibrary


def song(id, name="x"):
    return {"id": id, "name": name, "artist": "A", "album": "", "duration": 100.0}


def library():
    lib = FakeLibrary(
        playlists={
            "p1": ("Road trip", [song("v1", "One"), song("v2", "Two")]),
            "p2": ("Chill", [song("v3", "Three")]),
        },
        liked=[song("v9", "Nine")],
    )
    lib.name = "YouTube Music"
    return lib


def test_export_keeps_playlists_in_order_and_liked_songs():
    backup = export_library(library(), lambda *e: None)

    assert backup["format"] == FORMAT and backup["version"] == 1
    assert backup["service"] == "YouTube Music"
    assert [p["name"] for p in backup["playlists"]] == ["Road trip", "Chill"]
    assert [t["id"] for t in backup["playlists"][0]["tracks"]] == ["v1", "v2"]
    assert backup["playlists"][0]["tracks"][0] == {
        "id": "v1",
        "name": "One",
        "artist": "A",
        "duration": 100.0,
    }
    assert [t["id"] for t in backup["liked"]] == ["v9"]


def test_export_selected_playlists_only():
    backup = export_library(
        library(), lambda *e: None, playlist_ids=["p2"], liked=False
    )
    assert [p["name"] for p in backup["playlists"]] == ["Chill"]
    assert backup["liked"] == []


def test_restore_into_another_account_and_again_without_duplicates():
    backup = export_library(library(), lambda *e: None)
    other = FakeLibrary()
    other.name = "YouTube Music"

    report = restore_library(other, backup, lambda *e: None)

    assert other.added == {"new-Road trip": ["v1", "v2"], "new-Chill": ["v3"]}
    assert other.liked_ids == ["v9"]
    assert [(r["label"], r["total"], r["added"]) for r in report] == [
        ("Playlist: Road trip", 2, 2),
        ("Playlist: Chill", 1, 1),
        ("Liked songs", 1, 1),
    ]
    assert all(r["not_found"] == [] for r in report)

    other.liked = [{"id": "v9"}]
    again = restore_library(other, backup, lambda *e: None)
    assert [r["added"] for r in again] == [0, 0, 0]


def test_validate_rejects_other_files_and_other_services():
    backup = export_library(library(), lambda *e: None)
    validate_backup(backup, "YouTube Music")
    with pytest.raises(ValueError, match="YouTube Music backup"):
        validate_backup(backup, "Spotify")
    with pytest.raises(ValueError, match="isn't a backup"):
        validate_backup({"hello": 1}, "YouTube Music")
    with pytest.raises(ValueError, match="newer version"):
        validate_backup({**backup, "version": 99}, "YouTube Music")
    with pytest.raises(ValueError, match="isn't a backup"):
        validate_backup(
            {**backup, "playlists": [{"name": "x", "tracks": [{"no": "id"}]}]},
            "YouTube Music",
        )

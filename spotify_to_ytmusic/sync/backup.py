"""Back up a library's playlists and liked songs to a file, and restore that file
into another account on the same service.

The file stores each service's own ids (video ids / track ids), so restoring needs
no searching and works on any account of that service.
"""

from datetime import datetime, timezone

from spotify_to_ytmusic.sync.engine import Progress

FORMAT = "spotify_to_ytmusic-backup"
VERSION = 1


def _entry(track: dict) -> dict:
    return {
        "id": track["id"],
        "name": track["name"],
        "artist": track["artist"],
        "duration": track.get("duration"),
    }


def export_library(
    lib, progress: Progress, playlist_ids: list[str] | None = None, liked: bool = True
) -> dict:
    playlists = [
        p
        for p in lib.list_playlists()
        if playlist_ids is None or p["id"] in playlist_ids
    ]
    exported = []
    for i, p in enumerate(playlists, start=1):
        progress(f"Reading playlist {p['name']}", i, len(playlists))
        tracks = lib.get_playlist_tracks(p["id"])
        exported.append({"name": p["name"], "tracks": [_entry(t) for t in tracks]})
    return {
        "format": FORMAT,
        "version": VERSION,
        "service": lib.name,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "playlists": exported,
        "liked": [_entry(t) for t in lib.get_liked_tracks()] if liked else [],
    }


def _valid_tracks(tracks) -> bool:
    return isinstance(tracks, list) and all(
        isinstance(t, dict) and isinstance(t.get("id"), str) for t in tracks
    )


def validate_backup(backup, service: str) -> None:
    """Raise ValueError with a readable reason unless ``backup`` can be restored
    into ``service``."""
    if not isinstance(backup, dict) or backup.get("format") != FORMAT:
        raise ValueError("That file isn't a backup made by this app.")
    if backup.get("version", 0) > VERSION:
        raise ValueError("That backup was made by a newer version of this app.")
    if backup.get("service") != service:
        raise ValueError(
            f"That's a {backup.get('service')} backup; it can only be restored into "
            f"{backup.get('service')}, not {service}."
        )
    playlists = backup.get("playlists")
    if (
        not isinstance(playlists, list)
        or not all(
            isinstance(p, dict)
            and isinstance(p.get("name"), str)
            and _valid_tracks(p.get("tracks"))
            for p in playlists
        )
        or not _valid_tracks(backup.get("liked", []))
    ):
        raise ValueError(
            "That file isn't a backup made by this app (its contents are damaged)."
        )


def _section(label: str, target: dict, total: int, added: int) -> dict:
    return {
        "label": label,
        "kind": "track",
        "target": target,
        "total": total,
        "matched": total,
        "added": added,
        "not_found": [],
    }


def restore_library(lib, backup: dict, progress: Progress) -> list[dict]:
    """Recreate the backup's playlists (by name) and likes in ``lib``; songs that are
    already there are skipped, so restoring twice adds nothing."""
    validate_backup(backup, lib.name)
    report = []
    steps = len(backup["playlists"]) + 1
    for i, playlist in enumerate(backup["playlists"], start=1):
        progress(f"Restoring playlist {playlist['name']}", i, steps)
        dest_id, existing = lib.get_or_create_playlist(
            playlist["name"], f"Restored from a {backup['service']} backup"
        )
        ids = list(dict.fromkeys(t["id"] for t in playlist["tracks"]))
        new = [i for i in ids if i not in existing]
        lib.add_to_playlist(dest_id, new)
        report.append(
            _section(
                f"Playlist: {playlist['name']}",
                {"type": "playlist", "id": dest_id},
                len(ids),
                len(new),
            )
        )
    progress("Restoring liked songs", steps, steps)
    liked = list(dict.fromkeys(t["id"] for t in backup.get("liked", [])))
    have = {t["id"] for t in lib.get_liked_tracks()} if liked else set()
    new = [i for i in liked if i not in have]
    lib.like_tracks(new)
    report.append(_section("Liked songs", {"type": "liked"}, len(liked), len(new)))
    return report

"""Direction-agnostic transfer between two music libraries.

``source`` and ``dest`` are SpotifyLibrary / YTMusicLibrary instances (or anything
with the same methods). Re-running a transfer only adds what the destination is
missing, and matches are cached so repeat runs skip searching.
"""

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field

Progress = Callable[[str, int, int], None]


@dataclass(frozen=True)
class Selection:
    playlist_ids: list[str] = field(default_factory=list)
    liked: bool = False
    albums: bool = False
    artists: bool = False


def _unique(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _label(item: dict) -> str:
    return f"{item['artist']} - {item['name']}" if item.get("artist") else item["name"]


def _match_all(items, find, kind, dest, cache, progress, label):
    """Return (matched destination ids in source order, labels of unmatched items)."""
    matched, not_found = [], []
    for i, item in enumerate(items, start=1):
        key = f"{dest.name}|{kind}|{item.get('artist', '')}|{item['name']}|{item.get('album', '')}"
        dest_id = cache.get(key)
        if dest_id is None:
            dest_id = find(item)
            if dest_id is not None:
                cache[key] = dest_id
        if dest_id is None:
            not_found.append(_label(item))
        else:
            matched.append(dest_id)
        progress(f"{label}: matching {i}/{len(items)}", i, len(items))
    return matched, not_found


def _result(label, items, matched, added, not_found) -> dict:
    return {
        "label": label,
        "total": len(items),
        "matched": len(matched),
        "added": added,
        "not_found": not_found,
    }


def _transfer_playlist(source, dest, playlist, cache, progress) -> dict:
    label = f"Playlist: {playlist['name']}"
    tracks = source.get_playlist_tracks(playlist["id"])
    matched, not_found = _match_all(
        tracks, dest.find_track, "track", dest, cache, progress, label
    )
    dest_id, existing = dest.get_or_create_playlist(
        playlist["name"], f"Transferred from {source.name}"
    )
    new = [i for i in _unique(matched) if i not in existing]
    dest.add_to_playlist(dest_id, new)
    return _result(label, tracks, matched, len(new), not_found)


def _transfer_collection(
    label, items, find, kind, existing, write, dest, cache, progress
) -> dict:
    matched, not_found = _match_all(items, find, kind, dest, cache, progress, label)
    have = {x["id"] for x in existing()}
    new = [i for i in _unique(matched) if i not in have]
    write(new)
    return _result(label, items, matched, len(new), not_found)


def run_transfer(
    source, dest, selection: Selection, progress: Progress, cache: MutableMapping
) -> list[dict]:
    report = []
    wanted = set(selection.playlist_ids)
    for playlist in (p for p in source.list_playlists() if p["id"] in wanted):
        report.append(_transfer_playlist(source, dest, playlist, cache, progress))

    if selection.liked:
        report.append(
            _transfer_collection(
                "Liked songs",
                source.get_liked_tracks(),
                dest.find_track,
                "track",
                dest.get_liked_tracks,
                dest.like_tracks,
                dest,
                cache,
                progress,
            )
        )
    if selection.albums:
        report.append(
            _transfer_collection(
                "Saved albums",
                source.get_albums(),
                dest.find_album,
                "album",
                dest.get_albums,
                dest.save_albums,
                dest,
                cache,
                progress,
            )
        )
    if selection.artists:
        report.append(
            _transfer_collection(
                "Followed artists",
                source.get_artists(),
                dest.find_artist,
                "artist",
                dest.get_artists,
                dest.follow_artists,
                dest,
                cache,
                progress,
            )
        )
    return report

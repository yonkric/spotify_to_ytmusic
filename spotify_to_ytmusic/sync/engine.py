"""Direction-agnostic transfer between two music libraries.

``source`` and ``dest`` are SpotifyLibrary / YTMusicLibrary instances (or anything
with the same methods). Re-running a transfer only adds what the destination is
missing, and matches are cached so repeat runs skip searching.

Items that can't be matched are reported with a reason and the closest rejected
candidates, so the user can pick the right one (``apply_choices``).
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


def _cache_key(dest, kind: str, item: dict) -> str:
    return f"{dest.name}|{kind}|{item.get('artist', '')}|{item['name']}|{item.get('album', '')}"


def _match_all(items, find, kind, dest, cache, progress, label):
    """Return (matched destination ids in source order, unmatched item reports)."""
    matched, not_found = [], []
    for i, item in enumerate(items, start=1):
        key = _cache_key(dest, kind, item)
        dest_id = cache.get(key)
        if dest_id is None:
            match = find(item)
            dest_id = match.id
            if dest_id is None:
                not_found.append(
                    {
                        "label": _label(item),
                        "reason": match.reason,
                        "suggestions": list(match.suggestions),
                        "cache_key": key,
                    }
                )
            else:
                cache[key] = dest_id
        if dest_id is not None:
            matched.append(dest_id)
        progress(f"{label}: matching {i}/{len(items)}", i, len(items))
    return matched, not_found


def _result(label, kind, target, items, matched, added, not_found) -> dict:
    return {
        "label": label,
        "kind": kind,
        "target": target,
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
    target = {"type": "playlist", "id": dest_id}
    return _result(label, "track", target, tracks, matched, len(new), not_found)


def _transfer_collection(
    label, target_type, items, find, kind, existing, write, dest, cache, progress
) -> dict:
    matched, not_found = _match_all(items, find, kind, dest, cache, progress, label)
    have = {x["id"] for x in existing()}
    new = [i for i in _unique(matched) if i not in have]
    write(new)
    target = {"type": target_type}
    return _result(label, kind, target, items, matched, len(new), not_found)


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
                "liked",
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
                "albums",
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
                "artists",
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


def _write_chosen(dest, target: dict, ids: list[str]) -> list[str]:
    """Write ids the user picked; returns the ids that were actually new."""
    if target["type"] == "playlist":
        have = {t["id"] for t in dest.get_playlist_tracks(target["id"])}
        new = [i for i in _unique(ids) if i not in have]
        dest.add_to_playlist(target["id"], new)
        return new
    existing, write = {
        "liked": (dest.get_liked_tracks, dest.like_tracks),
        "albums": (dest.get_albums, dest.save_albums),
        "artists": (dest.get_artists, dest.follow_artists),
    }[target["type"]]
    have = {x["id"] for x in existing()}
    new = [i for i in _unique(ids) if i not in have]
    write(new)
    return new


def apply_choices(
    dest,
    section: dict,
    choices: dict[int, str],
    cache: MutableMapping,
    pasted: set[int] = frozenset(),
) -> dict:
    """Add the suggestions the user picked for unmatched items of one report section.

    ``choices`` maps an index into ``section["not_found"]`` to a suggested id. Only ids
    that were actually offered for that item are accepted, except for indexes in
    ``pasted``: ids from links the user pasted (already checked by the library).
    Returns the updated section.
    """
    for index, chosen in choices.items():
        if not 0 <= index < len(section["not_found"]):
            raise ValueError(f"No unmatched item #{index} in {section['label']}")
        offered = {s["id"] for s in section["not_found"][index]["suggestions"]}
        if index not in pasted and chosen not in offered:
            raise ValueError(
                f"'{chosen}' was not offered for {section['not_found'][index]['label']}"
            )

    new = _write_chosen(dest, section["target"], list(choices.values()))
    for index, chosen in choices.items():
        cache[section["not_found"][index]["cache_key"]] = chosen
    return {
        **section,
        "matched": section["matched"] + len(choices),
        "added": section["added"] + len(new),
        "not_found": [
            nf for i, nf in enumerate(section["not_found"]) if i not in choices
        ],
    }

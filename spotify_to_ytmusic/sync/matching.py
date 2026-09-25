"""Service-agnostic matching of tracks, albums and artists.

Candidates and targets are plain dicts so both directions (Spotify -> YouTube
Music and back) share one scorer:

- track:  {"id", "name", "artist", "album", "duration" (seconds|None), "is_song"}
- album:  {"id", "name", "artist"}
- artist: {"id", "name"}
"""

import difflib
import re

MIN_TITLE_SIMILARITY = 0.6
MIN_ARTIST_SIMILARITY = 0.4
MIN_ALBUM_SIMILARITY = 0.8
MIN_ALBUM_ARTIST_SIMILARITY = 0.5
MIN_ARTIST_NAME_SIMILARITY = 0.9
SONG_BONUS = 1.1

_FEATURING = re.compile(
    r"\s*[\(\[](feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.IGNORECASE
)
_REMASTER = re.compile(r"\s+-\s+.*remaster.*$", re.IGNORECASE)


def clean_title(title: str) -> str:
    title = _FEATURING.sub("", title)
    title = _REMASTER.sub("", title)
    return title.strip().lower()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a.lower(), b=b.lower()).ratio()


def _artist_similarity(candidate: str, target: str) -> float:
    candidate, target = candidate.lower(), target.lower()
    if candidate and target and (candidate in target or target in candidate):
        return 1.0
    return _similarity(candidate, target)


def _duration_score(candidate: float | None, target: float | None) -> float | None:
    if not candidate or not target:
        return None
    return max(0.0, 1 - abs(candidate - target) * 2 / target)


def _track_score(candidate: dict, target: dict) -> float | None:
    title = _similarity(clean_title(candidate["name"]), clean_title(target["name"]))
    artist = _artist_similarity(candidate["artist"], target["artist"])
    if title < MIN_TITLE_SIMILARITY or artist < MIN_ARTIST_SIMILARITY:
        return None

    weighted = [(title, 2.0), (artist, 1.0)]
    duration = _duration_score(candidate.get("duration"), target.get("duration"))
    if duration is not None:
        weighted.append((duration, 2.0))
    if candidate.get("album") and target.get("album"):
        weighted.append((_similarity(candidate["album"], target["album"]), 0.5))

    score = sum(s * w for s, w in weighted) / sum(w for _, w in weighted)
    return score * SONG_BONUS if candidate.get("is_song") else score


def best_track(candidates: list[dict], target: dict) -> str | None:
    scored = [
        (s, c["id"]) for c in candidates if (s := _track_score(c, target)) is not None
    ]
    return max(scored)[1] if scored else None


def best_album(candidates: list[dict], target: dict) -> str | None:
    scored = []
    for c in candidates:
        name = _similarity(clean_title(c["name"]), clean_title(target["name"]))
        artist = _artist_similarity(c["artist"], target["artist"])
        if name >= MIN_ALBUM_SIMILARITY and artist >= MIN_ALBUM_ARTIST_SIMILARITY:
            scored.append((name + artist, c["id"]))
    return max(scored)[1] if scored else None


def best_artist(candidates: list[dict], target: dict) -> str | None:
    scored = [
        (s, c["id"])
        for c in candidates
        if (s := _similarity(c["name"], target["name"])) >= MIN_ARTIST_NAME_SIMILARITY
    ]
    return max(scored)[1] if scored else None

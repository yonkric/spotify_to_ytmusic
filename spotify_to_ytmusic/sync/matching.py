"""Service-agnostic matching of tracks, albums and artists.

Candidates and targets are plain dicts so both directions (Spotify -> YouTube
Music and back) share one scorer:

- track:  {"id", "name", "artist", "album", "duration" (seconds|None), "is_song",
           "primary_artist", "artist_ids"}
- album:  {"id", "name", "artist"}
- artist: {"id", "name"}
"""

import difflib
import re
from dataclasses import dataclass

MIN_TITLE_SIMILARITY = 0.6
MIN_ARTIST_SIMILARITY = 0.4
MIN_ALBUM_SIMILARITY = 0.8
MIN_ALBUM_ARTIST_SIMILARITY = 0.5
MIN_ARTIST_NAME_SIMILARITY = 0.9
SONG_BONUS = 1.1
# The same recording is almost always within a few seconds on both services; a
# bigger gap means live / remix / edit / another song, so it goes to user review
MAX_DURATION_DIFF_SECONDS = 10
MAX_DURATION_DIFF_RATIO = 0.05
MIN_SPELLING_SIMILARITY = 0.8
MAX_SUGGESTIONS = 5
# Without a length to check, only near-identical titles are accepted
MIN_TITLE_SIMILARITY_WITHOUT_LENGTH = 0.9
# Soundtrack notes are only ignored in titles when lengths agree this closely
CORE_TITLE_MAX_DURATION_DIFF = 6

_FEATURING = re.compile(
    r"\s*[\(\[](feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.IGNORECASE
)
_REMASTER = re.compile(r"\s+-\s+.*remaster.*$", re.IGNORECASE)
# Markers for the standard version of a song, which the other service omits:
# "Seven - Explicit Ver.", "Tsunami - Original Mix", "Tremor (Original Mix)"
_STANDARD_VERSION = re.compile(
    r"\s*(?:-\s*|[\(\[])\s*(?:explicit(?:\s+ver(?:sion|\.)?)?|original\s+mix)\s*[\)\]]?",
    re.IGNORECASE,
)
# YouTube Music appends English translations to non-Latin titles: "オレンジ - Orange"
_TRANSLATION = re.compile(r"^(?P<title>.*[^\x00-\x7f].*?)\s+-\s+[\x00-\x7f]+$")
# Bracket notes that only say where a song is from: "有点甜 (《萌三国》网游主题曲)",
# "體面（電影《前任3》插曲）". Notes marking another recording are never ignored.
_BRACKET_NOTE = re.compile(r"\s*[\(（][^\)）]*[\)）]")
_SOUNDTRACK_MARKERS = re.compile(
    r"主题曲|主題曲|插曲|片尾曲|片头曲|片頭曲|电视剧|電視劇|电影|電影|《"
)
_VERSION_MARKERS = re.compile(
    r"伴奏|版|live|现场|現場|演唱会|演唱會|dj|remix|翻唱|cover|钢琴|鋼琴|纯音乐|純音樂|instrumental|倍速",
    re.IGNORECASE,
)

# Uploads that aren't the vocal original; rejected unless the target title says so too
_NOT_ORIGINAL = re.compile(
    r"instrumental|\binst\b|karaoke|off[ -]?vocal|\bcover\b|インスト|カラオケ|"
    r"オフボーカル|歌ってみた|弾いてみた|伴奏|翻唱|纯音乐|純音樂|伴唱|"
    r"[\(\[（【]\s*live\b|\s-\s*live\b",
    re.IGNORECASE,
)


def _not_original(candidate: str, target: str) -> bool:
    return bool(_NOT_ORIGINAL.search(candidate)) and not _NOT_ORIGINAL.search(target)


def _strip_soundtrack_notes(title: str) -> str:
    def replace(note: re.Match) -> str:
        text = note.group(0)
        if _SOUNDTRACK_MARKERS.search(text) and not _VERSION_MARKERS.search(text):
            return ""
        return text

    return _BRACKET_NOTE.sub(replace, title).strip()


def search_title(title: str) -> str:
    """Title as a person would search it: no featuring, remaster or
    standard-version markers."""
    title = _FEATURING.sub("", title)
    title = _REMASTER.sub("", title)
    return _STANDARD_VERSION.sub("", title).strip()


def clean_title(title: str) -> str:
    return search_title(title).lower()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a.lower(), b=b.lower()).ratio()


_FILLER_WORDS = {"of", "the", "and", "feat", "ft", "featuring", "with", "x", "vs"}


def _words(text: str) -> set[str]:
    """Distinctive name words; numbers and filler ("Jackson 5" / "5 Seconds of
    Summer") say nothing about being the same artist."""
    return {
        w
        for w in re.findall(r"\w+", text.lower())
        if not w.isdigit() and w not in _FILLER_WORDS
    }


def _artist_similarity(candidate: str, target: str) -> float:
    """Shared artist words (credits differ: "Dax" / "Dax Elle King"), or near-identical
    spelling. Loose character similarity alone lets unrelated names through."""
    candidate, target = candidate.lower(), target.lower()
    if candidate and target and (candidate in target or target in candidate):
        return 1.0
    c_words, t_words = _words(candidate), _words(target)
    overlap = (
        len(c_words & t_words) / min(len(c_words), len(t_words))
        if c_words and t_words
        else 0.0
    )
    spelling = _similarity(candidate, target)
    return max(overlap, spelling if spelling >= MIN_SPELLING_SIMILARITY else 0.0)


def _length_differs(candidate: float | None, target: float | None) -> bool:
    if not candidate or not target:
        return False
    allowed = max(MAX_DURATION_DIFF_SECONDS, target * MAX_DURATION_DIFF_RATIO)
    return abs(candidate - target) > allowed


def _duration_score(candidate: float | None, target: float | None) -> float | None:
    if not candidate or not target:
        return None
    return max(0.0, 1 - abs(candidate - target) * 2 / target)


def _title_similarity(candidate: str, target: str) -> float:
    target = clean_title(target)
    variants = [candidate]
    if match := _TRANSLATION.match(candidate):
        variants.append(match.group("title"))
    return max(_similarity(clean_title(v), target) for v in variants)


def _core_title_similarity(candidate: str, target: str) -> float:
    """Title similarity with soundtrack notes removed from both sides."""
    return _similarity(
        _strip_soundtrack_notes(clean_title(candidate)),
        _strip_soundtrack_notes(clean_title(target)),
    )


def _same_length(candidate: float | None, target: float | None) -> bool:
    return (
        bool(candidate and target)
        and abs(candidate - target) <= CORE_TITLE_MAX_DURATION_DIFF
    )


def _artist_verified(candidate: dict, artist_ids: frozenset[str]) -> bool:
    """The service itself says this is the target's artist (Jay Chou = 周杰倫)."""
    return bool(artist_ids & set(candidate.get("artist_ids") or ()))


def _no_length(candidate: dict, target: dict) -> bool:
    return bool(target.get("duration")) and not candidate.get("duration")


def _track_title(candidate: dict, target: dict) -> float:
    title = _title_similarity(candidate["name"], target["name"])
    if _same_length(candidate.get("duration"), target.get("duration")):
        title = max(title, _core_title_similarity(candidate["name"], target["name"]))
    return title


def _track_score(
    candidate: dict, target: dict, artist_ids: frozenset[str]
) -> float | None:
    if _length_differs(candidate.get("duration"), target.get("duration")):
        return None
    if _not_original(candidate["name"], target["name"]):
        return None
    title = _track_title(candidate, target)
    if _no_length(candidate, target) and title < MIN_TITLE_SIMILARITY_WITHOUT_LENGTH:
        return None
    artist = _artist_similarity(candidate["artist"], target["artist"])
    if _artist_verified(candidate, artist_ids):
        artist = 1.0
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


def needs_artist_check(candidates: list[dict], target: dict) -> bool:
    """True when some candidate fails only on the artist name, so asking the service
    who the artist is (aliases, other scripts) could still confirm it."""
    return any(
        not _length_differs(c.get("duration"), target.get("duration"))
        and _track_title(c, target) >= MIN_TITLE_SIMILARITY
        and _artist_similarity(c["artist"], target["artist"]) < MIN_ARTIST_SIMILARITY
        and c.get("artist_ids")
        for c in candidates
    )


@dataclass(frozen=True)
class Match:
    """Outcome of matching one item: the accepted id, or why nothing was accepted
    plus the closest rejected candidates for the user to review."""

    id: str | None
    reason: str = ""
    suggestions: tuple[dict, ...] = ()


def _no_match(rejected: list[tuple[float, dict, str]]) -> Match:
    if not rejected:
        return Match(None, "no results")
    rejected.sort(key=lambda r: -r[0])
    suggestions = tuple(
        {**c, "reason": why} for _, c, why in rejected[:MAX_SUGGESTIONS]
    )
    closest = suggestions[0]
    who = f"{closest['artist']} - " if closest.get("artist") else ""
    return Match(
        None, f"closest: {who}{closest['name']} ({closest['reason']})", suggestions
    )


def _track_rejection(candidate: dict, target: dict, artist_ids: frozenset[str]) -> str:
    if _not_original(candidate["name"], target["name"]):
        return "live/instrumental/karaoke/cover"
    title = _track_title(candidate, target)
    if title < MIN_TITLE_SIMILARITY:
        return "different title"
    parts = []
    if _artist_similarity(
        candidate["artist"], target["artist"]
    ) < MIN_ARTIST_SIMILARITY and not _artist_verified(candidate, artist_ids):
        parts.append("different artist")
    c_len, t_len = candidate.get("duration"), target.get("duration")
    if _length_differs(c_len, t_len):
        parts.append(f"length differs by {round(abs(c_len - t_len))}s")
    elif _no_length(candidate, target) and title < MIN_TITLE_SIMILARITY_WITHOUT_LENGTH:
        parts.append("no length to confirm")
    return ", ".join(parts) or "not close enough"


def match_track(
    candidates: list[dict], target: dict, artist_ids: frozenset[str] = frozenset()
) -> Match:
    """``artist_ids``: the target artist's ids on the candidates' service, when known."""
    scored, rejected = [], []
    for c in candidates:
        score = _track_score(c, target, artist_ids)
        if score is None:
            closeness = _title_similarity(c["name"], target["name"])
            rejected.append((closeness, c, _track_rejection(c, target, artist_ids)))
        else:
            scored.append((score, c["id"]))
    return Match(max(scored)[1]) if scored else _no_match(rejected)


def match_album(candidates: list[dict], target: dict) -> Match:
    scored, rejected = [], []
    for c in candidates:
        name = _similarity(clean_title(c["name"]), clean_title(target["name"]))
        artist = _artist_similarity(c["artist"], target["artist"])
        if name >= MIN_ALBUM_SIMILARITY and artist >= MIN_ALBUM_ARTIST_SIMILARITY:
            scored.append((name + artist, c["id"]))
        else:
            why = (
                "different album name"
                if name < MIN_ALBUM_SIMILARITY
                else "different artist"
            )
            rejected.append((name, c, why))
    return Match(max(scored)[1]) if scored else _no_match(rejected)


def match_artist(candidates: list[dict], target: dict) -> Match:
    scored, rejected = [], []
    for c in candidates:
        name = _similarity(c["name"], target["name"])
        if name >= MIN_ARTIST_NAME_SIMILARITY:
            scored.append((name, c["id"]))
        else:
            rejected.append((name, c, "different name"))
    return Match(max(scored)[1]) if scored else _no_match(rejected)


def best_track(candidates: list[dict], target: dict) -> str | None:
    return match_track(candidates, target).id


def best_album(candidates: list[dict], target: dict) -> str | None:
    return match_album(candidates, target).id


def best_artist(candidates: list[dict], target: dict) -> str | None:
    return match_artist(candidates, target).id

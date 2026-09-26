"""Read and write a YouTube Music library through ytmusicapi (browser auth)."""

import json
import re
import time

from ytmusicapi.models.content.enums import LikeStatus

from spotify_to_ytmusic.sync.matching import (
    Match,
    match_album,
    match_artist,
    match_track,
    bare_title,
    needs_artist_check,
    search_title,
)

ALL = 100_000  # ytmusicapi uses an int limit for these endpoints; large enough for any library
PLAYLIST_WRITE_BATCH = 100
AUTO_PLAYLISTS = {"LM", "SE"}  # Liked Music, Episodes for Later
# Pause between searches; Google answers bursts of ~2k searches with a 403
# "Sorry..." page. The real limit isn't published, 0.5s is a conservative guess.
SEARCH_INTERVAL = 0.5


def _artists(artists: list[dict] | None) -> str:
    return " ".join(a["name"] for a in artists or [])


def _seconds(t: dict) -> int | None:
    if t.get("duration_seconds"):
        return t["duration_seconds"]
    if not t.get("duration"):
        return None
    seconds = 0
    for part in t["duration"].split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


# Video titles carry upload notes: "Stonehenge [Official music video HD]"
_VIDEO_NOTES = re.compile(
    r"\s*[\(\[【]\s*(?:official|music\s+video|m/?v\b|lyrics?\b|audio\b|visuali[sz]er|hd\b|4k\b)"
    r"[^\)\]】]*[\)\]】]",
    re.IGNORECASE,
)
_DASH = re.compile(r"\s+[-–—]\s+")


def _video_variants(title: str, uploader: str) -> tuple[str, list[dict]]:
    """Read a video title like a person: drop upload notes, and also try
    "Artist - Title" (uploaded by a label or TV channel)."""
    title = _VIDEO_NOTES.sub("", title).strip()
    # every dash is a possible artist/title split: "Uploader - Artist - Title"
    variants = [
        {"name": title[dash.end() :], "artist": f"{title[: dash.start()]} {uploader}"}
        for dash in _DASH.finditer(title)
    ]
    return title, variants


def _track(t: dict) -> dict:
    album = t.get("album")
    # search results say resultType; playlist entries only carry videoType
    is_video = t.get("resultType") == "video" or t.get("videoType") in (
        "MUSIC_VIDEO_TYPE_OMV",
        "MUSIC_VIDEO_TYPE_UGC",
    )
    is_song = not is_video or t.get("videoType") == "MUSIC_VIDEO_TYPE_ATV"
    name, variants = t["title"], []
    if is_video:
        name, variants = _video_variants(t["title"], _artists(t.get("artists")))
    return {
        "id": t["videoId"],
        "name": name,
        "variants": variants,
        "artist": _artists(t.get("artists")),
        "primary_artist": (t.get("artists") or [{"name": ""}])[0]["name"],
        "artist_ids": [a["id"] for a in t.get("artists") or [] if a.get("id")],
        "album": album["name"] if album else "",
        "duration": _seconds(t),
        "is_song": is_song,
    }


def _to_tracks(items: list[dict]) -> list[dict]:
    return [_track(t) for t in items if t.get("videoId") and t.get("title")]


class YTMusicLibrary:
    name = "YouTube Music"

    def __init__(self, api, search_interval: float = SEARCH_INTERVAL):
        self.api = api
        self.search_interval = search_interval
        self._next_search_at = 0.0
        self._artist_ids: dict[str, frozenset[str]] = {}
        self._lengths: dict[str, int] = {}

    def _search(self, query: str, **kwargs) -> list[dict]:
        now = time.monotonic()
        wait = self._next_search_at - now
        if wait > 0:
            time.sleep(wait)
        self._next_search_at = now + max(wait, 0) + self.search_interval
        try:
            return self.api.search(query, **kwargs)
        except json.JSONDecodeError as ex:
            # ytmusicapi fails to parse Google's HTML "Sorry..." block page
            raise RuntimeError(
                "YouTube is rate-limiting searches from your account (it answered with "
                "its automated-traffic page). Wait a while, then run the transfer again; "
                "everything already copied is kept and matches are remembered."
            ) from ex

    # ---- read -------------------------------------------------------------

    def list_playlists(self) -> list[dict]:
        return [
            {"id": p["playlistId"], "name": p["title"], "count": p.get("count")}
            for p in self.api.get_library_playlists(limit=None)
            if p["playlistId"] not in AUTO_PLAYLISTS
        ]

    def get_playlist_tracks(self, playlist_id: str) -> list[dict]:
        return _to_tracks(
            self.api.get_playlist(playlist_id, limit=None).get("tracks", [])
        )

    def get_liked_tracks(self) -> list[dict]:
        return _to_tracks(self.api.get_liked_songs(limit=ALL).get("tracks", []))

    def get_albums(self) -> list[dict]:
        return [
            {
                "id": a["browseId"],
                "name": a["title"],
                "artist": _artists(a.get("artists")),
            }
            for a in self.api.get_library_albums(limit=ALL)
        ]

    def get_artists(self) -> list[dict]:
        return [
            {"id": a["browseId"], "name": a["artist"]}
            for a in self.api.get_library_subscriptions(limit=ALL)
        ]

    # ---- search -----------------------------------------------------------

    def resolve_artist(self, name: str) -> frozenset[str]:
        """Channel id YouTube Music itself gives an artist name (aliases, other
        scripts: "Jay Chou" -> 周杰倫). Top result only; remembered per run."""
        if name not in self._artist_ids:
            results = self._search(name, filter="artists")[:1]
            self._artist_ids[name] = frozenset(
                r["browseId"] for r in results if r.get("browseId")
            )
        return self._artist_ids[name]

    def _length(self, video_id: str) -> int:
        if video_id not in self._lengths:
            details = self.api.get_song(video_id)["videoDetails"]
            self._lengths[video_id] = int(details["lengthSeconds"])
        return self._lengths[video_id]

    def _match_once(self, candidates: list[dict], track: dict) -> Match:
        match = match_track(candidates, track)
        if match.id or not needs_artist_check(candidates, track):
            return match
        artist_ids = self.resolve_artist(track.get("primary_artist") or track["artist"])
        return match_track(candidates, track, artist_ids)

    def _match(self, candidates: list[dict], track: dict) -> Match:
        """Match, but never accept a result whose length wasn't checked: results
        from the all-results search carry no duration, so look it up first."""
        candidates = list(candidates)
        while True:
            match = self._match_once(candidates, track)
            chosen = next((c for c in candidates if c["id"] == match.id), None)
            if chosen is None or chosen["duration"] or not track.get("duration"):
                return match
            candidates = [
                {**c, "duration": self._length(c["id"])} if c is chosen else c
                for c in candidates
            ]

    def find_track(self, track: dict) -> Match:
        # "songs" results carry durations and artist channel ids; all-results is
        # last, for tracks that only exist as videos
        title = search_title(track["name"])
        queries = [
            (f"{track['artist']} {title}", "songs"),
            (bare_title(track["name"]), "songs"),
            (f"{track['artist']} {title}", None),
        ]
        seen: dict[str, dict] = {}
        for query_index, (query, search_filter) in enumerate(queries):
            results = self._search(query, filter=search_filter)
            candidates = [
                {**_track(r), "rank": query_index * len(results) + position}
                for position, r in enumerate(results)
                if r.get("resultType", "song") in ("song", "video")
                and r.get("videoId")
                and r.get("title")
            ]
            match = self._match(candidates, track)
            if match.id:
                return match
            # keep the first sighting: "songs" results carry durations
            for c in candidates:
                seen.setdefault(c["id"], c)
        return self._match(list(seen.values()), track)

    def find_album(self, album: dict) -> Match:
        results = self._search(f"{album['artist']} {album['name']}", filter="albums")
        candidates = [
            {
                "id": r["browseId"],
                "name": r["title"],
                "artist": _artists(r.get("artists")),
            }
            for r in results
        ]
        return match_album(candidates, album)

    def find_artist(self, artist: dict) -> Match:
        results = self._search(artist["name"], filter="artists")
        return match_artist(
            [{"id": r["browseId"], "name": r["artist"]} for r in results], artist
        )

    _LINK_PATTERNS = {
        "track": re.compile(r"(?:[?&]v=|youtu\.be/|^)([\w-]{11})(?:[&?#]|$)"),
        "album": re.compile(r"browse/(MPRE[\w-]+)"),
        "artist": re.compile(r"channel/(UC[\w-]+)"),
    }

    def id_from_link(self, kind: str, link: str) -> str:
        """Id from a pasted YouTube Music / YouTube link; checks the song exists."""
        match = self._LINK_PATTERNS[kind].search(link.strip())
        if not match:
            raise ValueError(
                f"That doesn't look like a YouTube Music link for a {kind}: {link.strip()}"
            )
        item_id = match.group(1)
        if kind == "track":
            status = self.api.get_song(item_id).get("playabilityStatus", {})
            if status.get("status") != "OK":
                raise ValueError(
                    f"That YouTube video is unavailable ({status.get('reason', 'unknown')})"
                )
        return item_id

    @staticmethod
    def url(kind: str, item_id: str) -> str:
        path = {"track": "watch?v=", "album": "browse/", "artist": "channel/"}[kind]
        return f"https://music.youtube.com/{path}{item_id}"

    # ---- write ------------------------------------------------------------

    def get_or_create_playlist(
        self, name: str, description: str
    ) -> tuple[str, set[str]]:
        for p in self.list_playlists():
            if p["name"] == name:
                return p["id"], {t["id"] for t in self.get_playlist_tracks(p["id"])}
        created = self.api.create_playlist(name, description, privacy_status="PRIVATE")
        if not isinstance(created, str):
            raise RuntimeError(
                f"YouTube Music refused to create playlist '{name}': {created}"
            )
        return created, set()

    def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        for i in range(0, len(track_ids), PLAYLIST_WRITE_BATCH):
            batch = track_ids[i : i + PLAYLIST_WRITE_BATCH]
            response = self.api.add_playlist_items(playlist_id, batch, duplicates=False)
            if not isinstance(response, dict) or "SUCCEEDED" not in response.get(
                "status", ""
            ):
                raise RuntimeError(
                    f"Adding {len(batch)} songs to playlist {playlist_id} failed: {response}"
                )

    def like_tracks(self, track_ids: list[str]) -> None:
        for video_id in track_ids:
            self.api.rate_song(video_id, LikeStatus.LIKE)

    def save_albums(self, album_ids: list[str]) -> None:
        for browse_id in album_ids:
            self.api.rate_playlist(
                self.api.get_album(browse_id)["audioPlaylistId"], LikeStatus.LIKE
            )

    def follow_artists(self, artist_ids: list[str]) -> None:
        if artist_ids:
            self.api.subscribe_artists(artist_ids)

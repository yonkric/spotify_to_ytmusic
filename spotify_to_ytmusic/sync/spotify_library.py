"""Read and write a Spotify library through the Feb 2026 Web API (spotipy>=2.26)."""

import re

from spotify_to_ytmusic.sync.matching import (
    Match,
    match_album,
    match_artist,
    match_track,
    needs_artist_check,
    search_title,
)

SEARCH_LIMIT = 10  # Spotify's maximum for development-mode apps since Feb 2026
PLAYLIST_WRITE_BATCH = 100
LIBRARY_WRITE_BATCH = 40

SCOPES = " ".join(
    [
        "user-library-read",
        "user-library-modify",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
        "user-follow-read",
        "user-follow-modify",
    ]
)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _artists(artists: list[dict]) -> str:
    return " ".join(a["name"] for a in artists)


def _track(t: dict) -> dict:
    return {
        "id": t["id"],
        "name": t["name"],
        "artist": _artists(t["artists"]),
        "primary_artist": t["artists"][0]["name"] if t["artists"] else "",
        "artist_ids": [a["id"] for a in t["artists"] if a.get("id")],
        "album": t["album"]["name"],
        "duration": t["duration_ms"] / 1000,
        "is_song": True,
    }


def _entries_to_tracks(entries: list[dict], key: str) -> list[dict]:
    tracks = []
    for entry in entries:
        t = entry.get(key)
        # local files and unavailable tracks have no id; episodes are not songs
        if t and t.get("id") and t.get("type", "track") == "track":
            tracks.append(_track(t))
    return tracks


class SpotifyLibrary:
    name = "Spotify"

    def __init__(self, api):
        self.api = api
        self._user_id = None
        self._artist_ids: dict[str, frozenset[str]] = {}

    def _all(self, page: dict) -> list[dict]:
        items = list(page["items"])
        while page.get("next"):
            page = self.api.next(page)
            items.extend(page["items"])
        return items

    @property
    def user_id(self) -> str:
        if self._user_id is None:
            self._user_id = self.api.current_user()["id"]
        return self._user_id

    # ---- read -------------------------------------------------------------

    def list_playlists(self) -> list[dict]:
        playlists = self._all(self.api.current_user_playlists(limit=50))
        return [
            {"id": p["id"], "name": p["name"], "count": p["items"]["total"]}
            for p in playlists
            # contents are only readable for playlists the user owns or collaborates on
            if p["owner"]["id"] == self.user_id or p.get("collaborative")
        ]

    def get_playlist_tracks(self, playlist_id: str) -> list[dict]:
        entries = self._all(self.api.playlist_items(playlist_id, limit=100))
        return _entries_to_tracks(entries, "item")

    def get_liked_tracks(self) -> list[dict]:
        return _entries_to_tracks(
            self._all(self.api.current_user_saved_tracks(limit=50)), "track"
        )

    def get_albums(self) -> list[dict]:
        entries = self._all(self.api.current_user_saved_albums(limit=50))
        return [
            {
                "id": e["album"]["id"],
                "name": e["album"]["name"],
                "artist": _artists(e["album"]["artists"]),
            }
            for e in entries
        ]

    def get_artists(self) -> list[dict]:
        page = self.api.current_user_followed_artists(limit=50)["artists"]
        artists = list(page["items"])
        while page.get("next"):
            page = self.api.next(page)["artists"]
            artists.extend(page["items"])
        return [{"id": a["id"], "name": a["name"]} for a in artists]

    # ---- search -----------------------------------------------------------

    def resolve_artist(self, name: str) -> frozenset[str]:
        """Artist id Spotify itself gives a name (aliases, other scripts:
        "周杰倫" -> Jay Chou). Top result only; remembered per run."""
        if name not in self._artist_ids:
            results = self.api.search(name, limit=1, type="artist")["artists"]["items"]
            self._artist_ids[name] = frozenset(a["id"] for a in results if a)
        return self._artist_ids[name]

    def find_track(self, track: dict) -> Match:
        query = f"{search_title(track['name'])} {track['artist']}"
        results = self.api.search(query, limit=SEARCH_LIMIT, type="track")["tracks"][
            "items"
        ]
        candidates = [
            {**_track(t), "rank": position} for position, t in enumerate(results) if t
        ]
        match = match_track(candidates, track)
        if match.id or not needs_artist_check(candidates, track):
            return match
        artist_ids = self.resolve_artist(track.get("primary_artist") or track["artist"])
        return match_track(candidates, track, artist_ids)

    def find_album(self, album: dict) -> Match:
        query = f"{album['name']} {album['artist']}"
        results = self.api.search(query, limit=SEARCH_LIMIT, type="album")["albums"][
            "items"
        ]
        candidates = [
            {"id": a["id"], "name": a["name"], "artist": _artists(a["artists"])}
            for a in results
            if a
        ]
        return match_album(candidates, album)

    def find_artist(self, artist: dict) -> Match:
        results = self.api.search(artist["name"], limit=SEARCH_LIMIT, type="artist")[
            "artists"
        ]["items"]
        return match_artist(
            [{"id": a["id"], "name": a["name"]} for a in results if a], artist
        )

    def id_from_link(self, kind: str, link: str) -> str:
        """Id from a pasted open.spotify.com link or spotify: URI; checks it exists."""
        match = re.search(
            rf"(?:open\.spotify\.com/(?:intl-[\w-]+/)?{kind}/|spotify:{kind}:)([A-Za-z0-9]{{22}})",
            link.strip(),
        )
        if not match:
            raise ValueError(
                f"That doesn't look like a Spotify {kind} link: {link.strip()}"
            )
        lookup = {
            "track": self.api.track,
            "album": self.api.album,
            "artist": self.api.artist,
        }
        return lookup[kind](match.group(1))["id"]

    @staticmethod
    def url(kind: str, item_id: str) -> str:
        return f"https://open.spotify.com/{kind}/{item_id}"

    # ---- write ------------------------------------------------------------

    def get_or_create_playlist(
        self, name: str, description: str
    ) -> tuple[str, set[str]]:
        for p in self.list_playlists():
            if p["name"] == name:
                existing = {t["id"] for t in self.get_playlist_tracks(p["id"])}
                return p["id"], existing
        created = self.api.current_user_playlist_create(
            name, public=False, description=description
        )
        return created["id"], set()

    def add_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        for batch in _chunks(track_ids, PLAYLIST_WRITE_BATCH):
            self.api.playlist_add_items(playlist_id, batch)

    def like_tracks(self, track_ids: list[str]) -> None:
        for batch in _chunks(track_ids, LIBRARY_WRITE_BATCH):
            self.api.current_user_saved_tracks_add(batch)

    def save_albums(self, album_ids: list[str]) -> None:
        for batch in _chunks(album_ids, LIBRARY_WRITE_BATCH):
            self.api.current_user_saved_albums_add(batch)

    def follow_artists(self, artist_ids: list[str]) -> None:
        for batch in _chunks(artist_ids, LIBRARY_WRITE_BATCH):
            self.api.user_follow_artists(batch)

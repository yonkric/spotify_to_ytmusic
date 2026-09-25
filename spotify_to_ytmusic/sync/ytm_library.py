"""Read and write a YouTube Music library through ytmusicapi (browser auth)."""

from ytmusicapi.models.content.enums import LikeStatus

from spotify_to_ytmusic.sync.matching import best_album, best_artist, best_track

ALL = 100_000  # ytmusicapi uses an int limit for these endpoints; large enough for any library
PLAYLIST_WRITE_BATCH = 100
AUTO_PLAYLISTS = {"LM", "SE"}  # Liked Music, Episodes for Later


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


def _track(t: dict) -> dict:
    album = t.get("album")
    return {
        "id": t["videoId"],
        "name": t["title"],
        "artist": _artists(t.get("artists")),
        "album": album["name"] if album else "",
        "duration": _seconds(t),
        "is_song": t.get("resultType", "song") == "song"
        or t.get("videoType") == "MUSIC_VIDEO_TYPE_ATV",
    }


def _to_tracks(items: list[dict]) -> list[dict]:
    return [_track(t) for t in items if t.get("videoId") and t.get("title")]


class YTMusicLibrary:
    name = "YouTube Music"

    def __init__(self, api):
        self.api = api

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

    def find_track(self, track: dict) -> str | None:
        query = f"{track['artist']} {track['name']}"
        results = self.api.search(query)
        candidates = [
            _track(r)
            for r in results
            if r.get("resultType") in ("song", "video")
            and r.get("videoId")
            and r.get("title")
        ]
        return best_track(candidates, track)

    def find_album(self, album: dict) -> str | None:
        results = self.api.search(f"{album['artist']} {album['name']}", filter="albums")
        candidates = [
            {
                "id": r["browseId"],
                "name": r["title"],
                "artist": _artists(r.get("artists")),
            }
            for r in results
        ]
        return best_album(candidates, album)

    def find_artist(self, artist: dict) -> str | None:
        results = self.api.search(artist["name"], filter="artists")
        return best_artist(
            [{"id": r["browseId"], "name": r["artist"]} for r in results], artist
        )

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

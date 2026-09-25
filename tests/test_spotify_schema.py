"""Offline tests for the Spotify Web API response schema introduced in Feb 2026.

Playlist objects expose their contents under ``items`` (formerly ``tracks``) and
each playlist entry nests the track under ``item`` (formerly ``track``).
"""

from unittest.mock import MagicMock

from spotify_to_ytmusic.spotify import Spotify, build_results


def make_track(name="Song", duration_ms=200_000):
    return {
        "name": name,
        "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
        "album": {"name": "Album"},
        "duration_ms": duration_ms,
    }


def make_spotify(api):
    spotify = Spotify.__new__(Spotify)
    spotify.api = api
    return spotify


class TestBuildResults:
    def test_playlist_entry_nested_under_item(self):
        results = build_results([{"item": make_track()}])
        assert results == [
            {
                "artist": "Artist A Artist B",
                "name": "Song",
                "album": "Album",
                "duration": 200.0,
            }
        ]

    def test_saved_track_nested_under_track(self):
        # GET /me/tracks still nests the track under "track"
        assert len(build_results([{"track": make_track()}])) == 1

    def test_skips_removed_and_empty_entries(self):
        entries = [{"item": None}, {"item": make_track(duration_ms=0)}]
        assert build_results(entries) == []


class TestGetSpotifyPlaylist:
    def test_reads_items_field_and_paginates(self):
        api = MagicMock()
        api.playlist.return_value = {
            "name": "My list",
            "description": "a &amp; b",
            "items": {"total": 2, "items": [{"item": make_track("One")}]},
        }
        api.playlist_items.return_value = {"items": [{"item": make_track("Two")}]}

        data = make_spotify(api).getSpotifyPlaylist(
            "https://open.spotify.com/playlist/03ICMYsVsC4I2SZnERcQJb"
        )

        assert [t["name"] for t in data["tracks"]] == ["One", "Two"]
        assert data["name"] == "My list"
        assert data["description"] == "a & b"
        api.playlist_items.assert_called_once_with(
            "03ICMYsVsC4I2SZnERcQJb", offset=1, limit=100
        )


class TestGetUserPlaylists:
    def test_lists_own_nonempty_playlists_via_current_user(self):
        api = MagicMock()
        api.current_user_playlists.return_value = {
            "items": [
                {"owner": {"id": "me"}, "items": {"total": 5}},
                {"owner": {"id": "me"}, "items": {"total": 0}},
                {"owner": {"id": "someone"}, "items": {"total": 3}},
            ],
            "next": None,
        }

        playlists = make_spotify(api).getUserPlaylists("me")

        assert playlists == [{"owner": {"id": "me"}, "items": {"total": 5}}]

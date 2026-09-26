from unittest.mock import MagicMock

import pytest
from ytmusicapi.models.content.enums import LikeStatus

from spotify_to_ytmusic.sync.spotify_library import SpotifyLibrary
from spotify_to_ytmusic.sync.ytm_library import YTMusicLibrary


def sp_track(id, name="Find You", artist="Nick Jonas", ms=197_900):
    return {
        "id": id,
        "type": "track",
        "name": name,
        "artists": [{"name": artist}],
        "album": {"name": name},
        "duration_ms": ms,
    }


def page(items, next=None):
    return {"items": items, "next": next}


class TestSpotifyLibrary:
    def setup_method(self):
        self.api = MagicMock()
        self.api.current_user.return_value = {"id": "me"}
        self.lib = SpotifyLibrary(self.api)

    def test_lists_owned_and_collaborative_playlists_across_pages(self):
        self.api.current_user_playlists.return_value = page(
            [{"id": "a", "name": "Mine", "owner": {"id": "me"}, "items": {"total": 3}}],
            next="n",
        )
        self.api.next.return_value = page(
            [
                {
                    "id": "b",
                    "name": "Followed",
                    "owner": {"id": "other"},
                    "items": {"total": 9},
                },
                {
                    "id": "c",
                    "name": "Collab",
                    "owner": {"id": "other"},
                    "collaborative": True,
                    "items": {"total": 1},
                },
            ]
        )
        assert self.lib.list_playlists() == [
            {"id": "a", "name": "Mine", "count": 3},
            {"id": "c", "name": "Collab", "count": 1},
        ]

    def test_playlist_tracks_skip_local_files_and_episodes(self):
        self.api.playlist_items.return_value = page(
            [
                {"item": sp_track("t1")},
                {"item": {**sp_track(None), "id": None}},
                {"item": {**sp_track("e1"), "type": "episode"}},
                {"item": None},
            ]
        )
        tracks = self.lib.get_playlist_tracks("p")
        assert [t["id"] for t in tracks] == ["t1"]
        assert tracks[0]["duration"] == 197.9

    def test_find_track_uses_dev_mode_search_limit(self):
        self.api.search.return_value = {"tracks": {"items": [sp_track("hit")]}}
        target = {
            "name": "Find You",
            "artist": "Nick Jonas",
            "album": "",
            "duration": 198,
        }
        assert self.lib.find_track(target).id == "hit"
        assert self.api.search.call_args.kwargs["limit"] == 10

    def test_followed_artists_use_cursor_paging(self):
        self.api.current_user_followed_artists.return_value = {
            "artists": page([{"id": "1", "name": "A"}], next="n")
        }
        self.api.next.return_value = {"artists": page([{"id": "2", "name": "B"}])}
        assert [a["id"] for a in self.lib.get_artists()] == ["1", "2"]

    def test_existing_playlist_is_reused_with_its_tracks(self):
        self.api.current_user_playlists.return_value = page(
            [{"id": "a", "name": "Mix", "owner": {"id": "me"}, "items": {"total": 1}}]
        )
        self.api.playlist_items.return_value = page([{"item": sp_track("t1")}])
        assert self.lib.get_or_create_playlist("Mix", "d") == ("a", {"t1"})
        self.api.current_user_playlist_create.assert_not_called()

    def test_writes_are_batched(self):
        self.lib.like_tracks([str(i) for i in range(85)])
        assert [
            len(c.args[0])
            for c in self.api.current_user_saved_tracks_add.call_args_list
        ] == [40, 40, 5]
        self.lib.add_to_playlist("p", [str(i) for i in range(150)])
        assert [len(c.args[1]) for c in self.api.playlist_add_items.call_args_list] == [
            100,
            50,
        ]


class TestYTMusicLibrary:
    def setup_method(self):
        self.api = MagicMock()
        self.lib = YTMusicLibrary(self.api)

    def test_lists_playlists_without_auto_playlists(self):
        self.api.get_library_playlists.return_value = [
            {"playlistId": "LM", "title": "Liked Music"},
            {"playlistId": "SE", "title": "Episodes for Later"},
            {"playlistId": "PL1", "title": "Mine", "count": "12"},
        ]
        assert self.lib.list_playlists() == [
            {"id": "PL1", "name": "Mine", "count": "12"}
        ]

    def test_find_track_parses_search_durations_and_prefers_songs(self):
        self.api.search.return_value = [
            {
                "resultType": "video",
                "videoId": "v",
                "title": "Find You",
                "artists": [{"name": "Nick Jonas"}],
                "duration": "3:18",
            },
            {
                "resultType": "song",
                "videoId": "s",
                "title": "Find You",
                "artists": [{"name": "Nick Jonas"}],
                "album": {"name": "Find You"},
                "duration": "3:18",
            },
            {"resultType": "album", "browseId": "x", "title": "Find You"},
        ]
        target = {
            "name": "Find You",
            "artist": "Nick Jonas",
            "album": "Find You",
            "duration": 197.9,
        }
        assert self.lib.find_track(target).id == "s"

    def test_create_playlist_error_is_raised(self):
        self.api.get_library_playlists.return_value = []
        self.api.create_playlist.return_value = {"error": "nope"}
        with pytest.raises(RuntimeError, match="Mix"):
            self.lib.get_or_create_playlist("Mix", "d")

    def test_failed_add_is_raised(self):
        self.api.add_playlist_items.return_value = {"status": "STATUS_FAILED"}
        with pytest.raises(RuntimeError, match="PL1"):
            self.lib.add_to_playlist("PL1", ["a"])

    def test_save_album_likes_its_audio_playlist(self):
        self.api.get_album.return_value = {"audioPlaylistId": "OLAK5"}
        self.lib.save_albums(["MPRE1"])
        self.api.rate_playlist.assert_called_once_with("OLAK5", LikeStatus.LIKE)


class TestYTMusicRateLimit:
    def test_google_block_page_becomes_clear_error(self):
        import json

        api = MagicMock()
        api.search.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
        lib = YTMusicLibrary(api, search_interval=0)
        target = {"name": "x", "artist": "y", "album": "", "duration": 100}
        with pytest.raises(RuntimeError, match="rate-limiting"):
            lib.find_track(target)

    def test_searches_are_spaced_out(self, monkeypatch):
        import spotify_to_ytmusic.sync.ytm_library as ytm

        clock = iter([100.0, 100.1])
        sleeps = []
        monkeypatch.setattr(ytm.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(ytm.time, "sleep", sleeps.append)
        api = MagicMock()
        api.search.return_value = []
        lib = YTMusicLibrary(api, search_interval=0.5)
        lib._search("a")
        lib._search("b")
        assert sleeps == [pytest.approx(0.4)]


class TestYTMusicSearchFallbacks:
    def test_songs_filter_first_then_title_only_then_all_results(self):
        api = MagicMock()
        hit = {
            "resultType": "song",
            "videoId": "v",
            "title": "童話",
            "artists": [{"name": "光良"}],
            "duration": "4:05",
        }
        api.search.side_effect = [[], [hit]]
        lib = YTMusicLibrary(api, search_interval=0)
        target = {
            "name": "童話",
            "artist": "Michael Wong",
            "album": "",
            "duration": 244,
        }

        assert lib.find_track(target).id == "v"
        assert [
            (c.args[0], c.kwargs.get("filter")) for c in api.search.call_args_list
        ] == [
            ("Michael Wong 童話", "songs"),
            ("童話", "songs"),
        ]

    def test_stops_at_first_query_with_a_match(self):
        api = MagicMock()
        api.search.return_value = [
            {
                "resultType": "song",
                "videoId": "v",
                "title": "x",
                "artists": [{"name": "y"}],
                "duration": "1:40",
            }
        ]
        lib = YTMusicLibrary(api, search_interval=0)
        assert (
            lib.find_track(
                {"name": "x", "artist": "y", "album": "", "duration": 100}
            ).id
            == "v"
        )
        assert api.search.call_count == 1

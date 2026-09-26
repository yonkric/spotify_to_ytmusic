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
            "title": "Fairy Tale",
            "artists": [{"name": "Michael Wong", "id": "UCmw"}],
            "duration": "4:05",
        }
        api.search.side_effect = [[], [hit]]
        lib = YTMusicLibrary(api, search_interval=0)
        target = {
            "name": "Fairy Tale",
            "artist": "Michael Wong",
            "album": "",
            "duration": 244,
        }

        assert lib.find_track(target).id == "v"
        assert [
            (c.args[0], c.kwargs.get("filter")) for c in api.search.call_args_list
        ] == [
            ("Michael Wong Fairy Tale", "songs"),
            ("Fairy Tale", "songs"),
        ]

    def test_artist_alias_resolved_once_and_used_to_confirm(self):
        api = MagicMock()
        song = {
            "resultType": "song",
            "videoId": "v",
            "title": "童話",
            "artists": [{"name": "光良", "id": "UCgl"}],
            "duration": "4:05",
        }
        cover = {**song, "videoId": "c", "artists": [{"name": "某人", "id": "UCx"}]}
        api.search.side_effect = [
            [cover, song],
            [{"browseId": "UCgl", "artist": "光良"}],
        ]
        lib = YTMusicLibrary(api, search_interval=0)
        target = {
            "name": "童話",
            "artist": "Michael Wong",
            "primary_artist": "Michael Wong",
            "album": "",
            "duration": 244,
        }

        assert lib.find_track(target).id == "v"
        assert api.search.call_args_list[1].args == ("Michael Wong",)
        assert api.search.call_args_list[1].kwargs == {"filter": "artists"}
        lib.resolve_artist("Michael Wong")
        assert api.search.call_count == 2  # remembered

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


class TestYTMusicLengthLookup:
    def _lib(self, results, lengths):
        api = MagicMock()
        api.search.side_effect = lambda *a, **k: (
            results if k.get("filter") is None else []
        )
        api.get_song.side_effect = lambda vid: {
            "videoDetails": {"lengthSeconds": str(lengths[vid])}
        }
        return api, YTMusicLibrary(api, search_interval=0)

    def test_result_without_duration_is_checked_before_accepting(self):
        video = {
            "resultType": "video",
            "videoId": "long",
            "title": "Burning Up (FIRE)",
            "artists": [{"name": "BTS", "id": "UCbts"}],
        }
        api, lib = self._lib([video], {"long": 295})
        target = {
            "name": "Burning Up (FIRE)",
            "artist": "BTS",
            "album": "",
            "duration": 203,
        }
        result = lib.find_track(target)
        assert result.id is None
        assert result.suggestions[0]["reason"] == "length differs by 92s"
        api.get_song.assert_called_once_with("long")

    def test_confirmed_length_is_accepted(self):
        video = {
            "resultType": "video",
            "videoId": "ok",
            "title": "Burning Up (FIRE)",
            "artists": [{"name": "BTS", "id": "UCbts"}],
        }
        _, lib = self._lib([video], {"ok": 204})
        target = {
            "name": "Burning Up (FIRE)",
            "artist": "BTS",
            "album": "",
            "duration": 203,
        }
        assert lib.find_track(target).id == "ok"


class TestVideoTitles:
    def _lib(self, results):
        api = MagicMock()
        api.search.side_effect = lambda *a, **k: (
            results if k.get("filter") is None else []
        )
        return YTMusicLibrary(api, search_interval=0)

    def test_artist_dash_title_video_is_read_like_a_person_would(self):
        video = {
            "resultType": "video",
            "videoId": "mv",
            "duration": "3:56",
            "title": "Ylvis - Stonehenge [Official music video HD]",
            "artists": [{"name": "TVNorge", "id": "UCtv"}],
        }
        target = {"name": "Stonehenge", "artist": "Ylvis", "album": "", "duration": 234}
        assert self._lib([video]).find_track(target).id == "mv"

    def test_official_video_notes_dropped_for_plain_video_titles(self):
        video = {
            "resultType": "video",
            "videoId": "mv",
            "duration": "3:56",
            "title": "Stonehenge (Official Video)",
            "artists": [{"name": "Ylvis", "id": "UCy"}],
        }
        target = {"name": "Stonehenge", "artist": "Ylvis", "album": "", "duration": 234}
        assert self._lib([video]).find_track(target).id == "mv"

    def test_songs_titles_are_not_split(self):
        from spotify_to_ytmusic.sync.ytm_library import _track

        song = {
            "resultType": "song",
            "videoId": "s",
            "title": "Help! - Remastered",
            "artists": [{"name": "The Beatles"}],
            "duration": "2:18",
        }
        assert _track(song)["name"] == "Help! - Remastered"


def test_playlist_entry_videos_are_read_like_search_videos():
    from spotify_to_ytmusic.sync.ytm_library import _track

    entry = {
        "videoId": "v",
        "title": "Beat Music - SCNDL & Strike Nine - Sultan",
        "videoType": "MUSIC_VIDEO_TYPE_UGC",
        "artists": [{"name": "Beat Music"}],
        "duration": "2:53",
    }
    parsed = _track(entry)
    assert parsed["is_song"] is False
    assert [v["name"] for v in parsed["variants"]] == [
        "SCNDL & Strike Nine - Sultan",
        "Sultan",
    ]
    assert (
        parsed["variants"][1]["artist"] == "Beat Music - SCNDL & Strike Nine Beat Music"
    )


def test_title_only_search_drops_bracket_notes():
    api = MagicMock()
    api.search.return_value = []
    lib = YTMusicLibrary(api, search_interval=0)
    lib.find_track(
        {
            "name": "At a Medium Pace (In the Style of Adam Sandler) [Performance Track]",
            "artist": "Done Again",
            "album": "",
            "duration": 120,
        }
    )
    queries = [c.args[0] for c in api.search.call_args_list]
    assert "At a Medium Pace" in queries


class TestPastedLinks:
    def test_youtube_music_link_formats(self):
        api = MagicMock()
        api.get_song.return_value = {
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {"videoId": "3Bzl2Y5A67k"},
        }
        lib = YTMusicLibrary(api, search_interval=0)
        for link in [
            "https://music.youtube.com/watch?v=3Bzl2Y5A67k&si=2FW9D-SlGHGxqL2M",
            "https://www.youtube.com/watch?v=3Bzl2Y5A67k",
            "https://youtu.be/3Bzl2Y5A67k?si=x",
            "3Bzl2Y5A67k",
        ]:
            assert lib.id_from_link("track", link) == "3Bzl2Y5A67k", link

    def test_youtube_music_album_and_artist_links(self):
        lib = YTMusicLibrary(MagicMock(), search_interval=0)
        assert (
            lib.id_from_link("album", "https://music.youtube.com/browse/MPREb_abc123")
            == "MPREb_abc123"
        )
        assert (
            lib.id_from_link("artist", "https://music.youtube.com/channel/UCabc_123")
            == "UCabc_123"
        )

    def test_bad_or_unavailable_youtube_link(self):
        api = MagicMock()
        api.get_song.return_value = {
            "playabilityStatus": {"status": "ERROR", "reason": "Video unavailable"}
        }
        lib = YTMusicLibrary(api, search_interval=0)
        with pytest.raises(ValueError, match="unavailable"):
            lib.id_from_link("track", "https://youtu.be/AAAAAAAAAAA")
        with pytest.raises(ValueError, match="YouTube Music link"):
            lib.id_from_link("track", "https://open.spotify.com/track/abc")

    def test_spotify_links(self):
        api = MagicMock()
        api.track.return_value = {"id": "4uLU6hMCjMI75M1A2tKUQC"}
        api.album.return_value = {"id": "1DFixLWuPkv3KT3TnV35m3"}
        lib = SpotifyLibrary(api)
        assert (
            lib.id_from_link(
                "track", "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=x"
            )
            == "4uLU6hMCjMI75M1A2tKUQC"
        )
        assert (
            lib.id_from_link(
                "album", "https://open.spotify.com/intl-de/album/1DFixLWuPkv3KT3TnV35m3"
            )
            == "1DFixLWuPkv3KT3TnV35m3"
        )
        with pytest.raises(ValueError, match="Spotify track link"):
            lib.id_from_link("track", "https://music.youtube.com/watch?v=3Bzl2Y5A67k")

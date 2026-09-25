from spotify_to_ytmusic.sync.matching import (
    best_album,
    best_artist,
    best_track,
    clean_title,
)


def track(name, artist, album="", duration=200.0, id="x", is_song=True):
    return {
        "id": id,
        "name": name,
        "artist": artist,
        "album": album,
        "duration": duration,
        "is_song": is_song,
    }


TARGET = track("Find You", "Nick Jonas", "Find You", 197.9, id=None)


class TestCleanTitle:
    def test_strips_featuring_and_remaster(self):
        assert clean_title("Wolves (feat. Marshmello)") == "wolves"
        assert clean_title("Help! - Remastered 2009") == "help!"
        assert clean_title("Song [ft. Someone]") == "song"

    def test_keeps_meaningful_brackets(self):
        assert clean_title("Find You (Acoustic)") == "find you (acoustic)"


class TestBestTrack:
    def test_picks_exact_song_over_acoustic_and_other_artist(self):
        candidates = [
            track("Find You (Acoustic)", "Nick Jonas", "Find You", 196, id="acoustic"),
            track("Gotta Find You", "Joe Jonas", "Camp Rock", 242, id="other"),
            track("Find You", "Nick Jonas", "Find You", 198, id="right"),
        ]
        assert best_track(candidates, TARGET) == "right"

    def test_prefers_song_over_equivalent_video(self):
        candidates = [
            track("Find You", "Nick Jonas", duration=198, id="video", is_song=False),
            track("Find You", "Nick Jonas", duration=198, id="song"),
        ]
        assert best_track(candidates, TARGET) == "song"

    def test_rejects_unrelated_results(self):
        candidates = [
            track("Totally Different", "Someone Else", duration=100, id="bad")
        ]
        assert best_track(candidates, TARGET) is None

    def test_empty_candidates(self):
        assert best_track([], TARGET) is None

    def test_missing_durations_still_match_on_text(self):
        candidates = [track("Find You", "Nick Jonas", duration=None, id="ok")]
        assert best_track(candidates, TARGET) == "ok"


class TestBestAlbum:
    def test_matches_name_and_artist(self):
        candidates = [
            {"id": "wrong", "name": "Find You", "artist": "Someone Else"},
            {"id": "right", "name": "Spaceman", "artist": "Nick Jonas"},
        ]
        target = {"name": "Spaceman", "artist": "Nick Jonas"}
        assert best_album(candidates, target) == "right"

    def test_rejects_when_no_close_name(self):
        candidates = [{"id": "x", "name": "Other Album", "artist": "Nick Jonas"}]
        assert (
            best_album(candidates, {"name": "Spaceman", "artist": "Nick Jonas"}) is None
        )


class TestBestArtist:
    def test_matches_case_insensitive(self):
        candidates = [
            {"id": "joe", "name": "Joe Jonas"},
            {"id": "nick", "name": "NICK JONAS"},
        ]
        assert best_artist(candidates, {"name": "Nick Jonas"}) == "nick"

    def test_rejects_different_artist(self):
        assert (
            best_artist([{"id": "joe", "name": "Joe Jonas"}], {"name": "Nick Jonas"})
            is None
        )


class TestCrossScript:
    def test_artist_in_other_script_accepted_when_title_and_duration_agree(self):
        target = track("告白氣球", "Jay Chou", duration=215, id=None)
        candidates = [track("告白氣球", "周杰倫", duration=216, id="right")]
        assert best_track(candidates, target) == "right"

    def test_artist_in_other_script_rejected_when_duration_differs(self):
        target = track("告白氣球", "Jay Chou", duration=215, id=None)
        candidates = [track("告白氣球", "周杰倫", duration=260, id="cover")]
        assert best_track(candidates, target) is None

    def test_artist_in_other_script_rejected_without_duration(self):
        target = track("告白氣球", "Jay Chou", duration=215, id=None)
        candidates = [track("告白氣球", "周杰倫", duration=None, id="unknown")]
        assert best_track(candidates, target) is None

    def test_translated_title_suffix_is_ignored(self):
        target = track("那些年", "Hu Xia", duration=369, id=None)
        candidates = [
            track("那些年 - Those Bygone Years", "Xia Hu", duration=369, id="right")
        ]
        assert best_track(candidates, target) == "right"

    def test_translation_suffix_with_other_script_artist(self):
        target = track("オレンジ", "7!!", duration=350, id=None)
        candidates = [
            track("オレンジ - Orange", "seven oops", duration=351, id="right")
        ]
        assert best_track(candidates, target) == "right"

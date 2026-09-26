from spotify_to_ytmusic.sync.matching import (
    best_album,
    best_artist,
    best_track,
    clean_title,
    match_album,
    match_artist,
    match_track,
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


class TestBracketNotes:
    def test_soundtrack_note_ignored_when_length_agrees(self):
        target = track("有点甜", "Silence Wang BY2", duration=235, id=None)
        candidates = [
            track(
                "有点甜 (《萌三国》网游主题曲|《微微一笑很倾城》电视剧插曲)",
                "汪苏泷 BY2",
                duration=236,
                id="right",
            )
        ]
        assert best_track(candidates, target) == "right"

    def test_bracket_version_rejected_when_length_differs(self):
        target = track("童話", "Michael Wong", duration=244, id=None)
        candidates = [track("童話 (演唱會版)", "光良", duration=290, id="live")]
        assert best_track(candidates, target) is None

    def test_five_second_difference_accepted_across_scripts(self):
        target = track("只是太愛你", "Hins Cheung", duration=254, id=None)
        candidates = [track("只是太愛你", "張敬軒", duration=249, id="right")]
        assert best_track(candidates, target) == "right"


class TestVersionNotesAreKept:
    """Only soundtrack notes are ignored; notes that mark another recording are not."""

    TARGET = track("有点甜", "Silence Wang BY2", duration=235, id=None)

    def test_instrumental_with_same_length_rejected(self):
        candidates = [track("有点甜 (伴奏)", "汪苏泷", duration=235, id="karaoke")]
        assert best_track(candidates, self.TARGET) is None

    def test_other_version_with_same_length_rejected(self):
        for note in ["(DJ版)", "（女生版）", "(演唱會版)", "(翻唱)", "(钢琴曲)"]:
            candidates = [track(f"有点甜 {note}", "汪苏泷", duration=235, id="v")]
            assert best_track(candidates, self.TARGET) is None, note

    def test_soundtrack_note_that_also_says_version_rejected(self):
        candidates = [
            track("有点甜 (电视剧插曲 伴奏版)", "汪苏泷", duration=235, id="v")
        ]
        assert best_track(candidates, self.TARGET) is None

    def test_prefers_original_over_instrumental(self):
        candidates = [
            track("有点甜 (伴奏)", "汪苏泷 BY2", duration=235, id="karaoke"),
            track("有点甜", "汪苏泷 BY2", duration=235, id="original"),
        ]
        assert best_track(candidates, self.TARGET) == "original"


class TestMatchExplanations:
    def test_no_results(self):
        result = match_track([], TARGET)
        assert result.id is None
        assert result.reason == "no results"
        assert result.suggestions == ()

    def test_rejections_explain_why_and_offer_suggestions(self):
        candidates = [
            track("Totally Different", "Someone", duration=100, id="far"),
            track("Find You", "尼克", duration=260, id="long"),
            track("Find You", "尼克", duration=None, id="nolen"),
        ]
        result = match_track(candidates, TARGET)

        assert result.id is None
        reasons = {s["id"]: s["reason"] for s in result.suggestions}
        assert reasons["long"] == "different artist, length differs by 62s"
        assert reasons["nolen"] == "different artist, no length to confirm"
        assert reasons["far"] == "different title"
        # closest titles first
        assert [s["id"] for s in result.suggestions][-1] == "far"
        assert result.reason.startswith("closest: 尼克 - Find You (")

    def test_suggestions_are_capped(self):
        candidates = [
            track(f"Find You {i}", "X", duration=1, id=str(i)) for i in range(9)
        ]
        assert len(match_track(candidates, TARGET).suggestions) == 5

    def test_match_has_no_suggestions(self):
        result = match_track(
            [track("Find You", "Nick Jonas", duration=198, id="ok")], TARGET
        )
        assert result.id == "ok"
        assert result.suggestions == ()

    def test_album_and_artist_explanations(self):
        album = match_album(
            [{"id": "a", "name": "Other", "artist": "Nick Jonas"}],
            {"name": "Spaceman", "artist": "Nick Jonas"},
        )
        assert (
            album.id is None
            and album.suggestions[0]["reason"] == "different album name"
        )
        artist = match_artist(
            [{"id": "j", "name": "Joe Jonas"}], {"name": "Nick Jonas"}
        )
        assert artist.id is None and artist.suggestions[0]["reason"] == "different name"


class TestFalsePositivesFromRealRuns:
    def test_similar_title_same_artist_but_other_length_rejected(self):
        target = track("Some", "BOL4", duration=222, id=None)
        result = match_track([track("Someday", "BOL4", duration=166, id="x")], target)
        assert result.id is None
        assert result.suggestions[0]["reason"] == "length differs by 56s"

    def test_live_version_rejected_by_length(self):
        target = track("Want You Back", "5 Seconds of Summer", duration=173, id=None)
        candidates = [
            track(
                "Want You Back (Live)", "5 Seconds of Summer", duration=432, id="live"
            )
        ]
        assert best_track(candidates, target) is None

    def test_unrelated_uploader_rejected(self):
        target = track("追光者", "Ariel Tsai", duration=213, id=None)
        candidates = [track("追光者", "PED GT Studio", duration=228, id="x")]
        assert best_track(candidates, target) is None

    def test_extra_featured_artist_still_matches_when_length_agrees(self):
        target = track("Dear Alcohol", "Dax", duration=236, id=None)
        candidates = [track("Dear Alcohol", "Dax Elle King", duration=237, id="ok")]
        assert best_track(candidates, target) == "ok"

    def test_artist_word_overlap_matches_multi_artist_credits(self):
        target = track(
            "Enemy",
            "Imagine Dragons JID Arcane League of Legends",
            duration=173,
            id=None,
        )
        candidates = [track("Enemy", "Imagine Dragons & JID", duration=174, id="ok")]
        assert best_track(candidates, target) == "ok"

    def test_long_tracks_allow_proportional_tolerance(self):
        target = track("Epic Mix", "DJ", duration=600, id=None)
        assert (
            best_track([track("Epic Mix", "DJ", duration=626, id="ok")], target) == "ok"
        )

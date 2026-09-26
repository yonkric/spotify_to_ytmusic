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
    def test_artist_in_other_script_accepted_when_service_confirms_identity(self):
        target = track("告白氣球", "Jay Chou", duration=215, id=None)
        candidates = [
            {
                **track("告白氣球", "周杰倫", duration=216, id="right"),
                "artist_ids": ["UCjay"],
            }
        ]
        assert match_track(candidates, target, frozenset({"UCjay"})).id == "right"

    def test_other_script_without_confirmed_identity_rejected(self):
        target = track("告白氣球", "Jay Chou", duration=215, id=None)
        candidates = [track("告白氣球", "周杰倫", duration=216, id="right")]
        assert best_track(candidates, target) is None

    def test_same_length_cover_by_other_channel_rejected(self):
        target = track("演員", "Joker Xue", duration=261, id=None)
        candidates = [
            {
                **track("演員", "吳業坤", duration=263, id="cover"),
                "artist_ids": ["UCcover"],
            }
        ]
        assert match_track(candidates, target, frozenset({"UCxue"})).id is None

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

    def test_same_script_alias_goes_to_review(self):
        # "7!!" / "seven oops" is the same band, but two Latin names that share
        # nothing look exactly like a cover; the user confirms it in review
        target = track("オレンジ", "7!!", duration=350, id=None)
        candidates = [
            track("オレンジ - Orange", "seven oops", duration=351, id="right")
        ]
        result = match_track(candidates, target)
        assert result.id is None
        assert result.suggestions[0]["id"] == "right"


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

    def test_five_second_difference_accepted_for_confirmed_artist(self):
        target = track("只是太愛你", "Hins Cheung", duration=254, id=None)
        candidates = [
            {
                **track("只是太愛你", "張敬軒", duration=249, id="right"),
                "artist_ids": ["UChins"],
            }
        ]
        assert match_track(candidates, target, frozenset({"UChins"})).id == "right"


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
        assert reasons["nolen"] == "different artist"
        assert reasons["far"] == "different title"
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


class TestSecondAuditFalsePositives:
    def test_numbers_and_filler_words_dont_make_artists_equal(self):
        target = track("Want You Back", "5 Seconds of Summer", duration=173, id=None)
        candidates = [track("I Want You Back", "Jackson 5", duration=173, id="j5")]
        assert best_track(candidates, target) is None

    def test_same_script_cover_of_same_length_rejected(self):
        target = track("Some", "BOL4", duration=184, id=None)
        candidates = [track("Some", "Shin Giwon Piano", duration=184, id="piano")]
        assert best_track(candidates, target) is None

    def test_traditional_simplified_artist_confirmed_by_identity(self):
        target = track("他不懂", "張杰", duration=240, id=None)
        candidates = [
            {**track("他不懂", "张杰", duration=241, id="ok"), "artist_ids": ["UCzj"]}
        ]
        assert match_track(candidates, target, frozenset({"UCzj"})).id == "ok"


class TestInstrumentalAndCoverUploads:
    TARGET = track("なんでもないや - movie ver.", "RADWIMPS", duration=344, id=None)

    def test_same_artist_instrumental_rejected(self):
        for title in [
            "なんでもないや (movie ver.) (Instrumental)",
            "なんでもないや (movie ver.) [Off Vocal]",
            "なんでもないや (movie ver.) カラオケ",
            "なんでもないや (movie ver.) - インスト",
            "なんでもないや【歌ってみた】",
        ]:
            candidates = [track(title, "RADWIMPS", duration=344, id="x")]
            result = match_track(candidates, self.TARGET)
            assert result.id is None, title
            assert (
                result.suggestions[0]["reason"] == "live/instrumental/karaoke/cover"
            ), title

    def test_instrumental_allowed_when_spotify_track_is_instrumental(self):
        target = track(
            "なんでもないや (Instrumental)", "RADWIMPS", duration=344, id=None
        )
        candidates = [
            track("なんでもないや (Instrumental)", "RADWIMPS", duration=344, id="ok")
        ]
        assert best_track(candidates, target) == "ok"

    def test_movie_version_is_not_a_cover(self):
        candidates = [
            track("なんでもないや (movie ver.)", "RADWIMPS", duration=344, id="ok")
        ]
        assert best_track(candidates, self.TARGET) == "ok"


class TestNoDurationResults:
    def test_other_song_with_shared_suffix_rejected_without_length(self):
        target = track("なんでもないや - movie ver.", "RADWIMPS", duration=344, id=None)
        candidates = [track("Sparkle - movie ver.", "RADWIMPS", duration=None, id="x")]
        result = match_track(candidates, target)
        assert result.id is None
        assert result.suggestions[0]["reason"] == "no length to confirm"

    def test_exact_title_without_length_still_matches(self):
        target = track("Want You Back", "5 Seconds of Summer", duration=173, id=None)
        candidates = [
            track("Want You Back", "5 Seconds of Summer", duration=None, id="ok")
        ]
        assert best_track(candidates, target) == "ok"

    def test_live_version_rejected_even_without_length(self):
        target = track("Want You Back", "5 Seconds of Summer", duration=173, id=None)
        for title in [
            "Want You Back (Live)",
            "Want You Back - Live",
            "Want You Back [LIVE]",
        ]:
            candidates = [track(title, "5 Seconds of Summer", duration=None, id="live")]
            assert best_track(candidates, target) is None, title

    def test_live_in_song_name_is_fine(self):
        target = track("Live While We're Young", "One Direction", duration=200, id=None)
        candidates = [
            track("Live While We're Young", "One Direction", duration=200, id="ok")
        ]
        assert best_track(candidates, target) == "ok"


class TestExplicitMarkers:
    def test_explicit_version_suffix_is_the_same_song(self):
        target = track("Seven (feat. Latto)", "Jung Kook Latto", duration=184, id=None)
        candidates = [
            track(
                "Seven - Explicit Ver. (feat. Latto)",
                "Jung Kook",
                duration=185,
                id="ok",
            )
        ]
        assert best_track(candidates, target) == "ok"

    def test_other_explicit_spellings(self):
        for title in [
            "Seven (Explicit)",
            "Seven [Explicit Version]",
            "Seven - Explicit",
        ]:
            assert clean_title(title) == "seven", title

    def test_band_version_is_still_different(self):
        target = track("Seven (feat. Latto)", "Jung Kook Latto", duration=184, id=None)
        candidates = [
            track(
                "Seven - Band Ver. (feat. Latto)", "Jung Kook", duration=190, id="band"
            )
        ]
        assert best_track(candidates, target) is None


class TestOriginalMix:
    def test_original_mix_is_the_standard_version(self):
        assert clean_title("Tsunami - Original Mix") == "tsunami"
        assert clean_title("Tremor (Original Mix)") == "tremor"

    def test_search_title_drops_markers_but_keeps_case(self):
        from spotify_to_ytmusic.sync.matching import search_title

        assert search_title("Tsunami - Original Mix") == "Tsunami"
        assert search_title("Seven (feat. Latto)") == "Seven"
        assert (
            search_title("なんでもないや - movie ver.") == "なんでもないや - movie ver."
        )


class TestSuggestionRanking:
    def test_same_artist_close_length_ranks_first(self):
        target = track(
            "LUA NA PRAÇA - Slowed", "Dj Samir DJ Zarek", duration=101, id=None
        )
        candidates = [
            track(
                "LUA NA PRAÇA (Ultra Slowed)",
                "AGRESSIVE PHONK",
                duration=169,
                id="phonk",
            ),
            track("LUA NA PRAÇA [ULTRA SLOWED]", "ATLXS", duration=195, id="atlxs"),
            track("LUA NA PRAÇA", "DJ Samir", duration=89, id="original"),
        ]
        result = match_track(candidates, target)
        assert result.id is None
        assert result.suggestions[0]["id"] == "original"

    def test_search_order_breaks_ties(self):
        target = track("Song", "A", duration=100, id=None)
        candidates = [
            {**track("Song", "B", duration=150, id="first"), "rank": 0},
            {**track("Song", "B", duration=150, id="second"), "rank": 3},
        ]
        assert match_track(candidates, target).suggestions[0]["id"] == "first"


class TestCoverStyleSources:
    def test_cover_allowed_when_source_is_itself_a_version_of_another_song(self):
        target = track(
            "Sisqo Thong Song - 1950s Motown Choir Version",
            "gino",
            duration=180,
            id=None,
        )
        candidates = [
            track(
                "Thong Song (1950s Motown Choir Cover Version - Remastered)",
                "The Classic Soul Choir",
                duration=186,
                id="choir",
            )
        ]
        result = match_track(candidates, target)
        assert result.suggestions[0]["id"] == "choir"
        assert "cover" not in result.suggestions[0]["reason"]

    def test_instrumental_still_rejected_for_version_sources(self):
        target = track("Thong Song - Choir Version", "gino", duration=180, id=None)
        candidates = [
            track(
                "Thong Song - Choir Version (Instrumental)",
                "gino",
                duration=180,
                id="x",
            )
        ]
        assert best_track(candidates, target) is None


class TestMixedSuggestions:
    def test_suggestions_cover_title_length_artist_and_top_result(self):
        target = track("At a Medium Pace", "Done Again", duration=120, id=None)
        candidates = [
            {
                **track("Ode to My Car", "Adam Sandler", duration=236, id="top"),
                "rank": 0,
            },
            {
                **track("The Longest Pee", "Adam Sandler", duration=136, id="b"),
                "rank": 1,
            },
            {
                **track("Toll Booth Willie", "Adam Sandler", duration=229, id="c"),
                "rank": 2,
            },
            {**track("Mayor", "Adam Sandler", duration=228, id="d"), "rank": 3},
            {**track("Pace", "Adam Sandler", duration=300, id="e"), "rank": 4},
            {**track("Something", "Other", duration=121, id="len"), "rank": 5},
            {
                **track("At a Medium Pace", "Adam Sandler", duration=200, id="title"),
                "rank": 6,
            },
            {**track("Nope", "Done Again", duration=400, id="artist"), "rank": 7},
        ]
        ids = [s["id"] for s in match_track(candidates, target).suggestions]
        assert len(ids) == 5
        assert {"title", "len", "artist", "top"} <= set(ids)
        assert ids[0] == "title"


class TestKaraokeSourcesAndDuplicates:
    def test_karaoke_allowed_when_source_is_a_performance_track(self):
        target = track(
            "At a Medium Pace (In the Style of Adam Sandler) [Performance Track]",
            "Done Again",
            duration=184,
            id=None,
        )
        candidates = [
            track(
                "At A Medium Pace (Karaoke Demonstration With Lead Vocal)",
                "Stingray Music Karaoke",
                duration=186,
                id="k",
            )
        ]
        assert "karaoke" not in match_track(candidates, target).suggestions[0]["reason"]

    def test_identical_uploads_are_suggested_once(self):
        target = track("Song", "A", duration=100, id=None)
        candidates = [
            track("Song", "B", duration=200, id="1"),
            track("Song", "B", duration=200, id="2"),
            track("Song", "C", duration=150, id="3"),
        ]
        ids = [s["id"] for s in match_track(candidates, target).suggestions]
        assert len(ids) == 2 and "3" in ids


class TestTraditionalSimplified:
    def test_traditional_and_simplified_are_the_same_title_and_artist(self):
        target = track("丟了你", "井朧", duration=277, id=None)
        candidates = [track("丢了你", "井胧", duration=278, id="ok")]
        assert best_track(candidates, target) == "ok"

    def test_mixed_scripts_in_title(self):
        target = track("剛好遇見你", "李玉剛", duration=201, id=None)
        candidates = [track("刚好遇见你", "李玉刚", duration=202, id="ok")]
        assert best_track(candidates, target) == "ok"

    def test_artist_match_across_scripts_but_other_singer_still_rejected(self):
        target = track("星辰大海", "黃霄雲", duration=207, id=None)
        candidates = [track("星辰大海", "某某", duration=208, id="cover")]
        assert best_track(candidates, target) is None

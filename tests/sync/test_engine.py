import pytest

from spotify_to_ytmusic.sync.engine import Selection, apply_choices, run_transfer
from spotify_to_ytmusic.sync.matching import Match


def t(name, artist="A"):
    return {
        "id": f"src-{name}",
        "name": name,
        "artist": artist,
        "album": "",
        "duration": 100.0,
    }


class FakeLibrary:
    name = "Fake"

    def __init__(
        self, playlists=None, liked=None, albums=None, artists=None, known=None
    ):
        self.playlists = playlists or {}  # id -> (name, [tracks])
        self.liked = liked or []
        self.albums = albums or []
        self.artists = artists or []
        self.known = known or {}  # track name -> dest id
        self.suggest = {}  # track name -> list of suggested ids
        self.created = {}
        self.added = {}
        self.liked_ids = []
        self.saved_albums = []
        self.followed = []
        self.searches = 0

    def list_playlists(self):
        return [
            {"id": pid, "name": n, "count": len(ts)}
            for pid, (n, ts) in self.playlists.items()
        ]

    @staticmethod
    def url(kind, item_id):
        return f"https://listen.example/{kind}/{item_id}"

    def get_playlist_tracks(self, pid):
        return self.playlists[pid][1]

    def get_liked_tracks(self):
        return self.liked

    def get_albums(self):
        return self.albums

    def get_artists(self):
        return self.artists

    def _find(self, item):
        if item["name"] in self.known:
            return Match(self.known[item["name"]])
        options = self.suggest.get(item["name"], [])
        suggestions = tuple(
            {"id": i, "name": i, "artist": "", "reason": "different artist"}
            for i in options
        )
        return Match(None, "closest: ..." if options else "no results", suggestions)

    def find_track(self, track):
        self.searches += 1
        return self._find(track)

    def find_album(self, album):
        return self._find(album)

    def find_artist(self, artist):
        return self._find(artist)

    def get_or_create_playlist(self, name, description):
        for pid, (n, ts) in self.playlists.items():
            if n == name:
                return pid, {x["id"] for x in ts}
        self.created[name] = description
        self.playlists[f"new-{name}"] = (f"_created {name}", [])
        return f"new-{name}", set()

    def add_to_playlist(self, pid, ids):
        self.added.setdefault(pid, []).extend(ids)
        if pid in self.playlists:
            self.playlists[pid][1].extend({"id": i} for i in ids)

    def like_tracks(self, ids):
        self.liked_ids.extend(ids)

    def save_albums(self, ids):
        self.saved_albums.extend(ids)

    def follow_artists(self, ids):
        self.followed.extend(ids)


def run(source, dest, selection, cache=None):
    events = []
    report = run_transfer(
        source,
        dest,
        selection,
        lambda *e: events.append(e),
        cache if cache is not None else {},
    )
    return report, events


def test_playlist_creates_destination_and_reports_missing():
    source = FakeLibrary(
        playlists={"p1": ("Road trip", [t("One"), t("Two"), t("Gone")])}
    )
    dest = FakeLibrary(known={"One": "d1", "Two": "d2"})

    report, events = run(source, dest, Selection(playlist_ids=["p1"]))

    assert dest.created == {"Road trip": "Transferred from Fake"}
    assert dest.added == {"new-Road trip": ["d1", "d2"]}
    [section] = report
    assert (
        section["label"],
        section["total"],
        section["matched"],
        section["added"],
    ) == (
        "Playlist: Road trip",
        3,
        2,
        2,
    )
    assert section["target"] == {"type": "playlist", "id": "new-Road trip"}
    assert [(nf["label"], nf["reason"]) for nf in section["not_found"]] == [
        ("A - Gone", "no results")
    ]
    assert events[-1][1:] == (3, 3)


def test_rerun_only_adds_missing_songs_and_dedupes():
    source = FakeLibrary(
        playlists={"p1": ("Mix", [t("One"), t("Two"), t("Two again")])}
    )
    dest = FakeLibrary(
        playlists={"d-mix": ("Mix", [{"id": "d1"}])},
        known={"One": "d1", "Two": "d2", "Two again": "d2"},
    )

    report, _ = run(source, dest, Selection(playlist_ids=["p1"]))

    assert dest.created == {}
    assert dest.added == {"d-mix": ["d2"]}
    assert report[0]["added"] == 1


def test_cache_skips_repeat_searches():
    source = FakeLibrary(playlists={"p1": ("Mix", [t("One")])})
    dest = FakeLibrary(known={"One": "d1"})
    cache = {}

    run(source, dest, Selection(playlist_ids=["p1"]), cache)
    run(source, FakeLibrary(), Selection(playlist_ids=["p1"]), cache)

    assert dest.searches == 1
    assert list(cache.values()) == ["d1"]


def test_liked_albums_artists_skip_what_destination_already_has():
    source = FakeLibrary(
        liked=[t("One"), t("Two")],
        albums=[
            {"id": "sa", "name": "LP", "artist": "A"},
            {"id": "sb", "name": "EP", "artist": "A"},
        ],
        artists=[{"id": "x", "name": "Band"}],
    )
    dest = FakeLibrary(
        liked=[{"id": "d1"}],
        albums=[{"id": "d-lp"}],
        known={"One": "d1", "Two": "d2", "LP": "d-lp", "EP": "d-ep", "Band": "d-band"},
    )

    report, _ = run(source, dest, Selection(liked=True, albums=True, artists=True))

    assert dest.liked_ids == ["d2"]
    assert dest.saved_albums == ["d-ep"]
    assert dest.followed == ["d-band"]
    assert [r["label"] for r in report] == [
        "Liked songs",
        "Saved albums",
        "Followed artists",
    ]
    assert [r["added"] for r in report] == [1, 1, 1]


def _section_with_suggestions():
    source = FakeLibrary(
        playlists={"p1": ("Mix", [t("One"), t("Two")])}, liked=[t("Three")]
    )
    dest = FakeLibrary()
    dest.suggest = {"One": ["o1", "o2"], "Two": ["w1"], "Three": ["h1"]}
    cache = {}
    report, _ = run(source, dest, Selection(playlist_ids=["p1"], liked=True), cache)
    return dest, report, cache


def test_not_found_items_carry_suggestions():
    _, report, _ = _section_with_suggestions()
    nf = report[0]["not_found"][0]
    assert nf["label"] == "A - One"
    assert [s["id"] for s in nf["suggestions"]] == ["o1", "o2"]


def test_apply_choices_adds_to_playlist_and_remembers():
    dest, report, cache = _section_with_suggestions()
    section = apply_choices(dest, report[0], {0: "o2"}, cache)

    assert dest.added["new-Mix"] == ["o2"]
    assert (section["added"], section["matched"]) == (1, 1)
    assert [nf["label"] for nf in section["not_found"]] == ["A - Two"]
    assert "o2" in cache.values()
    assert report[0]["added"] == 0  # original section untouched


def test_apply_choices_likes_for_liked_section():
    dest, report, cache = _section_with_suggestions()
    apply_choices(dest, report[1], {0: "h1"}, cache)
    assert dest.liked_ids == ["h1"]


def test_apply_choices_rejects_ids_that_were_not_offered():
    dest, report, cache = _section_with_suggestions()
    with pytest.raises(ValueError, match="not offered"):
        apply_choices(dest, report[0], {0: "evil"}, cache)
    with pytest.raises(ValueError, match="No unmatched item"):
        apply_choices(dest, report[0], {7: "o1"}, cache)
    assert dest.added["new-Mix"] == []

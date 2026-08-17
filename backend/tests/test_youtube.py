"""
YouTube URL and caption parsing — the SSRF guard and the format that breaks.

Offline by construction: `domain/youtube.py` is pure, which is the whole point
of putting these two things there. No network in this suite, ever.
"""

from __future__ import annotations

import pytest

from app.domain.youtube import (
    InvalidVideoUrl,
    TranscriptCue,
    cues_to_text,
    group_cues,
    parse_chapters,
    parse_json3,
    parse_video_id,
)

_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={_ID}",
        f"https://youtube.com/watch?v={_ID}&t=42s",
        f"https://m.youtube.com/watch?v={_ID}",
        f"https://music.youtube.com/watch?v={_ID}",
        f"https://youtu.be/{_ID}",
        f"https://youtu.be/{_ID}?si=abcdef",
        f"https://www.youtube.com/shorts/{_ID}",
        f"https://www.youtube.com/embed/{_ID}",
        f"https://www.youtube.com/live/{_ID}",
        f"www.youtube.com/watch?v={_ID}",  # scheme-less, as pasted from a browser
    ],
)
def test_every_shape_a_person_actually_pastes(url: str) -> None:
    assert parse_video_id(url) == _ID


# ── The SSRF guard ───────────────────────────────────────────────────────────
#
# This endpoint fetches server-side from a user-supplied string, so these are
# not edge cases — they are the attack.


@pytest.mark.parametrize(
    "url",
    [
        # The two that defeat a regex over the whole URL rather than a parsed
        # hostname. Both contain the literal "youtube.com".
        f"https://youtube.com.evil.test/watch?v={_ID}",
        f"https://evil.test/watch?v={_ID}&host=youtube.com",
        # Cloud metadata and loopback — the classic SSRF targets.
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/api/voices",
        "http://127.0.0.1/",
        "http://[::1]/",
        # Non-http schemes that a fetcher might otherwise honour.
        "file:///etc/passwd",
        f"ftp://youtube.com/watch?v={_ID}",
        # Credentials in the authority: `urlsplit().hostname` correctly returns
        # evil.test here, which is exactly why hostname is what gets checked.
        f"https://www.youtube.com@evil.test/watch?v={_ID}",
    ],
)
def test_only_youtube_hosts_are_ever_accepted(url: str) -> None:
    with pytest.raises(InvalidVideoUrl):
        parse_video_id(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=tooshort",
        "https://www.youtube.com/watch?v=waaaaaaaaaaaaytoolong",
        "https://www.youtube.com/watch?v=has+bad+chars",
        "https://www.youtube.com/results?search_query=urdu",
        _ID,  # a bare id is NOT a URL, and accepting one weakens the guard
    ],
)
def test_anything_without_a_real_video_id_is_rejected(url: str) -> None:
    with pytest.raises(InvalidVideoUrl):
        parse_video_id(url)


# ── json3 parsing ────────────────────────────────────────────────────────────


def test_parse_json3_reads_text_and_timings() -> None:
    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hello "}, {"utf8": "there."}]},
            {"tStartMs": 1500, "dDurationMs": 2000, "segs": [{"utf8": "How are you?"}]},
        ]
    }
    cues = parse_json3(payload)
    assert [c.text for c in cues] == ["Hello there.", "How are you?"]
    assert cues[1].start_sec == 1.5
    assert cues[1].duration_sec == 2.0


def test_parse_json3_skips_padding_and_newline_only_events() -> None:
    """Real tracks are full of these: timing events with no `segs`, and events
    whose only content is the format's own line break."""
    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 100},                       # no segs
            {"tStartMs": 100, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 200, "dDurationMs": 100, "segs": [{"utf8": "Real."}]},
        ]
    }
    assert [c.text for c in parse_json3(payload)] == ["Real."]


def test_parse_json3_degrades_to_empty_rather_than_raising() -> None:
    """This is the one part that breaks when YouTube changes format. It should
    return nothing and let the caller say so, not 500."""
    assert parse_json3({}) == []
    assert parse_json3({"events": "not a list"}) == []
    assert parse_json3({"events": [None, 42, {"segs": "nope"}]}) == []


# ── Cue joining ──────────────────────────────────────────────────────────────


def test_a_sentence_split_across_cues_is_rejoined_on_one_line() -> None:
    """
    THE POINT OF THIS FUNCTION. Caption cues break wherever the line got long
    enough, not at sentence ends. Joining them with newlines would put a
    paragraph break mid-sentence — and `direction_analyze` now gives a newline
    the longest pause there is, so that is a ~380 ms silence inside a clause.
    """
    cues = [
        TranscriptCue("Kal office mein", 0.0, 1.0),
        TranscriptCue("meeting hai aur", 1.0, 1.0),
        TranscriptCue("report bhi deni hai.", 2.0, 1.0),
        TranscriptCue("Phir ghar jaunga.", 3.0, 1.0),
    ]
    assert cues_to_text(cues) == (
        "Kal office mein meeting hai aur report bhi deni hai.\nPhir ghar jaunga."
    )


def test_urdu_and_hindi_sentence_ends_are_recognised() -> None:
    """`۔` and `।` end sentences in Perso-Arabic and Devanagari. Treating only
    `.` as terminal runs a whole Hindi transcript into one line."""
    urdu = cues_to_text([
        TranscriptCue("کل میٹنگ ہے۔", 0.0, 1.0),
        TranscriptCue("پھر گھر جاؤں گا۔", 1.0, 1.0),
    ])
    hindi = cues_to_text([
        TranscriptCue("कल मीटिंग है।", 0.0, 1.0),
        TranscriptCue("फिर घर जाऊंगा।", 1.0, 1.0),
    ])
    assert urdu.count("\n") == 1
    assert hindi.count("\n") == 1


def test_a_transcript_that_never_terminates_is_still_one_line() -> None:
    """Auto-generated captions frequently carry no punctuation at all."""
    cues = [TranscriptCue(f"word{i}", float(i), 1.0) for i in range(5)]
    out = cues_to_text(cues)
    assert "\n" not in out
    assert out == "word0 word1 word2 word3 word4"


def test_empty_input_is_empty_output() -> None:
    assert cues_to_text([]) == ""


# ── Chapters ─────────────────────────────────────────────────────────────────
# Grouping cues by chapter is what lets a part never straddle a chapter
# boundary. All of it is pure, so all of it is tested here rather than through
# the router.


def test_parse_chapters_degrades_to_empty_rather_than_raising() -> None:
    """Same contract as `parse_json3` above, for the same reason: yt-dlp's
    `chapters` is third-party data whose shape is not ours."""
    assert parse_chapters(None) == []
    assert parse_chapters([]) == []
    assert parse_chapters("not a list") == []
    assert parse_chapters([None, 42, "x"]) == []
    # An entry with no usable start is skipped, not guessed at.
    assert parse_chapters([{"title": "No start"}]) == []
    assert parse_chapters([{"start_time": "abc", "title": "Bad"}]) == []


def test_parse_chapters_sorts_by_start_and_names_the_unnamed() -> None:
    """yt-dlp does not promise an order, and `group_cues`'s boundary search
    assumes one. A chapter with no title is still a real division of the video
    — dropping it would silently merge it into its neighbour."""
    chapters = parse_chapters([
        {"start_time": 30, "end_time": 60, "title": "Second"},
        {"start_time": 0, "end_time": 30},
        {"start_time": 60, "title": "  "},
    ])
    assert [c.start_sec for c in chapters] == [0.0, 30.0, 60.0]
    assert [c.index for c in chapters] == [0, 1, 2]
    assert chapters[0].title == "Chapter 1"
    assert chapters[1].title == "Second"
    assert chapters[2].title == "Chapter 3"
    # `end_time` is optional and kept only for display.
    assert chapters[2].end_sec is None


def test_a_missing_end_time_does_not_affect_grouping() -> None:
    """Boundaries come from `start_sec` alone. yt-dlp emits chapters with gaps,
    with overlaps, and with no `end_time` at all — one rule handles all three
    where three rules would disagree."""
    chapters = parse_chapters([
        {"start_time": 0, "end_time": 5, "title": "A"},   # ends at 5...
        {"start_time": 10, "title": "B"},                  # ...but B starts at 10
    ])
    # A cue at 7 is in the GAP. It belongs to A, because A runs until B starts.
    groups = group_cues([TranscriptCue("gap", 7.0, 1.0)], chapters)
    assert [g[0].title for g in groups] == ["A"]


def test_no_chapters_returns_exactly_one_group_holding_everything() -> None:
    """
    THE INVARIANT THE WHOLE FEATURE RESTS ON, asserted on the object rather
    than inferred from downstream text.

    A chapter-less video must produce byte-identical output to before chapters
    existed, and every downstream difference follows from the number of groups.
    One group in means one `cues_to_text` call, which means no join, which
    means no injected newline — and a newline is ~380 ms of real silence.
    """
    cues = [TranscriptCue("a", 0.0, 1.0), TranscriptCue("b", 5.0, 1.0)]
    assert group_cues(cues, []) == [(None, cues)]


def test_a_cue_on_a_boundary_belongs_to_the_later_chapter() -> None:
    """Half-open spans: `[start, next start)`. Stated as a test because "which
    side of the boundary" is exactly the kind of thing that gets flipped by a
    later refactor with nothing to catch it."""
    chapters = parse_chapters([
        {"start_time": 0, "title": "First"},
        {"start_time": 10, "title": "Second"},
    ])
    groups = group_cues([TranscriptCue("edge", 10.0, 1.0)], chapters)
    assert [g[0].title for g in groups] == ["Second"]


def test_cues_before_the_first_chapter_get_a_leading_unnamed_group() -> None:
    """YouTube requires a chapter at 00:00, but description-derived chapters do
    not, so this is reachable. It renders exactly as the no-chapters case."""
    chapters = parse_chapters([{"start_time": 20, "title": "Late start"}])
    groups = group_cues(
        [TranscriptCue("intro", 0.0, 1.0), TranscriptCue("body", 25.0, 1.0)],
        chapters,
    )
    assert groups[0][0] is None
    assert [c.text for c in groups[0][1]] == ["intro"]
    assert groups[1][0] is not None
    assert groups[1][0].title == "Late start"


def test_a_chapter_with_no_cues_is_dropped_not_returned_empty() -> None:
    """An empty group renders as a heading with nothing under it, which reads
    as a broken transcript rather than a quiet stretch of video."""
    chapters = parse_chapters([
        {"start_time": 0, "title": "Talking"},
        {"start_time": 100, "title": "Silent montage"},
    ])
    groups = group_cues([TranscriptCue("hello", 1.0, 1.0)], chapters)
    assert [g[0].title for g in groups] == ["Talking"]


def test_every_cue_survives_grouping_exactly_once() -> None:
    """Grouping partitions; it must not drop or duplicate. Cheap to assert and
    the failure would be near-invisible in a long transcript."""
    cues = [TranscriptCue(f"c{i}", float(i * 5), 1.0) for i in range(12)]
    chapters = parse_chapters([
        {"start_time": 0, "title": "A"},
        {"start_time": 20, "title": "B"},
        {"start_time": 40, "title": "C"},
    ])
    regrouped = [cue for _, group in group_cues(cues, chapters) for cue in group]
    assert regrouped == cues

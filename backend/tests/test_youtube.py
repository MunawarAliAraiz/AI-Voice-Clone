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

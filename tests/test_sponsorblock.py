import json
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP

from obsidian_ingest.extractor.metadata import ExcludedRange, ExclusionStatus
from obsidian_ingest.extractor.providers.youtube.sponsorblock import (
    DEFAULT_DISCARD_CATEGORIES,
    _StrictSponsorBlockPP,
    fetch_exclusions,
    normalize_exclusions,
)


def chapter(category="sponsor", start=10, end=20, action="skip"):
    return {"category": category, "start_time": start, "end_time": end, "type": action}


def video_info():
    return {"id": "video-id", "extractor_key": "Youtube", "duration": 100}


@pytest.fixture
def ydl():
    # No test may accidentally contact a live service.
    with (
        YoutubeDL({"quiet": True, "no_warnings": True, "extractor_retries": 0}) as downloader,
        patch.object(downloader, "urlopen", side_effect=AssertionError("Unexpected network")),
    ):
        yield downloader


def test_explicit_pp_invocation_uses_existing_downloader_and_info(ydl):
    info = video_info() | {"chapters": [{"title": "Native"}], "sponsorblock_chapters": [chapter()]}
    original = info.copy()
    real_init = SponsorBlockPP.__init__
    real_run = SponsorBlockPP.run
    calls = []

    def init(pp, downloader, categories):
        calls.append((downloader, categories))
        real_init(pp, downloader, categories=categories)

    with (
        patch.object(SponsorBlockPP, "__init__", init),
        patch.object(SponsorBlockPP, "run", autospec=True, side_effect=real_run) as run,
        patch.object(SponsorBlockPP, "_get_sponsor_segments", return_value=[]) as segments,
    ):
        result = fetch_exclusions(ydl, info)

    assert calls == [(ydl, DEFAULT_DISCARD_CATEGORIES)]
    run.assert_called_once()
    passed_info = run.call_args.args[1]
    assert passed_info is not info
    assert passed_info["id"] == info["id"]
    assert passed_info["chapters"] == info["chapters"]
    segments.assert_called_once_with("video-id", "YouTube")
    assert info == original
    assert result.status == ExclusionStatus.COMPLETE
    assert result.ranges == ()
    assert result.categories == ("sponsor",)
    assert result.provenance == "SponsorBlock"
    assert result.retrieved_at is not None
    assert result.retrieved_at.utcoffset().total_seconds() == 0


def test_disabled_bypasses_pp_even_without_duration(ydl):
    with patch.object(SponsorBlockPP, "__init__") as constructor:
        result = fetch_exclusions(ydl, {}, categories=())
    constructor.assert_not_called()
    assert result.status == ExclusionStatus.DISABLED
    assert result.ranges == result.categories == ()
    assert result.retrieved_at is None


def test_normalization_is_sponsor_only_and_intro_is_opt_in():
    chapters = [chapter(), chapter("intro"), chapter("outro"), chapter(action="poi")]
    assert normalize_exclusions(chapters) == (ExcludedRange(10.0, 20.0, "sponsor", "SponsorBlock"),)
    configured = normalize_exclusions(chapters, ("sponsor", "intro", "outro"))
    assert tuple(item.reason for item in configured) == ("sponsor", "intro", "outro")
    assert normalize_exclusions(chapters, ()) == ()


def test_overlaps_and_source_order_are_preserved():
    chapters = [chapter(start=15, end=30), chapter(start=10, end=20), chapter(start=15, end=30)]
    assert tuple((item.start, item.end) for item in normalize_exclusions(chapters)) == (
        (15.0, 30.0),
        (10.0, 20.0),
        (15.0, 30.0),
    )


@pytest.mark.parametrize(
    "start,end",
    [
        (-1, 10),
        (10, 10),
        (20, 10),
        (float("nan"), 20),
        (10, float("inf")),
        (float("-inf"), 20),
        (None, 20),
        (10, None),
        ("10", 20),
        (True, 20),
        (10, False),
        (10, "20"),
        (10**400, 20),
    ],
)
def test_invalid_selected_timing_raises(start, end):
    with pytest.raises((TypeError, ValueError), match="SponsorBlock"):
        normalize_exclusions([chapter(start=start, end=end)])


def test_unselected_or_non_skip_ranges_are_not_validated():
    assert normalize_exclusions([chapter("intro", None, None), chapter(action="chapter")]) == ()


@pytest.mark.parametrize("category", ["unknown", "all", "default", "poi_highlight", "chapter"])
def test_invalid_categories_rejected_before_pp(ydl, category):
    with (
        patch.object(SponsorBlockPP, "__init__") as constructor,
        pytest.raises(ValueError, match="skip category"),
    ):
        fetch_exclusions(ydl, video_info(), (category,))
    constructor.assert_not_called()
    with pytest.raises(ValueError, match="skip category"):
        normalize_exclusions([], (category,))


@pytest.mark.parametrize("duration", [None, 0, -1, "100", True, float("nan"), float("inf")])
def test_missing_or_invalid_duration_fails_before_pp(ydl, duration):
    info = video_info()
    if duration is None:
        del info["duration"]
    else:
        info["duration"] = duration
    with (
        patch.object(SponsorBlockPP, "__init__") as constructor,
        pytest.raises((TypeError, ValueError), match="duration"),
    ):
        fetch_exclusions(ydl, info)
    constructor.assert_not_called()


def test_successful_api_response_passes_through_real_pp(ydl):
    response = Mock()
    response.headers.get_param.return_value = None
    response.read.return_value = json.dumps(
        [
            {
                "videoID": "video-id",
                "segments": [
                    {
                        "segment": [10, 20],
                        "category": "sponsor",
                        "actionType": "skip",
                        "videoDuration": 100,
                    }
                ],
            }
        ]
    ).encode()
    with patch.object(ydl, "urlopen", return_value=response) as request:
        result = fetch_exclusions(ydl, video_info())
    assert result.ranges == (ExcludedRange(10, 20, "sponsor", "SponsorBlock"),)
    assert result.status == ExclusionStatus.COMPLETE
    assert "categories=%5B%22sponsor%22%5D" in request.call_args.args[0].url


@pytest.mark.parametrize(
    "interval",
    [
        [-10, 20],
        [False, 20],
        [10, float("inf")],
        [float("nan"), 20],
        [20, 10],
        [10, 10],
        [None, 20],
        [10],
    ],
)
def test_raw_invalid_intervals_fail_before_yt_dlp_can_clamp_them(ydl, interval):
    response = Mock()
    response.headers.get_param.return_value = None
    response.read.return_value = json.dumps(
        [
            {
                "videoID": "video-id",
                "segments": [
                    {
                        "segment": interval,
                        "category": "sponsor",
                        "actionType": "skip",
                        "videoDuration": 100,
                    }
                ],
            }
        ]
    ).encode()
    with (
        patch.object(ydl, "urlopen", return_value=response),
        pytest.raises(RuntimeError, match="SponsorBlock lookup failed"),
    ):
        fetch_exclusions(ydl, video_info())


def test_successful_empty_api_response_is_complete(ydl):
    response = Mock()
    response.headers.get_param.return_value = None
    response.read.return_value = b"[]"
    with patch.object(ydl, "urlopen", return_value=response):
        result = fetch_exclusions(ydl, video_info())
    assert result.status == ExclusionStatus.COMPLETE
    assert result.ranges == ()


@pytest.mark.parametrize("body", [b"null", b"{}", b"not json"])
def test_malformed_response_is_not_complete(ydl, body):
    response = Mock()
    response.headers.get_param.return_value = None
    response.read.return_value = body
    with (
        patch.object(ydl, "urlopen", return_value=response),
        pytest.raises(RuntimeError, match="SponsorBlock lookup failed"),
    ):
        fetch_exclusions(ydl, video_info())


def test_network_warning_raises_even_when_downloader_suppresses_warnings(ydl):
    with (
        patch.object(ydl, "urlopen", side_effect=TransportError("offline")),
        pytest.raises(RuntimeError, match="SponsorBlock lookup failed.*offline"),
    ):
        fetch_exclusions(ydl, video_info())


@pytest.mark.parametrize("status", [429, 500])
def test_http_errors_are_not_complete_empty_results(ydl, status):
    error = HTTPError(Response(BytesIO(), "https://sponsor.ajay.app", {}, status=status))
    with (
        patch.object(ydl, "urlopen", side_effect=error),
        pytest.raises(RuntimeError, match=f"SponsorBlock lookup failed.*{status}"),
    ):
        fetch_exclusions(ydl, video_info())


def test_http_404_is_complete_no_matches(ydl):
    error = HTTPError(Response(BytesIO(), "https://sponsor.ajay.app", {}, status=404))
    with patch.object(ydl, "urlopen", side_effect=error):
        result = fetch_exclusions(ydl, video_info())
    assert result.status == ExclusionStatus.COMPLETE
    assert result.ranges == ()


def test_fetch_intro_requires_explicit_configuration(ydl):
    segments = [
        {
            "segment": [10, 20],
            "category": "intro",
            "actionType": "skip",
            "videoDuration": 100,
        }
    ]
    with patch.object(SponsorBlockPP, "_get_sponsor_segments", return_value=segments):
        assert fetch_exclusions(ydl, video_info()).ranges == ()
        configured = fetch_exclusions(ydl, video_info(), ("sponsor", "intro"))
    assert configured.categories == ("sponsor", "intro")
    assert configured.ranges == (ExcludedRange(10, 20, "intro", "SponsorBlock"),)


def test_duration_mismatch_fails_instead_of_returning_partial_ranges(ydl):
    segments = [
        {
            "segment": [10, 20],
            "category": "sponsor",
            "actionType": "skip",
            "videoDuration": duration,
        }
        for duration in (100, 150)
    ]
    with (
        patch.object(SponsorBlockPP, "_get_sponsor_segments", return_value=segments),
        pytest.raises(RuntimeError, match="different duration"),
    ):
        fetch_exclusions(ydl, video_info())


def test_missing_pp_results_cannot_reuse_stale_chapters(ydl):
    def run(pp, info):
        return [], info

    with (
        patch.object(_StrictSponsorBlockPP, "run", run),
        pytest.raises(RuntimeError, match="missing chapter results"),
    ):
        fetch_exclusions(ydl, video_info() | {"sponsorblock_chapters": [chapter()]})


@pytest.mark.parametrize("updates", [{"extractor_key": "Other"}, {"id": ""}])
def test_invalid_video_identity_fails(ydl, updates):
    with pytest.raises(ValueError, match="SponsorBlock"):
        fetch_exclusions(ydl, video_info() | updates)

import dataclasses
import importlib.util
import json
import pathlib

MODULE_PATH = pathlib.Path(__file__).parents[1] / "chalamet-sequence.py"
SPEC = importlib.util.spec_from_file_location("chalamet_sequence", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
chalamet_sequence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chalamet_sequence)


def make_play(
    *,
    start_time="2025-08-02T12:00:00Z",
    second_code="W",
    play_ids=("first-id", "second-id", "third-id"),
):
    return {
        "about": {"startTime": start_time},
        "matchup": {
            "batSide": {"code": "R"},
            "pitcher": {"fullName": "Pitcher Name"},
            "batter": {"fullName": "Batter Name"},
        },
        "pitchIndex": [0, 1, 2],
        "playEvents": [
            {
                "playId": play_ids[0],
                "pitchData": {"zone": 3},
            },
            {
                "playId": play_ids[1],
                "details": {"code": second_code},
            },
            {
                "playId": play_ids[2],
                "pitchData": {"startSpeed": 96.5, "zone": 1},
                "details": {"code": "C"},
            },
        ],
    }


def test_groups_all_criteria_by_pitch_and_counts_score():
    result = chalamet_sequence.build_play_result(make_play())

    assert isinstance(result, chalamet_sequence.PlayResult)
    assert result.score == 6
    assert result.pitches[0].criteria == {"outside": True}
    assert result.pitches[1].criteria == {"in_dirt": True, "chase": True}
    assert result.pitches[2].criteria == {
        "97_plus_mph": True,
        "inside": True,
        "called_strike": True,
    }

    for number, pitch in enumerate(result.pitches, start=1):
        assert isinstance(pitch, chalamet_sequence.PitchResult)
        assert pitch.number == number
        assert pitch.url == (
            "https://baseballsavant.mlb.com/sporty-videos?"
            f"playId={('first-id', 'second-id', 'third-id')[number - 1]}"
        )


def test_ordinary_swinging_strike_is_a_chase_but_not_in_dirt():
    result = chalamet_sequence.build_play_result(make_play(second_code="S"))

    assert result.pitches[1].criteria == {"in_dirt": False, "chase": True}
    assert result.score == 5


def test_missing_play_id_has_no_url():
    result = chalamet_sequence.build_play_result(
        make_play(play_ids=("first-id", None, "third-id"))
    )

    assert result.pitches[1].url is None


def test_uses_first_event_time_when_play_time_is_missing():
    play = make_play()
    play["about"].clear()
    play["playEvents"][0]["startTime"] = "2025-08-02T12:00:01Z"

    result = chalamet_sequence.build_play_result(play)

    assert result.start_time == "2025-08-02T12:00:01Z"


def test_allows_all_timestamps_to_be_missing():
    play = make_play()
    play["about"].clear()

    result = chalamet_sequence.build_play_result(play)

    assert result.start_time is None
    assert "unknown time - Pitcher Name to Batter Name" in (
        chalamet_sequence.format_human_results(result.score, [result])
    )
    document = json.loads(chalamet_sequence.format_json_results(result.score, [result]))
    assert document["plays"][0]["start_time"] is None


def test_human_output_shows_yes_and_no_for_every_criterion():
    play = make_play(second_code="S")
    play["playEvents"][0]["pitchData"]["zone"] = 2
    play["playEvents"][1].pop("playId")
    play["playEvents"][2]["pitchData"] = {
        "startSpeed": 90.0,
        "zone": 2,
    }
    play["playEvents"][2]["details"]["code"] = "S"
    result = chalamet_sequence.build_play_result(play)

    output = chalamet_sequence.format_human_results(1, [result])

    assert "best_score=1" in output
    assert "Outside: no" in output
    assert "In dirt: no" in output
    assert "Chase: yes" in output
    assert "97+ mph: no" in output
    assert "Inside: no" in output
    assert "Called strike: no" in output
    assert "URL: unavailable (missing playId)" in output


def test_json_output_matches_pipeline_contract():
    result = chalamet_sequence.build_play_result(make_play())

    output = chalamet_sequence.format_json_results(6, [result])
    document = json.loads(output)

    assert set(document) == {"best_score", "plays"}
    assert document["best_score"] == 6
    expected_play = json.loads(json.dumps(dataclasses.asdict(result)))
    assert document["plays"] == [expected_play]
    assert document["plays"][0]["pitches"][0]["criteria"]["outside"] is True
    assert "\n" not in output


def test_empty_json_output_is_valid():
    output = chalamet_sequence.format_json_results(0, [])

    assert json.loads(output) == {"best_score": 0, "plays": []}

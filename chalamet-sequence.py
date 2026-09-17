import argparse
import calendar
import datetime
import json
import pathlib
import pickle
import tempfile
from dataclasses import asdict, dataclass
from typing import Any

import statsapi
from tenacity import retry, wait_exponential
from tqdm import tqdm

CATCHER_LEFT = [1, 4, 7, 11, 13]
CATCHER_RIGHT = [3, 6, 9, 12, 14]
BALL_IN_DIRT_CODES = {"*B", "W"}
SWINGING_STRIKE_CODES = {"S", "W"}
SAVANT_VIDEO_URL = "https://baseballsavant.mlb.com/sporty-videos?playId={}"
CRITERION_LABELS = {
    "outside": "Outside",
    "in_dirt": "In dirt",
    "chase": "Chase",
    "97_plus_mph": "97+ mph",
    "inside": "Inside",
    "called_strike": "Called strike",
}

CACHE_DIR = pathlib.Path("pickles")


@dataclass(frozen=True, slots=True)
class PitchResult:
    number: int
    criteria: dict[str, bool]
    url: str | None


@dataclass(frozen=True, slots=True)
class PlayResult:
    start_time: str | None
    pitcher: str
    batter: str
    score: int
    pitches: tuple[PitchResult, ...]


@retry(wait=wait_exponential(multiplier=1, min=1, max=10))
def get_schedule(start_date: datetime.date, end_date: datetime.date):
    games = statsapi.schedule(start_date=start_date, end_date=end_date)
    return games


def get_games(year: int):
    games = []

    for month in range(1, 13):
        start_date = datetime.date(year, month, 1)
        end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])
        month_games = get_schedule(start_date, end_date)
        games.extend(month_games)

    return games


@retry(wait=wait_exponential(multiplier=1, min=1, max=10))
def get_play_by_play(game_id: int):
    game = statsapi.get("game_playByPlay", {"gamePk": game_id})
    return game


def pickle_dump(data: Any, path: pathlib.Path | str):
    with tempfile.NamedTemporaryFile("wb", delete=False) as f:
        pickle.dump(data, f)

    pathlib.Path(f.name).rename(path)


def build_play_result(play: dict[str, Any]) -> PlayResult:
    pitches = [play["playEvents"][i] for i in play["pitchIndex"]]
    first, second, third = pitches
    start_time = play.get("about", {}).get("startTime")
    if start_time is None:
        start_time = next(
            (
                event["startTime"]
                for event in play["playEvents"]
                if event.get("startTime") is not None
            ),
            None,
        )

    bat_side = play["matchup"]["batSide"]["code"]
    outside = CATCHER_RIGHT if bat_side == "R" else CATCHER_LEFT
    inside = CATCHER_LEFT if bat_side == "R" else CATCHER_RIGHT

    criteria = [
        {"outside": first["pitchData"]["zone"] in outside},
        {
            "in_dirt": second["details"]["code"] in BALL_IN_DIRT_CODES,
            "chase": second["details"]["code"] in SWINGING_STRIKE_CODES,
        },
        {
            "97_plus_mph": third["pitchData"]["startSpeed"] >= 96.5,
            "inside": third["pitchData"]["zone"] in inside,
            "called_strike": third["details"]["code"] == "C",
        },
    ]

    pitch_results = []
    for number, (pitch, pitch_criteria) in enumerate(zip(pitches, criteria), start=1):
        play_id = pitch.get("playId")
        pitch_results.append(
            PitchResult(
                number=number,
                criteria=pitch_criteria,
                url=SAVANT_VIDEO_URL.format(play_id) if play_id else None,
            )
        )

    score = sum(
        passed
        for pitch_result in pitch_results
        for passed in pitch_result.criteria.values()
    )

    return PlayResult(
        start_time=start_time,
        pitcher=play["matchup"]["pitcher"]["fullName"],
        batter=play["matchup"]["batter"]["fullName"],
        score=score,
        pitches=tuple(pitch_results),
    )


def format_human_results(best_score: int, plays: list[PlayResult]) -> str:
    lines = [f"best_score={best_score}"]

    for play in plays:
        start_time = play.start_time or "unknown time"
        lines.append(f"{start_time} - {play.pitcher} to {play.batter}")
        for pitch in play.pitches:
            lines.append(f"  Pitch {pitch.number}")
            for criterion, passed in pitch.criteria.items():
                lines.append(
                    f"    {CRITERION_LABELS[criterion]}: {'yes' if passed else 'no'}"
                )
            url = pitch.url or "unavailable (missing playId)"
            lines.append(f"    URL: {url}")

    return "\n".join(lines)


def format_json_results(best_score: int, plays: list[PlayResult]) -> str:
    return json.dumps(
        {"best_score": best_score, "plays": [asdict(play) for play in plays]},
        separators=(",", ":"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    quiet = args.quiet or args.json

    best_plays: list[PlayResult] = []
    best_score = 0

    schedules = {}

    CACHE_DIR.mkdir(exist_ok=True)

    try:
        with open(CACHE_DIR / "schedules.pickle", "rb") as f:
            schedules = pickle.load(f)
    except FileNotFoundError:
        pass

    current_year = datetime.datetime.now(tz=datetime.UTC).year

    for year in range(current_year, 2007, -1):
        games = schedules.get(year, None)
        if games is None:
            if not quiet:
                print(f"fetching {year} games")
            games = get_games(year)

            if year < current_year:
                schedules[year] = games
                file = CACHE_DIR / "schedules.pickle"
                if not quiet:
                    print(f"writing {file}")
                pickle_dump(schedules, file)

        play_by_play = {}

        try:
            file = CACHE_DIR / f"pbp{year}.pickle"
            with open(file, "rb") as f:
                if not quiet:
                    print(f"reading {file}")
                play_by_play = pickle.load(f)
        except FileNotFoundError:
            pass

        pbp_updated = False

        for scheduled in tqdm(games, desc=f"{year} season", disable=quiet):
            if scheduled["status"] != "Final":  # hasn't been played yet
                continue

            if scheduled["game_type"] in ["E", "S"]:  # exhibition or spring training
                continue

            game_id = scheduled["game_id"]

            game = play_by_play.get(game_id, None)

            if game is None:
                pbp_updated = True
                game = get_play_by_play(game_id)
                play_by_play[game_id] = game

            for play in game["allPlays"]:
                if not (play["count"]["balls"] == 0 and play["count"]["strikes"] == 3):
                    continue

                pitch_idxs = play["pitchIndex"]

                if len(pitch_idxs) != 3:  # foul balls
                    continue

                pitches = [play["playEvents"][i] for i in pitch_idxs]

                # automatic strike
                if any(pitch["details"]["code"] == "AC" for pitch in pitches):
                    continue

                first, second, third = pitches

                try:
                    # counting sweeper as a slider
                    if first["details"]["type"]["code"] not in ["SL", "ST"]:
                        continue

                    # counting knuckle curve as a curveball
                    if second["details"]["type"]["code"] not in ["CU", "KC"]:
                        continue

                    # counting sinker as a fastball
                    if third["details"]["type"]["code"] not in ["FF", "SI"]:
                        continue
                except KeyError:
                    continue

                result = build_play_result(play)
                score = result.score
                if score >= best_score:
                    if score > best_score:
                        best_plays.clear()
                    best_plays.append(result)
                    best_score = score

        best_plays.sort(
            key=lambda play: (play.start_time is None, play.start_time or "")
        )
        if not quiet:
            print(format_human_results(best_score, best_plays))

        if pbp_updated:
            file = CACHE_DIR / f"pbp{year}.pickle"
            if not quiet:
                print(f"writing {file}")
            pickle_dump(play_by_play, file)

    if args.json:
        print(format_json_results(best_score, best_plays))
    else:
        print(format_human_results(best_score, best_plays))


if __name__ == "__main__":
    main()

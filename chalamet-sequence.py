import argparse
import calendar
import datetime
import pathlib
import pickle
import tempfile
from typing import Any

import statsapi
from tenacity import retry, wait_exponential
from tqdm import tqdm

CATCHER_LEFT = [1, 4, 7, 11, 13]
CATCHER_RIGHT = [3, 6, 9, 12, 14]


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


parser = argparse.ArgumentParser()
parser.add_argument("--quiet", action="store_true")
args = parser.parse_args()

best_plays = []
best_score = 0

schedules = {}

try:
    with open("pickles/schedules.pickle", "rb") as f:
        schedules = pickle.load(f)
except FileNotFoundError:
    pass

current_year = datetime.datetime.now().year

for year in range(current_year, 2007, -1):
    games = schedules.get(year, None)
    if games is None:
        if not args.quiet:
            print(f"fetching {year} games")
        games = get_games(year)

        if year < current_year:
            schedules[year] = games
            file = "pickles/schedules.pickle"
            if not args.quiet:
                print(f"writing {file}")
            pickle_dump(schedules, file)

    play_by_play = {}

    try:
        file = f"pickles/pbp{year}.pickle"
        with open(file, "rb") as f:
            if not args.quiet:
                print(f"reading {file}")
            play_by_play = pickle.load(f)
    except FileNotFoundError:
        pass

    pbp_updated = False

    for scheduled in tqdm(games, desc=f"{year} season", disable=args.quiet):
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

            def get_score(play):
                score = 0

                bat_side = play["matchup"]["batSide"]["code"]
                outside = CATCHER_RIGHT if bat_side == "R" else CATCHER_LEFT
                inside = CATCHER_LEFT if bat_side == "R" else CATCHER_RIGHT

                # outside
                if first["pitchData"]["zone"] in outside:
                    score += 1

                # dirt
                if second["pitchData"]["zone"] in [13, 14]:
                    score += 1

                # chase
                if second["details"]["code"] == "S":
                    score += 1

                # 97+ mph
                if third["pitchData"]["startSpeed"] >= 96.5:
                    score += 1

                # inside
                if third["pitchData"]["zone"] in inside:
                    score += 1

                # called strike
                if third["details"]["code"] == "C":
                    score += 1

                return score

            score = get_score(play)
            if score >= best_score:
                if score > best_score:
                    best_plays.clear()
                best_plays.append(play)
                best_score = score

    best_plays.sort(key=lambda play: play["about"]["startTime"])
    if not args.quiet:
        print(f"{best_score=}")
        print(
            "\n".join(
                f"{play['about']['startTime']} - {play['matchup']['pitcher']['fullName']} to {play['matchup']['batter']['fullName']}"
                for play in best_plays
            )
        )

    if pbp_updated:
        file = f"pickles/pbp{year}.pickle"
        if not args.quiet:
            print(f"writing {file}")
        pickle_dump(play_by_play, file)

print(f"{best_score=}")
print(
    "\n".join(
        f"{play['about']['startTime']} - {play['matchup']['pitcher']['fullName']} to {play['matchup']['batter']['fullName']}"
        for play in best_plays
    )
)

import calendar
import datetime
import json
from pprint import pprint

import statsapi
from tenacity import retry, wait_exponential
from tqdm import tqdm

CATCHER_LEFT = [1, 4, 7, 11, 13]
CATCHER_RIGHT = [3, 6, 9, 12, 14]


@retry(wait=wait_exponential(multiplier=1, min=1, max=10))
def get_schedule(start_date: datetime.date, end_date: datetime.date):
    games = statsapi.schedule(start_date=start_date, end_date=end_date)
    return games


def get_games(year):
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


plays = []

for year in range(2025, 2015, -1):
    games = get_games(year)

    for scheduled in tqdm(games, desc=f"{year} season"):
        if scheduled["status"] != "Final":  # hasn't been played yet
            continue

        game = get_play_by_play(scheduled["game_id"])

        for play in game["allPlays"]:
            if not (play["count"]["balls"] == 0 and play["count"]["strikes"] == 3):
                continue

            pitch_idxs = play["pitchIndex"]

            if len(pitch_idxs) != 3:  # foul balls
                continue

            pitches = [play["playEvents"][i] for i in pitch_idxs]

            first, second, third = pitches

            try:
                # counting sweeper as a slider
                if first["details"]["type"]["code"] not in ["SL", "ST"]:
                    continue

                bat_side = play["matchup"]["batSide"]["code"]

                outside = CATCHER_RIGHT if bat_side == "R" else CATCHER_LEFT

                # not outside
                if first["details"]["zone"] not in outside:
                    continue

                # counting knuckle curve as a curveball
                if second["details"]["type"]["code"] not in ["CU", "KC"]:
                    continue

                # not a swinging strike
                if second["details"]["code"] != "S":
                    continue

                # not below the zone
                if second["details"]["zone"] not in [13, 14]:
                    continue

                # counting sinker as a fastball
                if third["details"]["type"]["code"] not in ["FF", "SI"]:
                    continue

                # not a called strike
                if third["details"]["code"] != "C":
                    continue

                inside = CATCHER_LEFT if bat_side == "R" else CATCHER_RIGHT

                # not inside
                if third["details"]["zone"] not in inside:
                    continue
            except Exception:  # pitch data missing
                continue

            pprint(play)
            plays.append(play)

with open("plays.json", "w") as f:
    json.dump(plays, f, indent=4)

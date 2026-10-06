"""Special prize selection consumes combat Math RNG, including singleton groups."""
import json
from combat_runtime_data import load_data, table
from combat_resolution import random_below


def special_prize_candidates(encounter_id):
    encounters=load_data('encounters.json')['encounters']
    encounter=next(e for e in encounters if e['id']==encounter_id)
    group=encounter['rewardGroup']
    return [tid for tid,row in table('Treasure').items() if int(row[4])==group]


def select_prize(candidates,next_math):
    if not candidates:return None
    return candidates[random_below(next_math(),len(candidates))]

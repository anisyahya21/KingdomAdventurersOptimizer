"""Counter (skill 26, type 20) invocation semantics and the scenario-level indexing trap.

Verified here without running a battle:

  * Counter is reactive: `flags & 32` excludes it from the ordinary active cascade, so it never
    competes in `SharedControllers.choose`;
  * its invocation rate is the type-20 ladder `(36, 18, 9)` for levels 0/1/2
    (`combat_resolution.skill_invocation_rate`);
  * its level is read by `zip(specs.skills, specs.levels)` at Counter's OWN index
    (`SharedControllers.reaction`), while the active cascade reads `invocation_levels[ordinal in the
    FILTERED list]` (`combat_skill_selection.active_skill_infos`);
  * therefore, with Counter declared FIRST (the library's encoding), raising Counter's level also
    raises the 7-hit's level -- they both read slot 0. Moving Counter LAST makes the two independent
    while leaving the active cascade's order and levels untouched.

    python check_combat_counter_invocation.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from combat_initial_state import EVIDENCE                            # noqa: E402
from combat_runtime_data import load_data                             # noqa: E402
from combat_resolution import skill_invocation_rate                   # noqa: E402
from combat_skill_selection import active_skill_infos                 # noqa: E402

COUNTER = 26
FAMILY = [26, 110, 25, 24, 23, 22]          # Counter + 7/5/4/3/2-hit, the library's order
ACTIVE = [110, 25, 24, 23, 22]


def main():
    rows = {r['id']: r for r in load_data('weapon-skill-profiles.json')['skills']}
    counter = rows[COUNTER]
    report = {
        'counterType': counter['type'],
        'counterFlags': counter['flags'],
        'counterIsReactive': bool(counter['flags'] & 32),
        'counterAlsoFlaggedActive': bool(counter['flags'] & 8),
        'counterCount': counter['count'],
        'counterMinMp': counter['minMp'],
        'invocationRatesByLevel': {level: skill_invocation_rate(counter['type'], level)
                                   for level in (0, 1, 2)},
    }

    def active_levels(skills, levels):
        infos = active_skill_infos([rows[s] for s in skills], levels, 10 ** 6, lambda s: 0)
        return {s['id']: level for s, level in infos}

    # A) the library's encoding: Counter first, so the active five read slots 0..4 and Counter reads
    # slot 0 as well.
    report['counterFirstAllZero'] = active_levels(FAMILY, [0] * 6)
    report['counterFirstCounterRaisedTo2'] = active_levels(FAMILY, [2, 0, 0, 0, 0, 0])
    report['counterFirstConfounded'] = (
        report['counterFirstAllZero'] != report['counterFirstCounterRaisedTo2'])
    # B) Counter last: the five actives still read slots 0..4, Counter reads slot 5.
    last = ACTIVE + [COUNTER]
    report['counterLastAllZero'] = active_levels(last, [0] * 6)
    report['counterLastCounterRaisedTo2'] = active_levels(last, [0, 0, 0, 0, 0, 2])
    report['counterLastIndependent'] = (
        report['counterLastAllZero'] == report['counterLastCounterRaisedTo2'] ==
        {skill: 0 for skill in ACTIVE})
    report['limits'] = [
        'Rate ladder and level indexing are read from the portable recovered functions; the original '
        "binary's Counter selection is checked separately by check_combat_counter_queue.py (72 cases) "
        'and check_combat_reflection_event.py.',
        'The player-facing High/Medium/Low label is a UI convention: this check reports the numeric '
        'levels and their rates, not which label the interface prints.',
        'No battle is run here; a Counter check happens per resolved attack against the carrier '
        '(hit or miss), which is measured in test_counter_level_arms.py.']
    (EVIDENCE / 'counter-invocation-checks.json').write_text(json.dumps(report, indent=2) + '\n',
                                                             encoding='utf-8')
    print(json.dumps(report, indent=2))
    assert report['counterIsReactive'], 'Counter must be the reactive (flags & 32) skill'
    assert report['counterFirstConfounded'], 'the library encoding was expected to be confounded'
    assert report['counterLastIndependent'], 'the Counter-last encoding must be independent'
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Exercise actual arm-command validation; replace only the battle stage with a no-op."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import uuid

import benchmark_encounter_redesign as harness


def main():
    root = Path(__file__).resolve().parents[2] / 'tmp' / 'encounter-redesign-20260928'
    work = root / ('arm-entry-check-' + uuid.uuid4().hex)
    work.mkdir(parents=True)
    plan = harness.build_plan(harness._Args(), python=sys.executable)
    path = work / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    reached = []
    original = harness.arm_smoke

    def no_battles(plan, arm, source, out, meter, workers):
        assert meter.charged == 0
        reached.append(arm)
        return {}

    harness.arm_smoke = no_battles
    try:
        for arm in harness.ARMS:
            args = SimpleNamespace(authorised=True, plan=str(path), arm=arm,
                                   source_root=plan['arms'][arm]['source'], out=str(work / arm),
                                   workers=1, cap=40, stage='smoke')
            assert harness.cmd_arm(args) == 0
        assert reached == list(harness.ARMS)
    finally:
        harness.arm_smoke = original
    print('PASS both real arm command paths reach the stubbed stage; zero battles')


if __name__ == '__main__':
    main()

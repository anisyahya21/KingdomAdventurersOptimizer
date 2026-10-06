"""Command lifecycle: an already-drained campaign must not be refused as if its battles were still
saving, while the drain-before-switch guard must still hold while battles are genuinely in flight.

The desktop sends its Start at the UI default duty 0.9. ``Coordinator.busy`` (and the evaluator
behind it) also report a worker that is inside its post-battle duty hold, and that hold starts only
after the result is persisted. The start/switch guards must therefore key on real in-flight work
(``evaluator.pending``), not on the duty hold, or a fully drained campaign is refused with
"Wait until outstanding simulations have saved." until the last hold expires.

Run:  <venv>/python -B -X utf8 tools/recovery/check_optimizer_command_lifecycle.py
No native battle, no user library, no desktop call; a throwaway scratch library only.
"""
import shutil
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import strategy_optimizer as so                       # noqa: E402
import strategy_encounter_search as search             # noqa: E402
import strategy_mechanics as mechanics                 # noqa: E402
import check_cohort_dispatch as fixture                # noqa: E402
import check_encounter_search as base                  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)) if detail else ''))


class LongBattlePool(fixture.GatedPool):
    """Gated pool whose results claim a long battle, so the duty hold is long and observable.

    The hold is ``elapsed * (1 - duty) / duty``; 90 s of claimed battle time at duty 0.9 leaves a
    deterministic ~10 s window in which the evaluator is still duty-held after every result is saved.
    """

    def __init__(self, baseline, seconds):
        super().__init__(baseline)
        self.seconds = seconds

    def release(self, count):
        released = 0
        for entry in list(self.pending):
            if released >= count:
                break
            future, scenario, seeds = entry
            units = mechanics.normalize_scenario(scenario).get('ownUnits')
            reward = 0 if units == self.baseline else 100
            future.set_result(dict(seeds=list(seeds), verdict=1, resultBackend='native',
                                   rewardOutcome=dict(pendingChests=reward),
                                   elapsedSeconds=self.seconds, cpuSeconds=0.01))
            self.pending.remove(entry)
            released += 1
        return released


def wait_for(predicate, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    return None


def main():
    home = HERE.parents[1] / 'tmp' / 'encounter-redesign-20260928' / ('ka-cmd-%s' % uuid.uuid4().hex[:8])
    home.mkdir(parents=True, exist_ok=True)
    path = home / 'scratch.sqlite'
    pool = LongBattlePool([], 90.0)
    search.EVALUATOR_FACTORY = base.mock_factory(pool)
    opt = so.Optimizer(path)
    try:
        wait_for(lambda: opt._campaign is not None)
        opened = opt.status()
        check('library opens to a paused, healthy coordinator',
              opened.get('state') == 'Paused' and not opened.get('error'), opened.get('state'))
        # Regression guard for the rollback snapshot behind every activation: the coordinator's
        # `planning_host` points back at this Optimizer, whose `lock`/`commands` are not
        # deep-copyable. `_encounter_snapshot` must keep that identity instead of walking into the
        # host, or `_activate_encounter` raises `cannot pickle '_thread.lock' object` and the run
        # pauses with zero dispatched battles.
        shim = type('StoreShim', (), {'db': opt._encounter._db_ref})()
        snapshot_error = None
        try:
            snapshot = opt._encounter_snapshot(shim)
        except Exception as exc:  # noqa: BLE001 - the guard reports the failure, it does not mask it
            snapshot, snapshot_error = None, repr(exc)
        check('rollback snapshot copies coordinator state without the host back-reference',
              snapshot_error is None
              and snapshot.get('planning_host') is opt._encounter.planning_host,
              snapshot_error)
        opt.command('focus_encounter', {'encounterIds': [19]}, wait=True)
        opt.command('community_start', {'workers': 8, 'duty': 0.9, 'newCampaign': True}, wait=True)
        check('campaign start reaches Running', opt.status().get('state') == 'Running',
              opt.status().get('state'))
        check('battles are dispatched', bool(wait_for(lambda: len(pool.pending) == 8)),
              len(pool.pending))

        # Drain safety, preserved: while battles are genuinely in flight the switch is refused.
        check('in-flight battles are outstanding simulations',
              opt._outstanding_simulations() is True)
        opt.command('community_start', {'workers': 8, 'duty': 0.9}, wait=True)
        refused = opt.status().get('error')
        check('a start while battles are in flight is refused with the drain message',
              refused == 'Wait until outstanding simulations have saved.', refused)

        opt.command('pause', wait=True)
        pool.release(8)
        check('pause drains the in-flight battles',
              bool(wait_for(lambda: opt.status().get('state') == 'Paused')),
              opt.status().get('state'))
        evaluator = opt._encounter.evaluator
        check('every submitted battle has been harvested and saved', len(evaluator.pending) == 0,
              len(evaluator.pending))
        check('a post-battle duty hold keeps Evaluator.busy() true', evaluator.busy() is True)
        check('the duty hold is not an outstanding simulation',
              opt._outstanding_simulations() is False)

        opt.command('community_start', {'workers': 8, 'duty': 0.9}, wait=True)
        message = opt.status().get('error')
        check('an already-drained campaign is not refused with the drain message',
              message != 'Wait until outstanding simulations have saved.', message)
        check('the drained campaign resumes to Running', opt.status().get('state') == 'Running',
              opt.status().get('state'))
    finally:
        try:
            opt.command('close')
        except Exception:  # noqa: BLE001 - shutdown must not mask the result
            pass
        pool.release(len(pool.pending))
        opt.thread.join(timeout=25)
        check('the coordinator thread stops after close', not opt.thread.is_alive())
        shutil.rmtree(home, ignore_errors=True)

    passed = sum(1 for _name, ok in RESULTS if ok)
    total = len(RESULTS)
    print('\ncheck_optimizer_command_lifecycle: %d/%d' % (passed, total))
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())

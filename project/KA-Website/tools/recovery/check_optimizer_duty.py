"""The duty envelope must cool only the worker that just completed a battle."""
from strategy_optimizer import duty_hold_seconds, duty_ready_slot


def main():
    hold = duty_hold_seconds(.05, .9)
    assert .0055 < hold < .0056
    ready_at = [hold, 0., 0.]
    assert duty_ready_slot([], ready_at, 0.) == 1
    assert duty_ready_slot([{'slot': 1}], ready_at, 0.) == 2
    assert duty_ready_slot([{'slot': 1}, {'slot': 2}], ready_at, 0.) is None
    assert duty_ready_slot([{'slot': 1}, {'slot': 2}], ready_at, hold) == 0
    assert duty_hold_seconds(.05, 1.) == 0.
    print('independent worker duty gates passed')


if __name__ == '__main__':
    main()

"""Regression: long fights must finish natively and match Python-verified complete outputs.

The fixture contains 13 command-queue overflows and two object-arena overflows.
Use --python to additionally repeat the canonical Python comparison (several minutes).
"""
import argparse
import json
from pathlib import Path

from strategy_optimizer_adapter import simulate


def payload(result):
    return {key: value for key, value in result.items()
            if key not in ('resultBackend', 'routingVersion')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', action='store_true')
    args = parser.parse_args()
    if args.python:
        import strategy_optimizer_backend as acceleration
        acceleration.install(acceleration.identity())
    fixture = json.loads((Path(__file__).parent / 'fixtures/native_long_tail.json').read_text())
    for case in fixture['cases']:
        scenario = fixture['scenarios'][case['candidate']]
        result = simulate(scenario, case['seeds'], backend='native')
        assert result['resultBackend'] == 'native', (case['seeds'], result['resultBackend'])
        actual = payload(result)
        assert actual == case['expected'], (case['seeds'], 'complete output changed')
        if args.python:
            reference = simulate(scenario, case['seeds'], backend='python')
            assert actual == payload(reference), (case['seeds'], 'Python mismatch')
    print(f"PASS: {len(fixture['cases'])} long fights finished natively; all complete outputs matched")
    import strategy_optimizer_native as native
    if native._large_module is not None:
        assert not native._large_module._cache, 'Expanded templates must be released after use'


if __name__ == '__main__':
    main()

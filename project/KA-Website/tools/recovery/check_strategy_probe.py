"""Threshold and interaction analysis: the arithmetic, the honesty rules, and the refusals.

Deterministic and synthetic - no simulator runs - so the boundary and compensation logic is pinned
independently of the engine. The live end-to-end path is covered by the probe run in the session's
verification notes.
"""
import unittest

import strategy_probe as probe


def pseudo_noise(index, spread):
    """Deterministic per-seed variation with a mean of about zero and a realistic spread.

    Real per-seed chest differences swing by several chests, which is exactly why a paired design is
    needed at all; a synthetic case with almost no spread would make every small difference look
    significant and would not test the rule that matters.
    """
    raw = (index*2654435761) % 4294967296
    return ((raw/4294967296.0)-0.5)*2*spread


def series(values):
    """A chest series keyed by seed pair, as the analyser reads it."""
    return {(index, index+1000): value for index, value in enumerate(values)}


BASE = [8, 10, 6, 9, 7, 11, 8, 9] * 8
REFERENCE = series(BASE)


def shifted(delta, spread=15.0):
    """The reference shifted by `delta`, with heavy per-seed noise around it."""
    return series([value+delta+pseudo_noise(index, spread) for index, value in enumerate(BASE)])


class SingleAxisTests(unittest.TestCase):
    def test_adaptive_refinement_preserves_uncertainty_and_two_edges(self):
        rows = [dict(value=20, viable=False, n=64),
                dict(value=40, viable=None, n=64),
                dict(value=60, viable=True, n=64),
                dict(value=100, viable=False, n=64),
                dict(value=140, viable=True, n=64)]
        self.assertEqual(probe.next_probe_actions(rows, 1, 200)['extend'], [40])
        rows[1]['viable'] = False
        self.assertEqual(probe.next_probe_actions(rows, 1, 200)['values'], [120, 80])

    def test_equal_point_is_not_a_failure(self):
        """A point that merely matches the reference must not be reported as a fall-off."""
        ladder = [dict(value=100, chests=REFERENCE, effective=100, label='a'),
                  dict(value=80, chests=shifted(-9), effective=80, label='b')]
        result = probe.analyse(ladder, REFERENCE)
        self.assertEqual(result['viableLow'], 100)
        self.assertTrue(result['rows'][0]['viable'])
        self.assertFalse(result['rows'][1]['viable'])
        self.assertIn('stops working at measured 80', result['statement'])

    def test_disconnected_working_points_are_not_called_one_range(self):
        ladder = [dict(value=20, chests=REFERENCE, label='a'),
                  dict(value=60, chests=shifted(-9), label='b'),
                  dict(value=100, chests=REFERENCE, label='c')]
        result = probe.analyse(ladder, REFERENCE)
        self.assertEqual(len(result['transitions']), 2)
        self.assertIn('do not treat them as one continuous range', result['statement'])
        self.assertNotIn('viable between', result['statement'])

    def test_no_boundary_in_range_is_reported_as_such(self):
        ladder = [dict(value=100, chests=REFERENCE, effective=100, label='a'),
                  dict(value=60, chests=shifted(-1), effective=60, label='b')]
        result = probe.analyse(ladder, REFERENCE)
        self.assertIsNone(result['viableLow'])
        self.assertIsNone(result['rows'][1]['viable'])
        self.assertIn('not enough conclusive points', result['statement'])

    def test_insufficient_evidence_is_refused(self):
        ladder = [dict(value=100, chests=REFERENCE, effective=100, label='a')]
        result = probe.analyse(ladder, REFERENCE)
        self.assertIsNone(result['viableLow'])
        self.assertIn('not enough conclusive points', result['statement'])


class GridTests(unittest.TestCase):
    def test_compensation_is_found_between_two_bracketed_rows(self):
        cells = [
            # low attack: needs a lot of defence before it stops being worse
            dict(v1=20, v2=0, chests=shifted(-9)),
            dict(v1=20, v2=40, chests=REFERENCE),
            # high attack: works with no extra defence at all
            dict(v1=40, v2=0, chests=REFERENCE),
            dict(v1=40, v2=40, chests=REFERENCE),
        ]
        result = probe.analyse_grid(cells, REFERENCE)
        self.assertIsNotNone(result['interaction'])
        self.assertEqual(result['interaction']['kind'], 'compensation')
        self.assertEqual(result['interaction']['fromThreshold'], 40)
        self.assertEqual(result['interaction']['toThreshold'], 0)
        self.assertFalse(result['interaction']['toBracketed'])
        self.assertIn('must reach at least 40 when the first is 20', result['statement'])

    def test_same_threshold_is_not_called_compensation(self):
        cells = [
            dict(v1=20, v2=0, chests=shifted(-9)),
            dict(v1=20, v2=40, chests=REFERENCE),
            dict(v1=40, v2=0, chests=shifted(-8)),
            dict(v1=40, v2=40, chests=REFERENCE),
        ]
        result = probe.analyse_grid(cells, REFERENCE)
        self.assertIsNone(result['interaction'])
        self.assertIn('not shown to compensate', result['statement'])

    def test_unbracketed_grid_says_so(self):
        cells = [dict(v1=20, v2=0, chests=REFERENCE),
                 dict(v1=20, v2=40, chests=REFERENCE)]
        result = probe.analyse_grid(cells, REFERENCE)
        self.assertIsNone(result['interaction'])
        self.assertIn('no boundary was bracketed', result['statement'])

    def test_axis_names_and_labels_round_trip(self):
        label = probe.grid_label('atk', 19, 'def', 40, 'Ninja', 'pivot')
        self.assertEqual(probe.grid_values(label, 'atk', 'def'), (19, 40))
        self.assertEqual(probe.axis_names(),
                         ['atk', 'def', 'dex', 'herbs', 'hp', 'int', 'lck', 'mp', 'spd'])
        self.assertIn('Attack', probe.axis_info('atk')['label'])

    def test_ladder_is_ratio_based_and_includes_the_pivot(self):
        import strategy_optimizer_adapter as adapter
        scenario = adapter.default_scenario()
        ladder = probe.default_ladder(scenario, 'Ninja (A aw20)', 'atk', 5)
        pivot = probe.pivot_value(scenario, 'Ninja (A aw20)', 'atk')
        self.assertIn(pivot, ladder)
        self.assertEqual(ladder, sorted(ladder))
        self.assertEqual(len(ladder), len(set(ladder)))


class ConditionalIntAxisTests(unittest.TestCase):
    """INT is a real combat axis, but only for a unit whose attack is magical.

    The rule is the canonical one: `combat_resolution.damage_from_parameters` reads parameter 18 for
    a magical hit and parameter 13 otherwise, and `search_contract.stat_is_searchable` says INT is
    searchable exactly while a magic attack skill is equipped. The probe reuses that classification
    rather than keeping a second magic-skill list.
    """

    @classmethod
    def setUpClass(cls):
        import search_contract
        from strategy_optimizer_adapter import default_scenario, validate_scenario
        cls.contract = search_contract
        cls.base = validate_scenario(default_scenario())
        humans = [unit for unit in cls.base['ownUnits'] if unit.get('human')]
        cls.plain_unit = next(unit for unit in humans
                              if not search_contract.stat_is_searchable(cls.base, unit, 'int'))
        cls.plain = cls.plain_unit['name']
        roomy = [unit for unit in humans
                 if len(unit['skills']) < search_contract.MAX_SKILLS_PER_HUMAN]
        magic_unit = next((unit for unit in roomy if not unit['skills']), roomy[0])
        cls.magic = magic_unit['name']
        # Fire Magic I (5) is a magic attack row, so it is what makes INT a damage input on this unit.
        cls.armed = search_contract.add_skill(cls.base, cls.magic, 5)

    def test_int_is_an_established_axis(self):
        info = probe.axis_info('int')
        self.assertEqual(info['parameter'], 18)
        self.assertTrue(info['established'])
        self.assertIn('Intelligence', info['label'])
        self.assertIn('int', probe.axis_names())

    def test_int_is_allowed_on_a_magic_attacker(self):
        self.assertEqual(probe.resolve_probe_unit(self.armed, ['int']), self.magic)
        pivot = probe.pivot_value(self.armed, self.magic, 'int')
        armed_unit = next(unit for unit in self.armed['ownUnits'] if unit['name'] == self.magic)
        self.assertEqual(pivot, int(armed_unit['parameters'][18]['rawValue']))
        self.assertGreater(pivot, 0)
        ladder = probe.default_ladder(self.armed, self.magic, 'int', 5)
        self.assertIn(pivot, ladder)
        stepped = probe.apply_axis(self.armed, self.magic, 'int', pivot + 10)
        self.assertEqual(probe.pivot_value(stepped, self.magic, 'int'), pivot + 10)
        self.assertIsInstance(probe.effective_stat(self.armed, self.magic, 'int'), int)

    def test_int_is_refused_on_a_non_magic_unit(self):
        self.assertFalse(self.contract.stat_is_searchable(self.base, self.plain_unit, 'int'))
        # The default is the first human, which is not a magic attacker, so it is refused.
        with self.assertRaises(probe.ProbeError):
            probe.resolve_probe_unit(self.base, ['int'])
        # An explicitly selected non-magic unit is refused the same way.
        with self.assertRaises(probe.ProbeError):
            probe.resolve_probe_unit(self.base, ['int'], self.plain)
        with self.assertRaises(probe.ProbeError):
            probe.pivot_value(self.base, self.plain, 'int')
        with self.assertRaises(probe.ProbeError):
            probe.apply_axis(self.base, self.plain, 'int', 50)

    def test_int_grid_requires_a_magic_attacker_for_either_axis(self):
        for axes in (['int', 'atk'], ['atk', 'int']):
            with self.assertRaises(probe.ProbeError):
                probe.resolve_probe_unit(self.base, axes)
            self.assertEqual(probe.resolve_probe_unit(self.armed, axes), self.magic)
        label = probe.grid_label('int', 40, 'atk', 60, self.magic, 'pivot')
        self.assertEqual(probe.grid_values(label, 'int', 'atk'), (40, 60))

    def test_equipment_only_stats_are_not_probe_axes(self):
        # The scenario carries Gathering (20), Move (21) and Love (22), but no recovered damage or
        # hit formula reads them, so they are neither probe axes nor generated stat mutations.
        carried = set(self.base['ownUnits'][0]['parameters'])
        self.assertTrue({20, 21, 22} <= carried)
        self.assertFalse({'gth', 'mov', 'hrt'} & set(probe.AXES))
        # The canonical probe axes are the seven core stats plus the conditional INT. Energy (native
        # parameter 12) is a bounded vitals axis a fine-tuning program may sweep - it resolves through
        # `axis_info` - but it is deliberately not one of the axes the optimiser's own probe rotation
        # enumerates, so `axis_names()` never grows underneath existing callers.
        canonical_parameters = {info['parameter'] for info in probe.PROBE_AXES.values()
                                if 'parameter' in info}
        self.assertEqual(canonical_parameters, {10, 11, 13, 14, 15, 16, 18, 19})
        extra_parameters = {info['parameter'] for info in probe.AXES.values() if 'parameter' in info}
        self.assertEqual(extra_parameters - canonical_parameters, {12})
        self.assertNotIn('vig', probe.axis_names())
        self.assertFalse({20, 21, 22} & set(self.contract.CORE_STATS.values()))
        self.assertFalse({20, 21, 22} & set(self.contract.INT_STAT.values()))


if __name__ == '__main__':
    unittest.main(verbosity=2)

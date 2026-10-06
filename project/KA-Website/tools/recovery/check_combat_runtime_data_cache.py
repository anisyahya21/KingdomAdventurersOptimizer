"""Focused checks for cached runtime JSON loading."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import combat_runtime_data as runtime


class RuntimeDataCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / 'fixture.json'
        self.data_path_patch = patch.object(runtime, 'data_path', return_value=self.path)
        self.data_path_patch.start()
        runtime.clear_data_cache()

    def tearDown(self):
        runtime.clear_data_cache()
        self.data_path_patch.stop()
        self.temp_dir.cleanup()

    def test_cache_hits_reuse_parse_and_isolate_mutations(self):
        expected = {'items': [{'id': 1, 'nested': [2, {'value': 'same'}]}]}
        self.path.write_text(json.dumps(expected), encoding='utf-8')
        original_loads = json.loads
        with patch.object(runtime.json, 'loads', wraps=original_loads) as loads:
            first = runtime.load_data('fixture.json')
            first['items'][0]['nested'][1]['value'] = 'caller mutation'
            first['items'].append({'id': 9})
            second = runtime.load_data('fixture.json')

        self.assertEqual(loads.call_count, 1)
        self.assertEqual(second, expected)
        self.assertIsNot(first, second)
        self.assertIsNot(first['items'], second['items'])
        self.assertIsNot(first['items'][0], second['items'][0])

    def test_content_change_is_seen_even_when_size_and_mtime_are_preserved(self):
        first_text = '{"items":[{"id":1,"label":"one"}]}'
        second_text = '{"items":[{"id":2,"label":"one"}]}'
        self.assertEqual(len(first_text), len(second_text))
        self.path.write_text(first_text, encoding='utf-8')
        first = runtime.load_data('fixture.json')
        original_stat = self.path.stat()

        self.path.write_text(second_text, encoding='utf-8')
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        second = runtime.load_data('fixture.json')

        self.assertEqual(first['items'][0]['id'], 1)
        self.assertEqual(second['items'][0]['id'], 2)

    def test_explicit_clear_reloads_updated_content(self):
        self.path.write_text('{"value":1}', encoding='utf-8')
        self.assertEqual(runtime.load_data('fixture.json'), {'value': 1})
        self.path.write_text('{"value":2}', encoding='utf-8')
        runtime.clear_data_cache()
        self.assertEqual(runtime.load_data('fixture.json'), {'value': 2})


if __name__ == '__main__':
    unittest.main(verbosity=2)

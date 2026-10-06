"""Frozen record restoration uses the existing import path and respects its refusals."""
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import strategy_optimizer_desktop as desktop
from strategy_optimizer import Store, identity
from strategy_optimizer_adapter import default_scenario, provenance, stats

class RestoreCheck(unittest.TestCase):
    def test_restore_and_refusals(self):
        scenario = default_scenario()
        with tempfile.TemporaryDirectory(prefix='ka-restore-') as folder:
            store = Store(Path(folder)/'library.sqlite', provenance())
            bridge = desktop.Bridge.__new__(desktop.Bridge)
            bridge._library = store.path if hasattr(store, 'path') else Path(folder)/'library.sqlite'
            bridge._operation = threading.Lock()
            bridge._start_requested = False
            snapshot = dict(state='Paused', error=None)
            def command(action, value, wait=False):
                self.assertEqual(action, 'import')
                self.assertTrue(wait)
                self.assertEqual(value['scenario'], scenario)
                with store.db:
                    store.add(value['scenario'], value['label'], 'supplied', stats(scenario))
            controller = SimpleNamespace(status=lambda: snapshot, commands=queue.Queue(), command=Mock(side_effect=command))
            bridge._optimizer = controller
            bridge._resolve_target = Mock(return_value=(scenario, None, 'Frozen build', {}, False))
            restored = bridge.restore_strategy('earned:19:0')
            self.assertEqual(restored, dict(ok=True, candidateId=identity(scenario)))
            self.assertFalse(bridge._operation.locked())
            controller.command.reset_mock()
            snapshot['state'] = 'Running'
            self.assertFalse(bridge.restore_strategy('earned:19:0')['ok'])
            controller.command.assert_not_called()
            snapshot['state'] = 'Paused'
            snapshot['error'] = 'Import scope refused'
            controller.command.side_effect = None
            self.assertEqual(bridge.restore_strategy('earned:19:0')['error'], 'Import scope refused')
            bridge._operation.acquire()
            self.assertFalse(bridge.restore_strategy('earned:19:0')['ok'])
            bridge._operation.release()
            store.db.close()

if __name__ == '__main__':
    unittest.main()

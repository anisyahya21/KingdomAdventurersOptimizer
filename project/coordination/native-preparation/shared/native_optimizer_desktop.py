"""Compatible launcher for the full optimizer; optional diagnostic-only monitor."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import traceback
from http.server import ThreadingHTTPServer
from pathlib import Path

SHARED = Path(__file__).resolve().parent
ROOT = SHARED.parents[2]
from optimizer_storage import configure_optimizer_storage
STORAGE = configure_optimizer_storage(ROOT)
RECOVERY = ROOT / 'KA-Website/tools/recovery'
APP = ROOT / 'KA-Website/artifacts/kingdom-adventures'


class Bridge:
    def __init__(self, language=None):
        from native_optimizer_app import NativeOptimizerApp
        self._app = NativeOptimizerApp(language)

    def native_optimizer_info(self):
        return self._app.info()

    def native_optimizer_start(self, options):
        return self._app.start(options)

    def native_optimizer_status(self):
        return self._app.status()

    def native_optimizer_open_output(self):
        return self._app.open_output()

    def native_optimizer_attach_last(self):
        return self._app.attach_last()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--language', choices=['rust', 'go', 'cpp'])
    args = parser.parse_args()
    if not (APP / 'desktop-dist/desktop.html').is_file():
        raise RuntimeError('The desktop UI build is missing.')
    sys.path.insert(0, str(RECOVERY))
    # Reuse the existing static asset routing and security headers. No old
    # Optimizer or library is instantiated by this host.
    from strategy_optimizer_desktop import asset_handler
    import webview
    bridge = Bridge(args.language)
    server = ThreadingHTTPServer(('127.0.0.1', 0), asset_handler(app=APP))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disk-cache-size=33554432 --disable-features=msEdgeSidebarV2'
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    webview.create_window('Kingdom Adventurers · Rust / Go / C++ Optimizers',
                          f'http://127.0.0.1:{server.server_port}/desktop.html?native-languages=1',
                          js_api=bridge, width=1320, height=900, min_size=(980, 700))
    try:
        webview.start(gui='edgechromium', private_mode=True)
    finally:
        # Native runs live in a detached worker and survive closing this window.
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    try:
        if '--diagnostic' in sys.argv:
            sys.argv.remove('--diagnostic')
            main()
        else:
            # Previously installed shortcuts may still refer to this entry point.
            # Normal launches must always use the original complete workspace.
            sys.path.insert(0, str(RECOVERY))
            from strategy_language_desktop import main as full_main
            sys.argv = ['--engine' if arg == '--language' else arg for arg in sys.argv]
            full_main()
    except Exception as error:
        folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KingdomAdventurersOptimizer/native-runs'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'desktop-startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Native Optimizers could not start', 0x10)
        raise

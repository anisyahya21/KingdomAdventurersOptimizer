"""Thin Windows WebView2 host for the shared Synthetic to Legal React page.

SQLite paths stay inside this native Python bridge. The bridge uses the existing independent
read-only library reader; it never copies database bytes into the browser or imports the optimiser
writer.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
APP = REPO / "artifacts" / "kingdom-adventures"
API = APP / "api"
DATA_DIR = Path(os.environ.get("KA_SYNTHETIC_LEGAL_DATA_DIR",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KingdomAdventurersSyntheticLegal")))
RECENT_FILE = DATA_DIR / "recent-libraries.json"
ALLOWED_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
MAX_RECENT = 8
def _library_reader():
    if str(API) not in sys.path:
        sys.path.insert(0, str(API))
    import _optimizer_library_reader
    return _optimizer_library_reader


def _preview_reader():
    runtime = API / "_battle_runtime"
    for path in (API, runtime):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import _battle_preview
    return _battle_preview


class SyntheticLegalBridge:
    """The native chooser and read-only Python calls exposed to the embedded React page."""

    def __init__(self, recent_file=RECENT_FILE):
        self._window = None
        self._recent_file = Path(recent_file)
        self._recent = self._load_recent()
        self._libraries: dict[str, Path] = {}

    def _load_recent(self):
        try:
            raw = json.loads(self._recent_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        return [str(Path(item).resolve()) for item in raw
                if isinstance(item, str) and Path(item).is_file()
                and Path(item).suffix.lower() in ALLOWED_SUFFIXES][:MAX_RECENT]

    def _save_recent(self):
        try:
            self._recent_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._recent_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._recent, indent=2), encoding="utf-8")
            os.replace(temporary, self._recent_file)
        except OSError:
            # Library opening remains available if the per-user recent list cannot be saved.
            pass

    def list_recent_optimizer_libraries(self):
        return {"ok": True, "libraries": [
            {"path": path, "filename": Path(path).name} for path in self._recent
            if Path(path).is_file()
        ]}

    def open_optimizer_library(self):
        import webview
        recent_parent = Path(self._recent[0]).parent if self._recent else Path.home()
        directory = str(recent_parent if recent_parent.is_dir() else Path.home())
        try:
            selection = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("SQLite library (*.sqlite;*.sqlite3;*.db)", "All files (*.*)"),
                directory=directory,
            )
            if not selection:
                return {"ok": True, "cancelled": True}
            path = selection[0] if isinstance(selection, (tuple, list)) else selection
            return self.open_optimizer_library_path(str(path))
        except Exception as error:  # bridge calls return UI-readable errors rather than JS exceptions
            return {"ok": False, "error": str(error)}

    def open_optimizer_library_path(self, path):
        try:
            selected = Path(path).expanduser().resolve(strict=True)
            if selected.suffix.lower() not in ALLOWED_SUFFIXES:
                raise ValueError("Choose a .sqlite, .sqlite3, or .db file.")
            if not selected.is_file():
                raise ValueError("The selected library is not a file.")
            library = _library_reader().inspect_library(selected)
            library_id = uuid.uuid4().hex
            self._libraries[library_id] = selected
            self._recent = [str(selected), *(entry for entry in self._recent if entry != str(selected))][:MAX_RECENT]
            self._save_recent()
            return {
                "ok": True,
                "schema": "ka-optimizer-library-session-1",
                "libraryId": library_id,
                "libraryPath": str(selected),
                "readOnly": True,
                "sourceFileWritten": False,
                "note": "Opened directly from its original path by the local read-only SQLite reader.",
                "library": library,
            }
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def _path_for(self, library_id):
        path = self._libraries.get(str(library_id))
        if path is None:
            raise ValueError("Open the SQLite library again; this desktop session has expired.")
        if not path.is_file():
            self._libraries.pop(str(library_id), None)
            raise ValueError("The selected SQLite library is no longer available at its original path.")
        return path

    def get_optimizer_library(self, library_id):
        try:
            return {"ok": True, **_library_reader().inspect_library(self._path_for(library_id))}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_optimizer_candidates(self, library_id, encounter_id, offset=0, limit=50):
        try:
            return {"ok": True, **_library_reader().list_candidates(
                self._path_for(library_id), encounter_id, offset=offset, limit=limit)}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def get_optimizer_candidate(self, library_id, candidate_id):
        try:
            candidate = _library_reader().get_candidate(self._path_for(library_id), candidate_id)
            if candidate is None:
                raise ValueError("The selected candidate is not in this library.")
            return {"ok": True, **candidate}
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def preview_battle(self, request):
        try:
            if not isinstance(request, dict) or request.get("schema") != "ka-battle-preview-1":
                raise ValueError("Unsupported battle preview request.")
            return _preview_reader().build_preview(request.get("scenario"))
        except Exception as error:
            return {"schema": "ka-battle-preview-error-1", "message": str(error)}


def asset_handler(app=APP):
    class Assets(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def translate_path(self, path):
            relative = unquote(urlsplit(path).path).lstrip("/") or "synthetic-legal.desktop.html"
            for root in (app / "desktop-dist", app / "public"):
                resolved = (root / relative).resolve()
                if resolved.is_relative_to(root.resolve()) and resolved.is_file():
                    return str(resolved)
            return str(app / "desktop-dist" / "__missing__")

        def list_directory(self, path):
            self.send_error(404)
            return None

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'")
            super().end_headers()
    return Assets


def main():
    page = APP / "desktop-dist" / "synthetic-legal.desktop.html"
    if not page.is_file():
        raise RuntimeError("Desktop UI has not been built. Run Install Synthetic to Legal Desktop.ps1 once.")
    import webview
    bridge = SyntheticLegalBridge()
    server = ThreadingHTTPServer(("127.0.0.1", 0), asset_handler())
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--disk-cache-size=33554432 --disable-features=msEdgeSidebarV2"
    )
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    window = webview.create_window(
        "Kingdom Adventurers · Synthetic to Legal Builds",
        f"http://127.0.0.1:{server.server_port}/synthetic-legal.desktop.html",
        js_api=bridge, width=1320, height=900, min_size=(980, 700))
    bridge._window = window
    try:
        # Keep React's PlayerProfile/settings in WebView2 local storage. The dedicated data folder
        # sits beside the recent-library list and avoids relying on WebView2's implicit profile path.
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA_DIR / "webview-data"))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as error:
        import traceback
        settings_dir = RECENT_FILE.parent
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "startup-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "Synthetic to Legal could not start", 0x10)
        else:
            raise

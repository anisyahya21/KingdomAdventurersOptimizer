"""Direct-path desktop bridge check using an isolated SQLite library and a stub native chooser."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from synthetic_legal_desktop import SyntheticLegalBridge


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_library(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE candidate (
            id TEXT PRIMARY KEY, scenario TEXT NOT NULL, label TEXT, source TEXT,
            stats TEXT, created INTEGER NOT NULL
        );
        CREATE TABLE candidate_meta (
            id TEXT PRIMARY KEY, encounter INTEGER NOT NULL, defeat INTEGER NOT NULL, region TEXT
        );
    """)
    connection.execute("INSERT INTO meta VALUES ('schema', '1')")
    scenario = json.dumps({"encounterId": 7, "ownUnits": [{"name": "Sample human", "human": True}]})
    connection.execute("INSERT INTO candidate VALUES (?, ?, ?, ?, ?, ?)",
                       ("candidate-direct-path", scenario, "Path fixture", "test", "{}", 1))
    connection.execute("INSERT INTO candidate_meta VALUES (?, ?, ?, ?)",
                       ("candidate-direct-path", 7, 3, "fixture"))
    connection.commit()
    connection.close()


def main():
    root = HERE.parents[1]
    token = uuid4().hex
    source = root / f".synthetic-legal-desktop-check-{token}.db"
    settings = root / f".synthetic-legal-desktop-settings-{token}.json"
    try:
        make_library(source)
        before = digest(source)

        class FileDialog:
            OPEN = "open"

        fake_webview = types.SimpleNamespace(FileDialog=FileDialog)
        bridge = SyntheticLegalBridge(settings)

        class NativeWindowStub:
            def __init__(self):
                self.selected = None

            def create_file_dialog(self, dialog, **options):
                assert dialog == FileDialog.OPEN
                assert options["allow_multiple"] is False
                assert ".sqlite" in options["file_types"][0] and ".db" in options["file_types"][0]
                self.selected = str(source.resolve())
                return [self.selected]

        window = NativeWindowStub()
        bridge._window = window
        with patch.dict(sys.modules, {"webview": fake_webview}):
            opened = bridge.open_optimizer_library()
        assert opened["ok"] is True, opened
        assert Path(opened["libraryPath"]) == source.resolve()
        assert opened["library"]["candidateCount"] == 1
        assert opened["readOnly"] is True and opened["sourceFileWritten"] is False

        page = bridge.get_optimizer_candidates(opened["libraryId"], 7, 0, 50)
        assert page["ok"] is True and page["total"] == 1, page
        assert page["candidates"][0]["id"] == "candidate-direct-path"
        recent = bridge.list_recent_optimizer_libraries()
        assert recent["libraries"] == [{"path": str(source.resolve()), "filename": source.name}]
        assert digest(source) == before, "opening or browsing changed the selected SQLite file"
        assert [path for path in root.glob(f".synthetic-legal-desktop-check-{token}*.db")] == [source], "desktop bridge created a database copy"
        scenario_path = HERE.parents[2] / "RE-evidence" / "20260919-configurable-battle-setup" / "run-battle-16.9" / "scenarios" / "R.scenario.json"
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        preview = bridge.preview_battle({"schema": "ka-battle-preview-1", "scenario": scenario})
        assert preview.get("schema") == "ka-battle-preview-1", preview
        assert any(unit.get("side") == "ally" for unit in preview.get("units", [])), preview
        print("PASS native chooser -> absolute path -> direct SQLite reader -> unchanged source -> recent path -> local runtime preview")
    finally:
        for path in (source, Path(str(source) + "-wal"), Path(str(source) + "-shm"),
                     Path(str(source) + "-journal"), settings, settings.with_suffix(".tmp")):
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

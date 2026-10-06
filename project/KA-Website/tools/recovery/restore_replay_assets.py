"""Restore only the replay's current, source-backed website assets.

Default invocation opens a small Tk progress window.  Use --headless only when
the caller explicitly wants no window.  Exporter output is isolated under a
unique coordination/native-finish staging directory and is retained for audit.
This script never replaces an existing differing asset or semantic JSON file.

Run from any directory:
    python KA-Website/tools/recovery/restore_replay_assets.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "KA-Website/artifacts/kingdom-adventures"
PUBLIC = APP / "public"
COORDINATION = ROOT / "coordination/native-finish"
PROGRESS_PATH = COORDINATION / "asset-restore-progress.json"
EXPECTED_HUMAN_PNGS = 742
EXPECTED_MONSTER_PNGS = 169
EXPECTED_TREASURE_PNGS = 41
EXPECTED_ANIMATION_PNGS = 3

# These are the 36 URLs read from the current battle-replay.ts source.  The
# historical inventory documents 38; the two unreferenced historical entries
# are deliberately outside this allowlist.
BATTLE_SOURCES = {
    "arena/bg_00.png": "battle/bg_00.png",
    "arena/bg_01.png": "battle/bg_01.png",
    "arena/bg_02.png": "battle/bg_02.png",
    "arena/bg_03.png": "battle/bg_03.png",
    "arena/bg_04.png": "battle/bg_04.png",
    "arena/bg_05.png": "battle/bg_05.png",
    "arena/bg_06.png": "battle/bg_06.png",
    "arena/bg_99.png": "battle/bg_99.png",
    "arena/bg_100.png": "battle/bg_100.png",
    "arena/bg_101.png": "battle/bg_101.png",
    "arena/bg_102.png": "battle/bg_102.png",
    "balloon/boss_balloon.png": "com/boss_balloon.png",
    "balloon/skill_balloon.png": "game/skill_balloon.png",
    "bubble/dialog_base.png": "com_2/dialog_base.png",
    "bubble/fukidashi_back.png": "com/fukidashi_back.png",
    "effects/flash.png": "effect/flash.png",
    "effects/impact-effect_00.png": "effect/effect_00.png",
    "effects/orb-trio.png": "effect/effect_24.png",
    "effects/smoke-strip.png": "effect/effect_37.png",
    "effects/sparkle-com.png": "com/effect_00.png",
    "indicators/arrow_00.png": "effect/arrow_00.png",
    "indicators/arw_ud.png": "com/arw_ud.png",
    "indicators/skew_arrow.png": "com/skew_arrow.png",
    "numbers/num_m_02.png": "com/num_m_02.png",
    "numbers/num_red.png": "com/num_red.png",
    "text/combat-text-en.png": "game/English.lproj/attack_str.png",
    "text/combat-text-jp.png": "game/attack_str.png",
    "ui/battle_gauge.png": "gauge/battle_gauge.png",
    "ui/boss.png": "game/boss.png",
    "ui/command_bar.png": "battle/command_bar.png",
    "ui/command_button.png": "battle/command_button.png",
    "ui/exp_box.png": "battle/exp_box.png",
    "ui/item_slot_mini.png": "com/item_slot_mini.png",
    "ui/member_info_bar.png": "battle/member_info_bar.png",
    "ui/treasure_bar.png": "battle/treasure_bar.png",
    "ui/zzz.png": "game/zzz.png",
}

BODY_FILES = {
    "all_monster_l_wairo02.png",
    "ex_monster_m_01.png",
    "ex_monster_m_09.png",
    "ex_monster_m_11.png",
    "ex_monster_m_16.png",
    "ex_monster_m_17.png",
    "iwa_monster_l_01.png",
    "iwa_monster_s_00.png",
    "iwa_monster_xl_00.png",
    "kazan_monster_l_00.png",
    "shadow_l.png",
    "shadow_m.png",
    "shadow_s.png",
    "shadow_xl.png",
}
ANIMATION_FILES = {"iwa_monster_s_00.png", "mizu_shadow_s.png", "shadow_s.png"}


def _expected_total() -> int:
    assets = (
        EXPECTED_HUMAN_PNGS
        + EXPECTED_MONSTER_PNGS
        + EXPECTED_TREASURE_PNGS
        + len(BATTLE_SOURCES)
        + 2 * len(BODY_FILES)
        + EXPECTED_ANIMATION_PNGS
    )
    # Each staged asset/manifest is followed by one destination reconciliation.
    # There are two human manifests plus one each for monster, treasure, animation.
    manifests = 5
    return 2 * (assets + manifests)


class Progress:
    def __init__(self, stage_dir: Path):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.state = {
            "stage": "Starting",
            "done": 0,
            "total": _expected_total(),
            "elapsedSeconds": 0.0,
            "ratePerSecond": 0.0,
            "etaSeconds": None,
            "detail": "Preparing bounded source and path checks",
            "outputPath": str(stage_dir),
            "errors": [],
            "warnings": [
                "Historical battle inventory lists 38 images; the current renderer references 36. Only those 36 are eligible for restoration."
            ],
        }
        self.write()

    def update(self, *, stage: str | None = None, detail: str | None = None) -> None:
        with self.lock:
            if stage is not None:
                self.state["stage"] = stage
            if detail is not None:
                self.state["detail"] = detail
            self._refresh_locked()

    def advance(self, count: int = 1, detail: str | None = None) -> None:
        with self.lock:
            self.state["done"] = min(self.state["total"], self.state["done"] + count)
            if detail is not None:
                self.state["detail"] = detail
            self._refresh_locked()

    def warning(self, message: str) -> None:
        with self.lock:
            self.state["warnings"].append(message)
            self._refresh_locked()

    def error(self, message: str) -> None:
        with self.lock:
            self.state["errors"].append(message)
            self._refresh_locked()

    def finish(self, success: bool) -> None:
        with self.lock:
            self.state["stage"] = "Complete" if success else "Finished with blockers"
            self.state["detail"] = "Review the durable report and retained staging directory."
            self._refresh_locked()

    def snapshot(self) -> dict:
        with self.lock:
            self._refresh_locked(write=False)
            return dict(self.state)

    def _refresh_locked(self, write: bool = True) -> None:
        elapsed = max(0.0, time.monotonic() - self.started)
        done = self.state["done"]
        self.state["elapsedSeconds"] = round(elapsed, 2)
        self.state["ratePerSecond"] = round(done / elapsed, 2) if elapsed else 0.0
        # Per-file encoding and I/O costs differ; a single aggregate ETA would be misleading.
        self.state["etaSeconds"] = None
        if write:
            self.write_locked()

    def write(self) -> None:
        with self.lock:
            self._refresh_locked()

    def write_locked(self) -> None:
        PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = PROGRESS_PATH.with_name(f"{PROGRESS_PATH.name}.{os.getpid()}.tmp")
        payload = json.dumps(self.state, ensure_ascii=False, indent=2) + "\n"
        temp.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(temp, PROGRESS_PATH)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.025)


def _within_root(path: Path, *, reject_symlinks: bool = True) -> Path:
    candidate = path.absolute()
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(ROOT):
        raise RuntimeError(f"Path escapes workspace: {path} -> {resolved}")
    if reject_symlinks:
        current = ROOT
        try:
            relative = candidate.relative_to(ROOT)
        except ValueError as exc:
            raise RuntimeError(f"Path is outside workspace: {path}") from exc
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeError(f"Refusing symlink in restoration path: {current}")
    return resolved


def _source(path: Path) -> Path:
    resolved = _within_root(path, reject_symlinks=False)
    if not resolved.is_file():
        raise FileNotFoundError(f"Required source file is missing: {path}")
    return resolved


def _source_dir(path: Path) -> Path:
    resolved = _within_root(path, reject_symlinks=False)
    if not resolved.is_dir():
        raise FileNotFoundError(f"Required source directory is missing: {path}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_value(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_pairs)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Exporter source changed: expected one {label} target, found {count}")
    return text.replace(old, new, 1)


def _check_static_sources() -> tuple[list[str], dict[str, str]]:
    battle_ts = _source(APP / "src/lib/battle-replay.ts").read_text(encoding="utf-8")
    current_urls = set(re.findall(r"['\"](/battle-assets/[^'\"]+\.png)['\"]", battle_ts))
    expected_urls = {"/battle-assets/" + key for key in BATTLE_SOURCES}
    if current_urls != expected_urls or len(current_urls) != 36:
        raise RuntimeError(
            "Current battle-replay.ts URL set no longer matches the audited 36-file source allowlist; "
            f"missing={sorted(current_urls ^ expected_urls)}"
        )

    body_path = _source(APP / "src/lib/native-body.ts")
    body_text = body_path.read_text(encoding="utf-8")
    current_body = set(
        re.findall(r'png:\s*"/battle-assets/monster-original/([^\"]+\.png)"', body_text)
    )
    if current_body != BODY_FILES or len(current_body) != 14:
        raise RuntimeError(
            "Current native-body.ts asset set no longer matches the audited 14-file body allowlist; "
            f"missing={sorted(current_body ^ BODY_FILES)}"
        )

    fingerprints = {}
    for name in sorted(BODY_FILES):
        stem = Path(name).stem
        match = re.search(
            rf"(?m)^  {re.escape(stem)}: \{{(?P<body>.*?)(?=^  \}},?\s*$)",
            body_text,
            re.S,
        )
        if not match:
            raise RuntimeError(f"Could not resolve current native-body proof block for {stem}")
        hash_match = re.search(r'optSha256_16:\s*"([0-9a-f]{16})"', match.group("body"))
        if not hash_match:
            raise RuntimeError(f"Missing current OPT fingerprint for {stem}")
        fingerprints[stem] = hash_match.group(1)

    source_root = _source_dir(ROOT / "RE-evidence/20260911-treasure/monster-original")
    for name in sorted(BODY_FILES):
        image = _source(source_root / name)
        opt = _source(source_root / f"{Path(name).stem}.opt")
        if not _sha256(opt).startswith(fingerprints[Path(name).stem]):
            raise RuntimeError(f"Source OPT hash disagrees with native-body.ts: {opt.name}")
        # Read the PNG header only; no image decoding is needed for this integrity check.
        with image.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError(f"Required source is not a PNG: {image}")

    return sorted(current_urls), fingerprints


def _with_image_save_counter(target_dir: Path, on_saved: Callable[[], None]):
    """Temporarily count only successful PIL saves directly into target_dir."""
    from PIL import Image

    original = Image.Image.save
    target = target_dir.resolve(strict=False)

    def counted(image, fp, *args, **kwargs):
        result = original(image, fp, *args, **kwargs)
        if isinstance(fp, (str, os.PathLike)):
            saved = Path(fp).resolve(strict=False)
            if saved.is_relative_to(target):
                on_saved()
        return result

    Image.Image.save = counted
    return Image, original


def _run_dynamic_export(source_path: Path, source_text: str, name: str) -> str:
    output = io.StringIO()
    previous_cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        with contextlib.redirect_stdout(output):
            namespace = {"__name__": name, "__file__": str(source_path), "__package__": None}
            exec(compile(source_text, str(source_path), "exec"), namespace)
    finally:
        os.chdir(previous_cwd)
    return output.getvalue()


class CountedShutil:
    def __init__(self, delegate, target_dir: Path, on_copy: Callable[[], None]):
        self._delegate = delegate
        self._target = target_dir.resolve(strict=False)
        self._on_copy = on_copy

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def copy2(self, src, dst, *args, **kwargs):
        result = self._delegate.copy2(src, dst, *args, **kwargs)
        dest = Path(dst).resolve(strict=False)
        if dest.is_relative_to(self._target):
            self._on_copy()
        return result


def _stage_human(stage: Path, progress: Progress, logger: logging.Logger) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    source = _source(ROOT / "KA-Website/tools/bake_character_frontend_assets.py")
    spec = importlib.util.spec_from_file_location("ka_asset_restore_human_export", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the canonical human asset exporter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    human_sources = [
        _source(module.KA_ASSETS / directory / image.name)
        for directory in module.SPRITE_DIRS
        for image in sorted((module.KA_ASSETS / directory).glob("*.png"))
    ]
    if len(human_sources) != EXPECTED_HUMAN_PNGS:
        raise RuntimeError(
            f"Canonical human source tree has {len(human_sources)} PNGs; expected {EXPECTED_HUMAN_PNGS}"
        )
    stage_app = stage / "app"
    (stage_app / "public").mkdir(parents=True, exist_ok=True)
    stage_api = stage / "api-server/data/sprite-rules.json"
    stage_front = stage_app / "public/character_sprites"
    module.FRONTEND_PUBLIC = stage_front
    module.FRONTEND_RULES_OUT = stage_front / "character-rules.json"
    module.API_RULES_OUT = stage_api
    delegate = module.shutil
    module.shutil = CountedShutil(delegate, stage_front, lambda: progress.advance(detail="Baking original human PNGs"))
    try:
        module.main()
    finally:
        module.shutil = delegate
    files = sorted(stage_front.glob("*/*.png"))
    if len(files) != EXPECTED_HUMAN_PNGS:
        raise RuntimeError(f"Human exporter produced {len(files)} PNGs; expected {EXPECTED_HUMAN_PNGS}")
    if {path.parent.name for path in files} != set(module.SPRITE_DIRS):
        raise RuntimeError("Human exporter output directory set does not match its canonical list")
    progress.advance(2, detail="Staged frontend and API sprite rules")
    logger.info("Human exporter staged %d PNGs", len(files))
    assets = [(path, PUBLIC / path.relative_to(stage_front.parent)) for path in files]
    manifests = [
        (module.FRONTEND_RULES_OUT, PUBLIC / "character_sprites/character-rules.json"),
        (stage_api, ROOT / "KA-Website/artifacts/api-server/data/sprite-rules.json"),
    ]
    return assets, manifests


def _stage_monsters(stage: Path, progress: Progress, logger: logging.Logger) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    exporter = _source(ROOT / "RE-evidence/20260911-treasure/export_monster_sprites.py")
    source = exporter.read_text(encoding="utf-8")
    stage_app = stage / "app"
    stage_public = stage_app / "public"
    (stage_public / "monster-sprites").mkdir(parents=True, exist_ok=True)
    (stage_app / "src/game-data").mkdir(parents=True, exist_ok=True)
    original = source
    source = _replace_once(
        source,
        "W=Path('KA-Website/artifacts/kingdom-adventures')",
        f"W=Path({str(stage_app)!r})",
        "monster exporter output root",
    )
    source = _replace_once(
        source,
        "sheet.save(R/'monster-sprites-contact.png')",
        f"sheet.save(Path({str(stage / 'monster-sprites-contact.png')!r}))",
        "monster contact-sheet output",
    )
    if source == original:
        raise RuntimeError("Monster exporter staging redirection did not change its output targets")
    image_module, original_save = _with_image_save_counter(
        stage_public / "monster-sprites", lambda: progress.advance(detail="Rendering original monster sprites")
    )
    try:
        exporter_output = _run_dynamic_export(exporter, source, "ka_asset_restore_monsters")
    finally:
        image_module.Image.save = original_save
    (stage / "monster-export.log").write_text(exporter_output, encoding="utf-8")
    data_path = stage_app / "src/game-data/monster-sprites.json"
    data = _json_value(data_path)
    files = sorted((stage_public / "monster-sprites").glob("*.png"))
    if len(data) != EXPECTED_MONSTER_PNGS or len(files) != EXPECTED_MONSTER_PNGS:
        raise RuntimeError(
            f"Monster exporter produced {len(data)} rows and {len(files)} PNGs; expected {EXPECTED_MONSTER_PNGS} each"
        )
    if len({row["id"] for row in data}) != EXPECTED_MONSTER_PNGS:
        raise RuntimeError("Monster sprite IDs are not unique")
    if {path.name for path in files} != {f"{row['id']}.png" for row in data}:
        raise RuntimeError("Monster PNG names do not match the staged manifest")
    progress.advance(detail="Staged monster-sprite manifest")
    logger.info("Monster exporter staged %d portraits", len(files))
    assets = [(path, PUBLIC / "monster-sprites" / path.name) for path in files]
    manifests = [(data_path, APP / "src/game-data/monster-sprites.json")]
    return assets, manifests


def _stage_treasure(stage: Path, progress: Progress, logger: logging.Logger) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    exporter = _source(ROOT / "RE-evidence/20260911-treasure/export_sources.py")
    source = exporter.read_text(encoding="utf-8")
    stage_app = stage / "app"
    stage_public = stage_app / "public"
    (stage_public / "treasure-icons").mkdir(parents=True, exist_ok=True)
    (stage_app / "src/game-data").mkdir(parents=True, exist_ok=True)
    original = source
    source = _replace_once(
        source,
        "W=Path('KA-Website/artifacts/kingdom-adventures')",
        f"W=Path({str(stage_app)!r})",
        "treasure exporter output root",
    )
    source = _replace_once(
        source,
        "(R/'table-audit.json').write_text",
        f"(Path({str(stage / 'table-audit.json')!r})).write_text",
        "treasure audit output",
    )
    source = _replace_once(
        source,
        "thumb.save(R/'chests.png')",
        f"thumb.save(Path({str(stage / 'treasure-contact.png')!r}))",
        "treasure contact-sheet output",
    )
    if source == original:
        raise RuntimeError("Treasure exporter staging redirection did not change its output targets")
    image_module, original_save = _with_image_save_counter(
        stage_public / "treasure-icons", lambda: progress.advance(detail="Rendering original treasure icons")
    )
    try:
        exporter_output = _run_dynamic_export(exporter, source, "ka_asset_restore_treasure")
    finally:
        image_module.Image.save = original_save
    (stage / "treasure-export.log").write_text(exporter_output, encoding="utf-8")
    data_path = stage_app / "src/game-data/native-treasure.json"
    data = _json_value(data_path)
    icons = sorted((stage_public / "treasure-icons").glob("*.png"))
    audit_path = stage / "table-audit.json"
    audit = _json_value(audit_path)
    if len(icons) != EXPECTED_TREASURE_PNGS or audit.get("icons") != EXPECTED_TREASURE_PNGS:
        raise RuntimeError(
            f"Treasure exporter staged {len(icons)} icons; audit expected {EXPECTED_TREASURE_PNGS}"
        )
    icon_paths = {
        row["icon"].removeprefix("/treasure-icons/")
        for row in data.get("treasures", [])
        if row.get("icon")
    }
    if icon_paths != {path.name for path in icons}:
        raise RuntimeError("Treasure icon filenames do not match native-treasure.json")
    progress.advance(detail="Staged native treasure catalog")
    logger.info("Treasure exporter staged %d icons", len(icons))
    assets = [(path, PUBLIC / "treasure-icons" / path.name) for path in icons]
    manifests = [(data_path, APP / "src/game-data/native-treasure.json")]
    return assets, manifests


def _stage_battle_sources(stage: Path, progress: Progress, logger: logging.Logger) -> list[tuple[Path, Path]]:
    asset_root = _source_dir(APP / "tmp/KA_assets")
    pairs = []
    for relative_output, relative_source in sorted(BATTLE_SOURCES.items()):
        source = _source(asset_root / relative_source)
        dest_rel = Path("battle-assets") / relative_output
        staged = stage / "app/public" / dest_rel
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, staged)
        progress.advance(detail=f"Staging current battle source: {relative_output}")
        pairs.append((staged, PUBLIC / dest_rel))
    logger.info("Staged current battle source allowlist: %d PNGs", len(pairs))
    return pairs


def _stage_body_assets(stage: Path, progress: Progress, logger: logging.Logger) -> list[tuple[Path, Path]]:
    source_root = _source_dir(ROOT / "RE-evidence/20260911-treasure/monster-original")
    stage_root = stage / "app/public/battle-assets/monster-original"
    pairs = []
    for name in sorted(BODY_FILES):
        for filename in (name, f"{Path(name).stem}.opt"):
            source = _source(source_root / filename)
            staged = stage_root / filename
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, staged)
            progress.advance(detail=f"Staging native monster body asset: {filename}")
            pairs.append((staged, PUBLIC / "battle-assets/monster-original" / filename))
    logger.info("Staged current native body allowlist: %d PNG/OPT files", len(pairs))
    return pairs


def _stage_animation(stage: Path, progress: Progress, logger: logging.Logger) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    exporter = _source(ROOT / "KA-Website/tools/recovery/export_battle_animation.py")
    spec = importlib.util.spec_from_file_location("ka_asset_restore_battle_animation", exporter)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the canonical battle animation exporter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stage_app = stage / "app"
    stage_public = stage_app / "public/battle-assets/anim"
    stage_public.mkdir(parents=True, exist_ok=True)
    staged_manifest = stage_app / "src/game-data/battle-animation.json"
    staged_manifest.parent.mkdir(parents=True, exist_ok=True)
    current_manifest = APP / "src/game-data/battle-animation.json"
    if current_manifest.is_file():
        shutil.copyfile(_source(current_manifest), staged_manifest)
    module.PUBLIC = stage_public
    module.MANIFEST = staged_manifest
    delegate = module.shutil

    class AnimationShutil:
        def __getattr__(self, name):
            return getattr(delegate, name)

        def copyfile(self, src, dst, *args, **kwargs):
            result = delegate.copyfile(src, dst, *args, **kwargs)
            if Path(dst).resolve(strict=False).is_relative_to(stage_public.resolve(strict=False)):
                progress.advance(detail="Staging original battle animation textures")
            return result

    module.shutil = AnimationShutil()
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            module.main()
    finally:
        module.shutil = delegate
    (stage / "animation-export.log").write_text(output.getvalue(), encoding="utf-8")
    data = _json_value(staged_manifest)
    copied = set(data.get("copied", []))
    images = sorted(stage_public.glob("*.png"))
    if copied != ANIMATION_FILES or {path.name for path in images} != ANIMATION_FILES:
        raise RuntimeError(
            "Battle animation exporter output differs from the current 3-file allowlist; "
            f"manifest={sorted(copied)}, files={sorted(path.name for path in images)}"
        )
    progress.advance(detail="Staged battle-animation manifest")
    logger.info("Battle animation exporter staged %d textures", len(images))
    assets = [(path, PUBLIC / "battle-assets/anim" / path.name) for path in images]
    manifests = [(staged_manifest, current_manifest)]
    return assets, manifests


def _inspect_asset(pair: tuple[Path, Path]) -> dict:
    staged, destination = pair
    _within_root(staged)
    _within_root(destination)
    if not staged.is_file():
        return {"staged": staged, "destination": destination, "problem": "staged output missing"}
    staged_hash = _sha256(staged)
    if not destination.exists():
        return {"staged": staged, "destination": destination, "stagedHash": staged_hash, "action": "missing"}
    if not destination.is_file():
        return {
            "staged": staged,
            "destination": destination,
            "stagedHash": staged_hash,
            "problem": "destination exists but is not a regular file",
        }
    existing_hash = _sha256(destination)
    return {
        "staged": staged,
        "destination": destination,
        "stagedHash": staged_hash,
        "existingHash": existing_hash,
        "action": "same" if staged_hash == existing_hash else "conflict",
    }


def _copy_new_without_replace(staged: Path, destination: Path, expected_hash: str) -> tuple[str, str | None]:
    _within_root(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _within_root(destination.parent)
    if destination.exists():
        actual = _sha256(destination) if destination.is_file() else "<non-file>"
        return ("same", actual) if actual == expected_hash else ("conflict", actual)
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.asset-restore-tmp")
    with staged.open("rb") as src, temp.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    if _sha256(temp) != expected_hash:
        raise IOError(f"Temporary copy hash mismatch for {destination}")
    try:
        os.link(temp, destination)
    except FileExistsError:
        actual = _sha256(destination) if destination.is_file() else "<non-file>"
        return ("same", actual) if actual == expected_hash else ("conflict", actual)
    finally:
        if temp.exists() and destination.exists():
            temp.unlink()
    return "restored", expected_hash


def _reconcile_group(
    name: str,
    assets: list[tuple[Path, Path]],
    manifests: list[tuple[Path, Path]],
    progress: Progress,
    logger: logging.Logger,
) -> dict:
    result = {
        "status": "preflighting",
        "stagedAssets": len(assets),
        "restoredAssets": 0,
        "unchangedAssets": 0,
        "blockers": [],
        "differences": [],
        "manifestResults": [],
    }
    progress.update(stage=f"Preflight: {name}", detail=f"Checking {len(assets)} staged asset files")
    inspected = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, max(1, len(assets)))) as executor:
        futures = [executor.submit(_inspect_asset, pair) for pair in assets]
        for future in concurrent.futures.as_completed(futures):
            inspected.append(future.result())
            progress.advance(detail=f"Preflight: {name} assets ({len(inspected)}/{len(assets)})")
    inspected.sort(key=lambda item: str(item["destination"]))
    for item in inspected:
        if item.get("problem"):
            result["blockers"].append(f"{item['destination']}: {item['problem']}")
        elif item["action"] == "conflict":
            result["differences"].append(
                {
                    "destination": str(item["destination"]),
                    "existingSha256": item["existingHash"],
                    "stagedSha256": item["stagedHash"],
                }
            )

    manifest_plan = []
    for staged, destination in manifests:
        try:
            _within_root(staged)
            _within_root(destination)
            if not staged.is_file():
                raise FileNotFoundError(f"staged manifest missing: {staged}")
            staged_value = _json_value(staged)
            if destination.exists():
                if not destination.is_file():
                    raise ValueError("destination exists but is not a regular file")
                existing_value = _json_value(destination)
                if staged_value != existing_value:
                    result["blockers"].append(f"Semantic JSON differs; preserving existing file: {destination}")
                    result["differences"].append(
                        {
                            "destination": str(destination),
                            "kind": "semantic-json",
                            "existingSha256": _sha256(destination),
                            "stagedSha256": _sha256(staged),
                        }
                    )
                    action = "conflict"
                else:
                    action = "same"
            else:
                action = "missing"
            manifest_plan.append((staged, destination, action, _sha256(staged)))
            result["manifestResults"].append({"destination": str(destination), "action": action})
        except Exception as exc:
            result["blockers"].append(f"Manifest preflight failed for {destination}: {exc}")
            result["manifestResults"].append({"destination": str(destination), "action": "blocked", "error": str(exc)})
        finally:
            progress.advance(detail=f"Preflight: {name} manifests")

    for item in inspected:
        if item.get("action") == "missing":
            result["missingAssets"] = result.get("missingAssets", 0) + 1
        elif item.get("action") == "same":
            result["unchangedAssets"] += 1

    if result["blockers"]:
        result["status"] = "blocked"
        logger.warning("Group %s blocked: %s", name, " | ".join(result["blockers"]))
        return result

    try:
        for item in inspected:
            if item["action"] == "same":
                continue
            state, actual_hash = _copy_new_without_replace(
                item["staged"], item["destination"], item["stagedHash"]
            )
            if state == "conflict":
                raise RuntimeError(
                    f"Destination changed during publication; preserving it: {item['destination']} "
                    f"(now {actual_hash})"
                )
            if state == "restored":
                result["restoredAssets"] += 1
            else:
                result["unchangedAssets"] += 1
        for staged, destination, action, expected_hash in manifest_plan:
            if action == "missing":
                state, actual_hash = _copy_new_without_replace(staged, destination, expected_hash)
                if state == "conflict":
                    raise RuntimeError(
                        f"Manifest appeared with different bytes during publication; preserving it: {destination}"
                    )
                result["manifestResults"].append({"destination": str(destination), "publish": state})
        result["status"] = "complete"
    except Exception as exc:
        result["status"] = "partial; rerunnable"
        result["blockers"].append(str(exc))
        logger.exception("Group %s publication stopped safely", name)
    logger.info(
        "Group %s: %s; restored=%d unchanged=%d",
        name,
        result["status"],
        result["restoredAssets"],
        result["unchangedAssets"],
    )
    return result


def _set_up_log(stage: Path) -> logging.Logger:
    logger = logging.getLogger(f"asset_restore_{stage.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(stage / "restore.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _write_report(stage: Path, report: dict) -> None:
    path = stage / "report.json"
    temp = path.with_name("report.json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def run_restore(progress: Progress) -> tuple[bool, Path]:
    stage = Path(progress.snapshot()["outputPath"])
    _within_root(stage)
    stage.mkdir(parents=True, exist_ok=False)
    logger = _set_up_log(stage)
    report = {
        "createdAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workspace": str(ROOT),
        "staging": str(stage),
        "scope": {
            "humanPngs": EXPECTED_HUMAN_PNGS,
            "monsterPngs": EXPECTED_MONSTER_PNGS,
            "treasurePngs": EXPECTED_TREASURE_PNGS,
            "battleCurrentUrls": len(BATTLE_SOURCES),
            "historicalBattleCount": 38,
            "monsterBodyPngAndOptFiles": 2 * len(BODY_FILES),
            "animationPngs": EXPECTED_ANIMATION_PNGS,
        },
        "groups": {},
        "warnings": list(progress.snapshot()["warnings"]),
        "errors": [],
        "status": "running",
    }
    success = True
    try:
        progress.update(stage="Source manifest checks", detail="Verifying current URLs, body proof hashes, and workspace paths")
        _check_static_sources()
        (stage / "app/public").mkdir(parents=True, exist_ok=True)
        (stage / "app/src/game-data").mkdir(parents=True, exist_ok=True)

        stages: list[tuple[str, Callable[[], tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]]]] = [
            ("human", lambda: _stage_human(stage, progress, logger)),
            ("monsters", lambda: _stage_monsters(stage, progress, logger)),
            ("treasure", lambda: _stage_treasure(stage, progress, logger)),
            ("battle animation", lambda: _stage_animation(stage, progress, logger)),
        ]
        for name, stage_fn in stages:
            progress.update(stage=f"Stage: {name}", detail="Running canonical exporter into isolated staging")
            try:
                assets, manifests = stage_fn()
                report["groups"][name] = _reconcile_group(name, assets, manifests, progress, logger)
                if report["groups"][name]["status"] != "complete":
                    success = False
            except Exception as exc:
                success = False
                report["groups"][name] = {"status": "blocked", "blockers": [str(exc)]}
                report["errors"].append(f"{name}: {exc}")
                progress.error(f"{name}: {exc}")
                logger.exception("Group %s failed; stage retained", name)
                # Fill the units for a failed stage as skipped decisions so the counter
                # still represents completed work units without claiming successful output.
                expected = {
                    "human": EXPECTED_HUMAN_PNGS + 2,
                    "monsters": EXPECTED_MONSTER_PNGS + 1,
                    "treasure": EXPECTED_TREASURE_PNGS + 1,
                    "battle animation": EXPECTED_ANIMATION_PNGS + 1,
                }[name]
                # Generation callback may already have advanced; do not fabricate progress.
                logger.info("Failed group retained after %d expected stage units", expected)

        progress.update(stage="Stage: battle current sources", detail="Copying only the 36 URLs in current battle-replay.ts")
        try:
            assets = _stage_battle_sources(stage, progress, logger)
            result = _reconcile_group("battle sources", assets, [], progress, logger)
            report["groups"]["battle sources"] = result
            success = success and result["status"] == "complete"
        except Exception as exc:
            success = False
            report["groups"]["battle sources"] = {"status": "blocked", "blockers": [str(exc)]}
            report["errors"].append(f"battle sources: {exc}")
            progress.error(f"battle sources: {exc}")
            logger.exception("Battle source restoration failed; stage retained")

        progress.update(stage="Stage: native monster body", detail="Copying current PNG/OPT allowlist with source proof hashes")
        try:
            assets = _stage_body_assets(stage, progress, logger)
            result = _reconcile_group("monster body", assets, [], progress, logger)
            report["groups"]["monster body"] = result
            success = success and result["status"] == "complete"
        except Exception as exc:
            success = False
            report["groups"]["monster body"] = {"status": "blocked", "blockers": [str(exc)]}
            report["errors"].append(f"monster body: {exc}")
            progress.error(f"monster body: {exc}")
            logger.exception("Native body restoration failed; stage retained")

    except Exception as exc:
        success = False
        report["errors"].append(str(exc))
        progress.error(str(exc))
        logger.exception("Global source/path preflight failed")
    finally:
        report["status"] = "complete" if success else "blocked or partial"
        report["progress"] = progress.snapshot()
        _write_report(stage, report)
        progress.finish(success)
        logger.info("Finished with status %s", report["status"])
    return success, stage


def _show_window(progress: Progress, worker: threading.Thread, start_worker: Callable[[], None]) -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        raise RuntimeError(f"Tk is unavailable; rerun with --headless to opt out of the monitor: {exc}") from exc

    root = tk.Tk()
    root.title("Kingdom Adventures asset restoration")
    root.geometry("650x245")
    root.minsize(540, 220)
    root.configure(padx=14, pady=12)
    root.columnconfigure(0, weight=1)
    title = ttk.Label(root, text="Restoring verified replay assets", font=("Segoe UI", 12, "bold"))
    title.grid(row=0, column=0, sticky="w")
    stage_var = tk.StringVar(value="Starting")
    detail_var = tk.StringVar(value="Preparing")
    counters_var = tk.StringVar(value="0 / 0")
    elapsed_var = tk.StringVar(value="Elapsed 0s · rate unknown · ETA unknown")
    output_var = tk.StringVar(value=str(progress.snapshot()["outputPath"]))
    ttk.Label(root, textvariable=stage_var).grid(row=1, column=0, sticky="w", pady=(10, 2))
    bar = ttk.Progressbar(root, mode="determinate", maximum=100)
    bar.grid(row=2, column=0, sticky="ew", pady=3)
    ttk.Label(root, textvariable=counters_var).grid(row=3, column=0, sticky="w")
    ttk.Label(root, textvariable=detail_var, wraplength=610).grid(row=4, column=0, sticky="w", pady=(4, 2))
    ttk.Label(root, textvariable=elapsed_var).grid(row=5, column=0, sticky="w")
    ttk.Label(root, textvariable=output_var, wraplength=610).grid(row=6, column=0, sticky="w", pady=(8, 0))
    state = {"closed": False, "worker_done": False}

    def close_window():
        state["closed"] = True
        root.withdraw()
        if state["worker_done"]:
            root.quit()

    root.protocol("WM_DELETE_WINDOW", close_window)

    def poll():
        snapshot = progress.snapshot()
        done, total = snapshot["done"], snapshot["total"]
        stage_var.set(snapshot["stage"])
        detail_var.set(snapshot["detail"])
        counters_var.set(f"{done:,} / {total:,}")
        bar["value"] = (100 * done / total) if total else 0
        rate = snapshot["ratePerSecond"]
        elapsed_var.set(
            f"Elapsed {snapshot['elapsedSeconds']:.0f}s · rate {rate:.2f} units/s · ETA unknown"
        )
        if not worker.is_alive():
            state["worker_done"] = True
            if not state["closed"]:
                title.configure(text="Asset restoration finished")
                root.update_idletasks()
            else:
                root.quit()
            return
        root.after(450, poll)

    root.after(200, poll)
    start_worker()
    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the default progress window; the durable progress sidecar remains enabled.",
    )
    parser.add_argument("--plan-only", action="store_true", help="Print audited counts and exit without writing files.")
    args = parser.parse_args()
    if args.plan_only:
        print(json.dumps({"root": str(ROOT), "expectedWorkUnits": _expected_total(), "battleUrls": len(BATTLE_SOURCES), "bodyFiles": len(BODY_FILES)}, indent=2))
        return 0

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    stage = COORDINATION / f"asset-restore-staging-{run_id}"
    _within_root(stage)
    progress = Progress(stage)
    outcome = {"success": False, "stage": stage, "error": None}

    def work():
        try:
            outcome["success"], outcome["stage"] = run_restore(progress)
        except Exception as exc:
            outcome["error"] = str(exc)
            progress.error(str(exc))
            progress.finish(False)

    worker = threading.Thread(target=work, name="asset-restore-worker", daemon=False)
    if args.headless:
        worker.start()
        worker.join()
    else:
        # Create the monitor before starting the worker so progress is visible from
        # the first real source/output step. Closing this window hides it; the worker
        # continues until completion and writes its durable report.
        try:
            _show_window(progress, worker, worker.start)
        except Exception as exc:
            progress.error(f"Progress window startup failed: {exc}")
            progress.finish(False)
            print(f"Could not start the progress window: {exc}. Use --headless to opt out.", file=sys.stderr)
            return 2
        if worker.is_alive():
            worker.join()
    if outcome["error"]:
        print(f"Asset restoration failed: {outcome['error']}", file=sys.stderr)
    print(f"Staging and report: {outcome['stage']}")
    print(f"Progress sidecar: {PROGRESS_PATH}")
    return 0 if outcome["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

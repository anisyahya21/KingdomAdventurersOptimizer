"""Separate planner-heavy desktop profile; canonical app/source remains unchanged."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path
import sys
import secrets
import threading
from http.server import ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[2] / 'coordination/native-preparation/shared'
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))
from optimizer_storage import configure_optimizer_storage
STORAGE = configure_optimizer_storage(HERE.parents[2])
sys.path.insert(0, str(HERE))
import strategy_optimizer_desktop as desktop
import strategy_optimizer_limits as limits
import strategy_parallel_proposals as proposals
CanonicalBridge = desktop.Bridge

PROFILE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KingdomAdventurersPlannerOptimizer'
SETTINGS = PROFILE / 'settings.json'
MAX_WORKERS = 48
DEFAULT_WORKERS = 48
DEFAULT_SHARE = 80
RESERVE_BYTES = 6 * 1024 ** 3
# Existing live battle children measured 104–141 MiB RSS on 2026-10-04;
# 256 MiB admission allows startup/working-set growth. 768 MiB is their hard
# per-process failure ceiling, not an observed reservation. Planner admission
# retains the canonical 256 MiB estimate (live planner RSS was 207–212 MiB).
BATTLE_ADMISSION_BYTES = 256 * 1024 ** 2


def allocation(total, share):
    total, share = int(total), int(share)
    if not 1 <= total <= MAX_WORKERS or not 10 <= share <= 90:
        raise ValueError('Choose 1–48 total workers and 10–90% planning workers.')
    planners = max(0, min(total - 1, (total * share + 50) // 100))
    return planners, total - planners


def resource_check(total, share):
    planners, battles = allocation(total, share)
    free = proposals._free_bytes()
    estimate = planners * proposals.PER_WORKER_BYTES + battles * BATTLE_ADMISSION_BYTES
    if free is None:
        raise ValueError('Available RAM could not be measured; worker start is blocked.')
    if free < RESERVE_BYTES + estimate:
        raise ValueError(f'Insufficient RAM for {total} workers: {free / 1024**3:.1f} GiB free; '
                         f'{(RESERVE_BYTES + estimate) / 1024**3:.1f} GiB required including a 6 GiB reserve. '
                         'Lower the total worker count.')
    return dict(freeGiB=round(free / 1024**3, 2), estimatedWorkersGiB=round(estimate / 1024**3, 2))


class PlannerOptimizer(desktop.Optimizer):
    planning_share = DEFAULT_SHARE

    def _planner_worker_count(self, total=None):
        return allocation(total if total is not None else getattr(self, '_workers', DEFAULT_WORKERS),
                          self.planning_share)[0]


class PlannerBridge(desktop.Bridge):
    def __init__(self, library, remember=False):
        self.selected_workers = DEFAULT_WORKERS
        self.selected_share = DEFAULT_SHARE
        try:
            saved = json.loads(SETTINGS.read_text(encoding='utf-8'))
            allocation(saved.get('workers', DEFAULT_WORKERS), saved.get('planningShare', DEFAULT_SHARE))
            self.selected_workers = int(saved.get('workers', DEFAULT_WORKERS))
            self.selected_share = int(saved.get('planningShare', DEFAULT_SHARE))
        except (OSError, ValueError, TypeError):
            pass
        PlannerOptimizer.planning_share = self.selected_share
        if library is None:
            # No Store, lock, schema changes or fresh file exist until New/Open.
            self._library = PROFILE / 'strategies.sqlite'
            self._optimizer = None
            self._guard = None
            self._window = None
            self._operation = threading.Lock()
            self._start_requested = False
            self._status_path = '/__optimizer_status/' + secrets.token_urlsafe(32)
        else:
            super().__init__(library, remember=True)

    def _paused(self):
        if self._optimizer is not None:
            return super()._paused()

    def _save_settings(self):
        PROFILE.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(library=str(self._library), workers=self.selected_workers,
                                              planningShare=self.selected_share)), encoding='utf-8')
        os.replace(temporary, SETTINGS)

    def planner_controls(self):
        planners, battles = allocation(self.selected_workers, self.selected_share)
        status = self._optimizer.status() if self._optimizer is not None else dict(state='No library')
        return dict(ok=True, workers=self.selected_workers, planningShare=self.selected_share,
                    requestedPlanningWorkers=planners, requestedBattleWorkers=battles,
                    allocation=status.get('workerAllocation') or (status.get('scheduler') or {}).get('workerAllocation'),
                    state=status.get('state'), actualCpuPlanningShare=None)

    def configure_planner(self, workers, planning_share):
        try:
            if self._optimizer is None:
                raise ValueError('Create or open a library first.')
            workers, planning_share = int(workers), int(planning_share)
            allocation(workers, planning_share)
            status = self._optimizer.status()
            if planning_share != self.selected_share:
                if status.get('state') in ('Running', 'Saving', 'Opening library') or getattr(self._optimizer, '_campaign_started', False):
                    raise ValueError('Stop and let accepted work save before changing the planning ratio.')
                if not self._optimizer._campaign_resize_drain_status()['ready']:
                    raise ValueError('Accepted work is still draining. Wait before changing the planning ratio.')
            resource_check(workers, planning_share)
            old_workers, old_share = self.selected_workers, self.selected_share
            self.selected_workers, self.selected_share = workers, planning_share
            self._optimizer.planning_share = planning_share
            if status.get('state') == 'Running':
                response = self.command('community_start', dict(workers=workers,
                                        duty=getattr(self._optimizer, '_duty', 1.0)))
                if not response.get('ok'):
                    self.selected_workers, self.selected_share = old_workers, old_share
                    self._optimizer.planning_share = old_share
                    return response
            self._save_settings()
            return self.planner_controls()
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def command(self, action, value=None):
        if self._optimizer is None:
            return dict(ok=False, error='Create or open a library first.')
        if action in ('start', 'community_start', 'encounter_activate'):
            try:
                resource_check(self.selected_workers, self.selected_share)
            except ValueError as exc:
                return dict(ok=False, error=str(exc))
            value = dict(value or {}, workers=self.selected_workers)
        return super().command(action, value)

    def _switch_library(self, path):
        if self._optimizer is None:
            # Canonical New/Open hold the existing operation lock. Keep that
            # same lock through first initialization so their finally releases it.
            window, operation = self._window, self._operation
            workers, share = self.selected_workers, self.selected_share
            try:
                CanonicalBridge.__init__(self, path, remember=True)
            finally:
                self._window, self._operation = window, operation
                self.selected_workers, self.selected_share = workers, share
            self._optimizer.planning_share = share
            self._optimizer.prepare_status_transport()
            self._save_settings()
            return dict(ok=True, library=str(path))
        result = super()._switch_library(path)
        self._optimizer.planning_share = self.selected_share
        return result


CONTROLS_JS = r"""
(() => {
 if(document.getElementById('planner-version-controls')) return;
 const box=document.createElement('div');box.id='planner-version-controls';
 box.style.cssText='position:sticky;top:0;z-index:9999;background:#152031;color:#e4edff;padding:12px 22px;border-bottom:2px solid #9673ec;font:14px system-ui';
 box.innerHTML='<strong>Planner Optimiser · separate version</strong> &nbsp; <label>Total workers <input id="planner-total" type="number" min="1" max="48" value="48" style="width:64px;color:black"></label> &nbsp; <label>Planning workers <input id="planner-share" type="range" min="10" max="90" value="80"></label> <span id="planner-counts"></span> &nbsp; <button id="planner-apply" style="background:#7655c8;padding:5px 12px;border-radius:5px">Apply</button><div id="planner-message" style="margin-top:6px"></div><small>Counts allocate worker slots. Actual CPU share is not measured or controlled in this version. The coordinator remains serial. Stop to change the planning ratio; live total changes save accepted work before resizing.</small>';
 document.body.prepend(box);
 const total=box.querySelector('#planner-total'),share=box.querySelector('#planner-share'),counts=box.querySelector('#planner-counts'),msg=box.querySelector('#planner-message');
 function showCounts(){let t=Number(total.value),s=Number(share.value);let p=Math.max(0,Math.min(t-1,Math.floor((t*s+50)/100)));counts.textContent=`${s}% requested · ${p} planning / ${t-p} execution`;}
 total.oninput=share.oninput=showCounts;
 async function refresh(){try{const r=await window.pywebview.api.planner_controls();total.value=r.workers;share.value=r.planningShare;showCounts();const a=r.allocation;msg.textContent=a?`Effective: ${a.effectivePlannerWorkers} planning / ${a.effectiveBattleWorkers} execution · pools ${a.configuredPlanningPoolWorkers}/${a.configuredBattlePoolWorkers} · resize ${a.resizeState}`:'Ready; no search automatically started.';}catch(e){msg.textContent=String(e);}}
 box.querySelector('#planner-apply').onclick=async()=>{msg.textContent='Applying…';const r=await window.pywebview.api.configure_planner(Number(total.value),Number(share.value));if(!r.ok){msg.textContent=r.error;return;}await refresh();};
 function hideOld(){document.querySelectorAll('[data-optimizer-workers]').forEach(e=>{e.style.display='none';});}
 hideOld();new MutationObserver(hideOld).observe(document.body,{childList:true,subtree:true});
 refresh();setInterval(async()=>{try{const r=await window.pywebview.api.planner_controls(),a=r.allocation;if(a)msg.textContent=`Effective: ${a.effectivePlannerWorkers} planning / ${a.effectiveBattleWorkers} execution · pools ${a.configuredPlanningPoolWorkers}/${a.configuredBattlePoolWorkers} · resize ${a.resizeState}`;}catch(e){}},5000);
})();
"""

LANDING_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Planner Optimiser</title>
<style>body{margin:0;background:#10151f;color:#e4edff;font:16px system-ui}main{max-width:650px;margin:12vh auto;padding:35px;background:#192232;border-radius:14px}button{font:inherit;padding:12px 22px;margin:12px 12px 12px 0;background:#7655c8;color:white;border:0;border-radius:7px;cursor:pointer}small{color:#afbdd1}#message{min-height:30px;color:#ebc583}</style></head>
<body><main><h1>Planner Optimiser</h1><p>Separate version · 48 total worker slots · 38 planning / 10 execution by default.</p>
<p>Create a new library or open an existing library to begin.</p>
<button id="planner-new-library" onclick="choose('new_library')">New library</button>
<button id="planner-open-library" onclick="choose('open_library')">Open library</button>
<div id="message"></div><small>No library is opened automatically. Runs start when you press Run. Actual CPU share is not measured or controlled.</small>
<script>async function choose(method){const message=document.getElementById('message');message.textContent='';try{const result=await window.pywebview.api[method]();if(!result.ok){message.textContent=result.error;return;}if(result.library)window.location.href=window.__optimizerDesktopUrl;}catch(error){message.textContent=String(error);}}</script></main></body></html>"""


def install_profile():
    # All overrides are confined to this separately launched process.
    limits.worker_ceiling = lambda cpus=None: MAX_WORKERS
    limits.THROUGHPUT_KNEE_WORKERS = DEFAULT_WORKERS
    desktop.strategy_optimizer.worker_ceiling = limits.worker_ceiling
    proposals.MAX_PLANNING_WORKERS = MAX_WORKERS
    original_get_pool = proposals.get_planning_pool
    def get_pool(*args, **kwargs):
        kwargs['hard_max_workers'] = MAX_WORKERS
        return original_get_pool(*args, **kwargs)
    proposals.get_planning_pool = get_pool
    desktop.Optimizer = PlannerOptimizer
    desktop.Bridge = PlannerBridge
    desktop.SETTINGS = SETTINGS


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, help='Existing, unlocked library to open. Never starts runs automatically.')
    parser.add_argument('--check', action='store_true', help='Print profile/allocation information without opening a library.')
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(dict(total=48, planning=38, execution=10, actualCpuShare=None)))
        return
    install_profile()
    import webview
    if not (desktop.APP / 'desktop-dist/desktop.html').is_file():
        raise RuntimeError('The existing desktop UI build is missing.')
    bridge = PlannerBridge(None)
    server = ThreadingHTTPServer(('127.0.0.1', 0), desktop.asset_handler(bridge=bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    desktop_url = f'http://127.0.0.1:{server.server_port}/desktop.html'
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    window = webview.create_window('Kingdom Adventurers · Planner Optimiser (48-worker profile)',
        html=LANDING_HTML, js_api=bridge, width=1320, height=900, min_size=(980, 700))
    bridge._window = window
    closing, allow_destroy = threading.Event(), threading.Event()

    def on_loaded():
        if bridge._optimizer is None:
            window.evaluate_js('window.__optimizerDesktopUrl=' + json.dumps(desktop_url))
            if args.library is not None:
                if not args.library.is_file():
                    window.evaluate_js("document.getElementById('message').textContent='Choose an existing library.'")
                    return
                try:
                    bridge._switch_library(args.library)
                    window.load_url(desktop_url)
                except Exception as exc:
                    window.evaluate_js("document.getElementById('message').textContent=" + json.dumps(str(exc)))
        else:
            window.evaluate_js(CONTROLS_JS)

    def on_closing():
        if allow_destroy.is_set() or bridge._optimizer is None:
            return True
        if bridge._optimizer.thread.is_alive() or bridge._operation.locked():
            if not closing.is_set():
                closing.set()
                window.set_title('Saving simulations — Planner Optimiser')
                bridge.close()
                def finish():
                    bridge._optimizer.thread.join()
                    with bridge._operation:
                        allow_destroy.set()
                    window.destroy()
                threading.Thread(target=finish, daemon=True).start()
            return False
        return True

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    try:
        webview.start(gui='edgechromium', private_mode=True)
    finally:
        if bridge._optimizer is not None and bridge._optimizer.thread.is_alive():
            bridge.close()
            bridge._optimizer.thread.join()
        if bridge._guard is not None:
            bridge._guard.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as exc:
        PROFILE.mkdir(parents=True, exist_ok=True)
        import traceback
        (PROFILE / 'last-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f'{exc}\n\nDetails: {PROFILE / "last-error.log"}', 'Planner Optimiser', 0x10)

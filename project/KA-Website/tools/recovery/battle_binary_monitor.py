"""Local progress window; closing it does not stop the child benchmark."""
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'studies/battle_binary_corpus'; OUT.mkdir(parents=True,exist_ok=True)
log=(OUT/'run.log').open('a',encoding='utf-8')
child=subprocess.Popen([sys.executable,str(ROOT/'benchmark_battle_binary.py'),*sys.argv[1:]],
    stdout=log,stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
root=tk.Tk(); root.title('Rich battle binary corpus test'); root.geometry('700x340')
label=tk.StringVar(value='Starting corpus test; progress and ETA unknown')
tk.Label(root,textvariable=label,justify='left',wraplength=660).pack(padx=18,pady=20)
tk.Label(root,text='Closing this window leaves the test running. Durable status and logs are saved.',wraplength=660).pack()
def refresh():
    try:
        s=json.loads((OUT/'status.json').read_text()); total=s.get('total'); done=s.get('completed',0)
        percent=f'{100*done/total:.1f}%' if total else 'unknown'
        rate=f"{s['rate']:.0f}/s" if s.get('rate') else 'unknown'
        eta=f"{s['eta']:.1f}s (provisional)" if s.get('eta') is not None else 'unknown'
        label.set(f"{s['stage']}\n\nStage progress: {done:,} / {total or 'unknown'} ({percent})\nElapsed: {s['elapsed']:.1f}s\nRate: {rate}; stage ETA: {eta}\nWarnings/errors: {s.get('error') or 'none'}\n\nOutput: {OUT}")
    except (OSError,ValueError): pass
    if child.poll() is None: root.after(1000,refresh)
    else: log.close()
refresh(); root.mainloop()

import json,subprocess,sys,tkinter as tk,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'studies/battle_binary_corpus/complete-file-audit'; OUT.mkdir(parents=True,exist_ok=True)
log=(OUT/'run.log').open('a',encoding='utf-8')
command=([sys.executable,str(ROOT/'verify_battle_binary_complete_file_packets.py')] if '--decode-only' in sys.argv else [sys.executable,str(ROOT/'measure_battle_binary_complete_files.py'),'--output-dir',str(OUT),'--workers','4'])
child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
root=tk.Tk();root.title('Battle codec complete file audit');root.geometry('740x370'); label=tk.StringVar(value='Starting complete corpus file audit; totals and ETA unknown')
tk.Label(root,textvariable=label,justify='left',wraplength=700).pack(padx=18,pady=18)
tk.Label(root,text='Closing this window leaves the job running. Durable status and diagnostics are saved.').pack()
start=time.monotonic()
def refresh():
    try:
        s=json.loads((OUT/'status.json').read_text()); done=s.get('completed',s.get('done',0));total=s.get('total');rate=s.get('rate');eta=s.get('eta')
        percent=f'{100*done/total:.1f}%' if total else 'unknown'
        label.set(f"{s.get('stage','Working')}\nStage: {done:,} / {total or 'unknown'} ({percent})\nElapsed: {time.monotonic()-start:.1f}s\nRate: {rate or 'unknown'}/s; ETA: {str(round(eta,1))+'s (provisional)' if eta is not None else 'unknown'}\nWarnings/errors: {s.get('error') or 'none'}\nOutput: {OUT}")
    except (OSError,ValueError):pass
    if child.poll() is None:root.after(800,refresh)
    else:
        log.close();label.set(label.get()+f'\nFinal process outcome: exit {child.returncode}; see report.json / run.log.')
refresh();root.mainloop()

"""Portable desktop entry: new isolated library, never the original workspace."""
import sys
import multiprocessing
from pathlib import Path
def main():
 root=Path(__file__).resolve().parent
 sys.path.insert(0,str(root/'project/KA-Website/tools/recovery'))
 import strategy_language_desktop
 engine=sys.argv[1] if len(sys.argv)>1 else 'original'
 library=root/'runtime/libraries'/f'{engine}.sqlite'
 library.parent.mkdir(parents=True,exist_ok=True)
 sys.argv=[__file__,'--engine',engine,'--library',str(library)]
 strategy_language_desktop.main()

if __name__ == '__main__':
 multiprocessing.freeze_support()
 main()

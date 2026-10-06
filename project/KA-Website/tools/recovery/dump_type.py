"""Print the frozen dump.cs block(s) for a managed type, by exact name.

    .venv\\Scripts\\python.exe <this> Entity TreasureComponent

Matches declaration lines of the form
    public class Entity : Object // TypeDefIndex: 123
and prints from that line to the closing brace at column 0, together with the
preceding `// Namespace:` line. This keeps type lookups scoped to the frozen
`dump/dump.cs` instead of a repo-wide search (the ~20 MB file makes those time
out).

Read-only against the frozen dump; prints to stdout.
"""
import re
import sys
from pathlib import Path


def find_dump():
    for parent in Path(__file__).resolve().parents:
        cand = parent / 'RE-evidence/G2.1/39257e72291d/dump/dump.cs'
        if cand.is_file():
            return cand
    raise SystemExit('could not locate dump/dump.cs')


DUMP = find_dump()
MODIFIERS = ('public', 'internal', 'private', 'protected', 'sealed', 'abstract',
             'static', 'unsafe', 'partial', 'readonly', 'ref')


def decl_pattern(name):
    mods = r'(?:' + '|'.join(MODIFIERS) + r')\s+'
    return re.compile(rf'^(?:{mods})*(?:class|struct|enum|interface)\s+'
                      rf'{re.escape(name)}\b')


def blocks(lines, name):
    pat = decl_pattern(name)
    out = []
    for idx, line in enumerate(lines):
        if not pat.match(line):
            continue
        end = next((n for n in range(idx + 1, len(lines)) if lines[n] == '}'), None)
        if end is None:
            continue
        start = idx - 1 if idx and lines[idx - 1].startswith('// Namespace:') else idx
        out.append((start, end, idx))
    return out


def main():
    args = sys.argv[1:]
    out_path = None
    if '--out' in args:
        i = args.index('--out')
        out_path = args[i + 1]
        del args[i:i + 2]
    if not args:
        raise SystemExit(__doc__)
    lines = DUMP.read_text(encoding='utf-8', errors='replace').splitlines()
    chunks = []
    for name in args:
        hits = blocks(lines, name)
        chunks.append(f'==== {name}: {len(hits)} declaration(s) ====')
        for start, end, decl in hits:
            chunks.append(f'---- dump.cs lines {start + 1}-{end + 1} '
                          f'(decl at {decl + 1}) ----')
            chunks.append('\n'.join(lines[start:end + 1]))
        chunks.append('')
    text = '\n'.join(chunks)
    if out_path:
        Path(out_path).write_text(text, encoding='utf-8')
        print(f'wrote {out_path} ({len(text.splitlines())} lines)')
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        print(text)


if __name__ == '__main__':
    main()

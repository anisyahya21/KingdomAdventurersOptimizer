"""Deterministically project frozen combined schemas onto existing separate storage cells.

No training or source database access. Run with --apply to append an immutable version;
default checks that the deployed current version is exactly reproducible.
"""
import copy
import hashlib
import json
import sys
import zlib
from pathlib import Path
import strategy_battle_binary as binary


def schematic(schema):
    kind = schema['type']
    if kind == 'reference':
        return schematic(schema['fallback'])
    if kind == 'object':
        return {k: schematic(v) for k, v in sorted(schema['fields'])}
    if kind == 'array':
        return [schematic(v) for v in schema['items']]
    if kind == 'integer-array-delta':
        return ['scalar'] * schema['length']
    return 'scalar'


def main():
    base = json.loads(binary.DEFAULT_DEFINITIONS.read_text(encoding='utf-8'))
    doc = dict(registryVersion=binary.REGISTRY_VERSION, codecVersion=binary.VERSION,
               shapes=[], schemas=[], dictionary=base['dictionary'])
    for schema in base['schemas']:
        if schema['type'] != 'object':
            continue
        for kind, child in schema['fields']:
            if kind not in ('result', 'outcome'):
                continue
            cell = dict(type='object', fields=[[kind, copy.deepcopy(child)]])
            shape = hashlib.sha256(binary.dumps(schematic(cell)).encode()).hexdigest()
            if shape not in doc['shapes']:
                doc['shapes'].append(shape); doc['schemas'].append(cell)
    raw = binary.dumps(doc).encode()
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(__file__).with_name('battle_binary_registry')
    manifest = json.loads((root/'manifest.json').read_text())
    if '--apply' in sys.argv:
        path = root/(digest+'.json.zlib')
        if path.exists():
            assert zlib.decompress(path.read_bytes()) == raw
        else:
            path.write_bytes(zlib.compress(raw, 9))
        manifest['current'] = digest
        manifest['definitions'][digest[:16]] = digest
        (root/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    assert manifest['current'] == digest
    assert zlib.decompress((root/(digest+'.json.zlib')).read_bytes()) == raw
    print(json.dumps(dict(current=digest, schemas=len(doc['schemas']), reproducible=True)))


if __name__ == '__main__':
    main()

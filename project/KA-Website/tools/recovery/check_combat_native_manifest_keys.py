"""Regression for audit finding 5: same-basename manifests must not collide in the harness.

`combat_native_composition.load` used to key loaded manifests by `path.name`, so the four
`slices.json` sets the receipt fixtures load overwrote one another in `slice_manifest.manifests`.
This check loads two real same-basename manifests and asserts both survive under distinct,
stable, evidence-relative keys with their own digests and slice counts. The manifests and the
frozen binary are evidence input; nothing here rewrites a slice or a frozen artifact.
"""
import hashlib
import json

from combat_native_composition import Composition, RECEIPT_SLICES, manifest_key

DISPATCH = RECEIPT_SLICES / 'dispatch/slices.json'
ARM = RECEIPT_SLICES / 'box-result-arms/slices.json'


def main():
    assert DISPATCH.name == ARM.name == 'slices.json', (DISPATCH.name, ARM.name)
    keys = [manifest_key(DISPATCH), manifest_key(ARM)]
    assert keys[0] != keys[1], keys
    assert all(key.endswith('slices.json') for key in keys), keys
    assert not any(key.startswith('/') or ':'.join(key.split(':')[:1]) == '' for key in keys), keys
    machine = Composition([DISPATCH, ARM], name='manifest-key-regression')
    record = machine.slice_manifest()
    assert record['manifestsLoaded'] == 2, record
    assert sorted(record['manifests']) == sorted(keys), record['manifests']
    for path, key in zip((DISPATCH, ARM), keys):
        entry = record['manifests'][key]
        assert entry['path'] == key, entry
        assert entry['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest(), entry
        assert entry['slices'] == len(json.loads(path.read_text(encoding='utf-8'))['slices']), entry
    # Re-loading an already loaded manifest is idempotent: same key, still two manifests.
    machine.load(ARM)
    assert machine.slice_manifest()['manifestsLoaded'] == 2, machine.slice_manifest()
    print(json.dumps(dict(keys=keys, manifestsLoaded=record['manifestsLoaded'],
                          slices=record['slices'], digest=record['digest'])))


if __name__ == '__main__':
    main()

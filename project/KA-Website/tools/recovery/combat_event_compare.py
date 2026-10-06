"""Ordered event and state comparison with first-divergence reporting (B4).

A fixture gives two traces of the same composition: one recorded while the original slices ran
under Unicorn (combat_native_composition), one recorded by the offline model. Both traces are
ordered event dictionaries; this module projects them onto the fields that are actually claimed,
finds the first index where they disagree, and returns that divergence as data instead of reducing
the run to a final total. A matching final total can hide compensating errors, so the comparison
always runs over the ordered sequence and never only over the end state.
"""
import json

from combat_run_manifest import canonical_hash


def jsonable(value):
    """Tuples and byte strings become JSON lists so both tracers serialize identically."""
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return list(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    return value


def normalize(event, fields=None, ignore=('index', 'address')):
    if fields is None:
        return {key: jsonable(value) for key, value in event.items() if key not in ignore}
    return {key: jsonable(event[key]) for key in fields if key in event}


def project(trace, fields=None, ignore=('index', 'address'), kinds=None, names=None):
    """Keep the events a fixture claims equivalent, in order, normalized for comparison."""
    rows = []
    for event in trace:
        if kinds is not None and event.get('kind') not in kinds:
            continue
        key = event.get('name') or event.get('event')
        if names is not None and key not in names:
            continue
        rows.append(normalize(event, fields, ignore))
    return rows


def first_divergence(native, model, fields=None, ignore=('index', 'address'), kinds=None,
                     names=None, context=3):
    """Return None when the projected sequences agree, else the first disagreement as data.

    `reason` is 'event' for a differing event and 'length' when one sequence is a strict prefix of
    the other; `context` carries the events that led up to the divergence on both sides.
    """
    left = project(native, fields, ignore, kinds, names)
    right = project(model, fields, ignore, kinds, names)
    for index, (one, other) in enumerate(zip(left, right)):
        if one != other:
            return dict(index=index, reason='event', native=one, model=other,
                        context=dict(native=left[max(0, index - context):index],
                                     model=right[max(0, index - context):index]),
                        compared=dict(native=len(left), model=len(right)),
                        projectedDigest=canonical_hash(dict(native=left, model=right)))
    if len(left) != len(right):
        index = min(len(left), len(right))
        return dict(index=index, reason='length', native=left[index:index + 1] or None,
                    model=right[index:index + 1] or None,
                    context=dict(native=left[max(0, index - context):index],
                                 model=right[max(0, index - context):index]),
                    compared=dict(native=len(left), model=len(right)),
                    projectedDigest=canonical_hash(dict(native=left, model=right)))
    return None


def state_divergences(native_state, model_state):
    """Per-key comparison of state observed at the same boundary."""
    divergences = []
    for key in sorted(set(native_state) | set(model_state)):
        one, other = native_state.get(key), model_state.get(key)
        if jsonable(one) != jsonable(other):
            divergences.append(dict(key=key, native=jsonable(one), model=jsonable(other)))
    return divergences


def describe(divergence, label='composition'):
    """One readable line plus the compared lengths, for an exception or a log entry."""
    if divergence is None:
        return f'{label}: no divergence'
    where = divergence['index']
    if divergence['reason'] == 'length':
        return (f'{label}: sequences agree for {where} events then diverge in length '
                f'(native {divergence["compared"]["native"]}, '
                f'model {divergence["compared"]["model"]})')
    return (f'{label}: first divergence at event {where}: native against model differ '
            f'({json.dumps(divergence["native"], sort_keys=True)} vs '
            f'{json.dumps(divergence["model"], sort_keys=True)})')


def channels(native, model, names, fields=('args',), kinds=('call',)):
    """Extract one named boundary channel from both traces, in order, for direct comparison."""
    def channel(trace):
        rows = []
        for event in trace:
            if event.get('kind') in kinds and event.get('name') in names:
                rows.append([jsonable(event.get(field)) for field in fields])
        return rows
    return channel(native), channel(model)


def assert_no_divergence(native, model, label='composition', **kwargs):
    divergence = first_divergence(native, model, **kwargs)
    if divergence is not None:
        raise AssertionError(describe(divergence, label))
    return divergence

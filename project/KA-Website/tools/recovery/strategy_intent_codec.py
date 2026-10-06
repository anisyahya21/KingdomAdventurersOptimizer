"""Lossless full-intent storage with a small, duplicate-aware JSON metadata projection.

Schema 5 stores the six fields used by indexed selectors in ``intent_json`` and keeps the exact
original intent bytes in a framed compressed BLOB when compression is worthwhile. The projection is
assembled from lexical JSON slices: duplicate occurrences, key spelling, nested JSON, and numeric
spelling stay intact so SQLite continues to select the first duplicate while Python ``json.loads``
continues to expose the last one.
"""
from __future__ import annotations

import json

import strategy_payload_codec as payload_codec

PROJECTION_FIELDS = (
    'compatibility',
    'engineRevision',
    'mechanicsRevision',
    'policy',
    'measurementWindow',
    'changedFields',
)

_DECODER = json.JSONDecoder(parse_constant=lambda token: (_ for _ in ()).throw(
    ValueError('non-standard JSON number: ' + token)))


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position] in ' \t\r\n':
        position += 1
    return position


def _top_level_pairs(raw: str):
    """Yield ``(decoded_key, exact_key, exact_value)`` pairs from a bounded JSON object."""
    if not isinstance(raw, str):
        raise ValueError('intent must be JSON text')
    try:
        if len(raw.encode('utf-8')) > payload_codec.MAX_RAW_BYTES:
            raise ValueError('intent exceeds the codec raw-size bound')
    except UnicodeEncodeError as exc:
        raise ValueError('intent is not valid UTF-8 text') from exc

    start = _skip_whitespace(raw, 0)
    if start >= len(raw) or raw[start] != '{':
        raise ValueError('intent root is not an object')
    position = _skip_whitespace(raw, start + 1)
    if position < len(raw) and raw[position] == '}':
        if _skip_whitespace(raw, position + 1) != len(raw):
            raise ValueError('trailing data')
        return

    while True:
        key_start = position
        key, key_end = _DECODER.raw_decode(raw, position)
        if not isinstance(key, str):
            raise ValueError('top-level object key is not a string')
        position = _skip_whitespace(raw, key_end)
        if position >= len(raw) or raw[position] != ':':
            raise ValueError('missing colon')
        value_start = _skip_whitespace(raw, position + 1)
        _value, value_end = _DECODER.raw_decode(raw, value_start)
        yield key, raw[key_start:key_end], raw[value_start:value_end]
        position = _skip_whitespace(raw, value_end)
        if position < len(raw) and raw[position] == ',':
            position = _skip_whitespace(raw, position + 1)
            continue
        if position < len(raw) and raw[position] == '}':
            if _skip_whitespace(raw, position + 1) != len(raw):
                raise ValueError('trailing data')
            return
        raise ValueError('missing comma or closing brace')


def metadata_projection(raw: str) -> str:
    """Return the exact lexical projection used by v5 SQL readers."""
    selected = [(key_raw, value_raw) for key, key_raw, value_raw in _top_level_pairs(raw)
                if key in PROJECTION_FIELDS]
    return '{' + ','.join(key + ':' + value for key, value in selected) + '}'


def encode_intent(raw: str) -> tuple[str, bytes | None]:
    """Return thin SQL metadata and an exact compressed full-intent BLOB, when worthwhile.

    Invalid, over-limit, or insufficiently compressible input stays in the TEXT cell unchanged and
    returns ``None`` for the BLOB. The caller can therefore preserve every old/fallback reader.
    """
    if not isinstance(raw, str):
        raise TypeError('intent codec input must be JSON text')
    try:
        metadata = metadata_projection(raw)
    except (ValueError, TypeError, RecursionError):
        return raw, None
    encoded = payload_codec.encode_text(raw)
    if not isinstance(encoded, bytes):
        return raw, None
    return metadata, encoded


def decode_intent(metadata: str | None, full_blob: bytes | None) -> str | None:
    """Return the exact full intent, requiring a valid framed codec BLOB when present."""
    if full_blob is None:
        return metadata
    if not isinstance(full_blob, bytes) or not payload_codec.has_codec_magic(full_blob):
        raise payload_codec.PayloadCodecError('full intent BLOB has no recognized codec frame')
    return payload_codec.decode_text(full_blob)

"""Lossless, versioned storage codec for the encounter ledger's result/outcome JSON cells.

SQLite returns legacy JSON TEXT as ``str`` and framed payloads as ``bytes``. ``encode_text``
retains the exact UTF-8 KAP contract used by intents and historical migrations. Result/outcome
writes use frozen KAC/KAB definitions; their readers reconstruct the ledger's canonical JSON.
Existing TEXT/KAP records remain readable. Definitions ship beside the code, never in new tables.
"""
from __future__ import annotations

import codecs
import hashlib
import hmac
import json
import struct
import zlib
from functools import lru_cache
from pathlib import Path
import threading

import strategy_battle_binary as battle_binary

PACKED_MAGIC = b'KAC\x01'
_REGISTRY = Path(__file__).with_name('battle_binary_registry')
_packed_lock = threading.RLock()


@lru_cache(maxsize=1)
def _registry_manifest():
    manifest = json.loads((_REGISTRY / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['formatVersion'] != 1:
        raise PayloadCodecError('unsupported packed registry manifest')
    return manifest


@lru_cache(maxsize=16)
def _packed_codec(identity):
    digest = _registry_manifest()['definitions'].get(identity)
    if digest is None:
        raise PayloadCodecError('packed definitions are not deployed: %s' % identity)
    raw = zlib.decompress((_REGISTRY / (digest + '.json.zlib')).read_bytes())
    if hashlib.sha256(raw).hexdigest() != digest:
        raise PayloadCodecError('packed definitions checksum mismatch')
    return battle_binary.RecordDecoder(json.loads(raw))


def encode_packed_text(text, kind='result'):
    """Pack a canonical result/outcome cell; retain exact-text fallback for other JSON."""
    if not isinstance(text, str):
        raise TypeError('payload codec input must be JSON text')
    if kind not in ('result', 'outcome'):
        raise ValueError('packed cell kind must be result or outcome')
    raw = text.encode('utf-8')
    if len(raw) > MAX_RAW_BYTES:
        return text
    value = json.loads(text)
    # The ledger supplies this canonical spelling. Other callers keep exact UTF-8,
    # including duplicate keys, nonfinite floats, and unusual number spellings.
    if json.dumps(value, sort_keys=True, separators=(',', ':'), default=str) != text:
        return encode_text(text)
    # The packet reader bounds varints. Legacy JSON accepts larger integers;
    # retain exact text rather than emitting a packet the reader cannot read.
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is int and item.bit_length() > 1020:
            return encode_text(text)
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    try:
        battle_binary.dumps(value).encode('utf-8')
        with _packed_lock:
            encoder = _packed_codec(_registry_manifest()['current'][:16])
            packet = PACKED_MAGIC + encoder.encode({kind: value})
        if len(packet) > MAX_ENCODED_BYTES:
            return encode_text(text)
        return packet
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, PayloadCodecError):
            raise
        # Permanent exact-text fallback preserves records outside the packed domain.
        return encode_text(text)


def _decode_packed(value):
    decoded = _decode_packed_value(value)
    text = json.dumps(decoded, sort_keys=True, separators=(',', ':'), default=str)
    if len(text.encode('utf-8')) > MAX_RAW_BYTES:
        raise PayloadCodecError('packed payload exceeds the decoded-size bound')
    return text


def _decode_packed_value(value):
    """Decode a packed frame to its JSON value without serializing it back to text.

    Most callers need parsed fields, not the canonical JSON string. Keep the public text decoder
    above unchanged; this private primitive is for read models that immediately call ``json.loads``.
    """
    if len(value) > MAX_ENCODED_BYTES:
        raise PayloadCodecError('packed payload exceeds the encoded-size bound')
    cell = value.startswith(b'KAC')
    if cell:
        if value[:4] != PACKED_MAGIC:
            raise PayloadCodecError('unsupported packed cell version')
        value = value[4:]
    core = value
    if value.startswith(b'KAT'):
        if value[:4] != b'KAT\x01' or len(value) < 22:
            raise PayloadCodecError('unsupported or truncated timing envelope')
        core = value[8:]
    if len(core) < 14 or core[:4] != battle_binary.MAGIC:
        raise PayloadCodecError('unsupported or truncated packed payload')
    try:
        with _packed_lock:
            decoded = _packed_codec(core[4:12].hex()).decode(value)
        if cell:
            if not isinstance(decoded, dict) or len(decoded) != 1 or not set(decoded) <= {'result', 'outcome'}:
                raise PayloadCodecError('invalid packed cell fields')
            decoded = next(iter(decoded.values()))
        # Preserve the existing decoded-size guard without allocating a JSON string on the hot
        # path. A conservative upper bound may send an unusual but valid value through the legacy
        # exact-size check below; it never accepts a value that could exceed MAX_RAW_BYTES.
        if _json_size_upper_bound(decoded) > MAX_RAW_BYTES:
            text = json.dumps(decoded, sort_keys=True, separators=(',', ':'), default=str)
            if len(text.encode('utf-8')) > MAX_RAW_BYTES:
                raise PayloadCodecError('packed payload exceeds the decoded-size bound')
            # Mirror the old text-decoder -> json.loads semantics on uncommon decoder values the
            # fast path does not know how to size. JSON normalization can stringify non-string keys
            # or `default=str` future objects, so returning `decoded` directly would change them.
            decoded = json.loads(text)
        return decoded
    except (ValueError, KeyError, IndexError, UnicodeError, struct.error, OSError, zlib.error) as exc:
        raise PayloadCodecError('invalid packed payload: %s' % exc) from exc


def _json_size_upper_bound(value):
    """Conservative JSON UTF-8 size bound for decoder output, stopping at the codec limit."""
    total = 0
    pending = [value]
    while pending:
        item = pending.pop()
        if item is None or isinstance(item, bool):
            total += 5
        elif isinstance(item, str):
            # ensure_ascii JSON escaping is at most 12 ASCII chars per Unicode code point.
            total += 2 + 12 * len(item)
        elif isinstance(item, int):
            total += max(1, (item.bit_length() * 30103) // 100000 + 2)
        elif isinstance(item, float):
            total += 32
        elif isinstance(item, list):
            total += 2 + max(0, len(item) - 1)
            pending.extend(item)
        elif isinstance(item, dict):
            total += 2 + max(0, len(item) - 1) + len(item)
            for key, child in item.items():
                if not isinstance(key, str):
                    return MAX_RAW_BYTES + 1
                pending.append(key)
                pending.append(child)
        else:
            # The binary decoder currently emits only JSON primitives and containers. Keep
            # unusual future types on the exact legacy validation path.
            return MAX_RAW_BYTES + 1
        if total > MAX_RAW_BYTES:
            return total
    return total


def decode_json_codec_if_magic(value):
    """Like the overview's permissive ``_json(decode_codec_if_magic(value))`` helper.

    Legacy TEXT/BLOB, compressed KAP frames, and malformed reserved frames retain the public
    decoder's validation and exception behavior. The optimization only avoids serializing an
    already parsed KAB/KAC/KAT value when the caller immediately parses it again.
    """
    if value is None:
        return None
    if isinstance(value, bytes) and value.startswith((b'KAC', b'KAB', b'KAT')):
        return _decode_packed_value(value)
    decoded = decode_codec_if_magic(value)
    try:
        return json.loads(decoded)
    except (TypeError, ValueError):
        return None


def _decode_framed(value):
    return _decode_compressed(value) if value.startswith(MAGIC_PREFIX) else _decode_packed(value)

MAGIC_PREFIX = b'KAP'
MAGIC = MAGIC_PREFIX + b'\x01'
_HEADER = struct.Struct('>4sI32s')
HEADER_BYTES = _HEADER.size

MAX_RAW_BYTES = 8 * 1024 * 1024
MIN_SAVING_BYTES = 128
MIN_SAVING_RATIO = 0.10
COMPRESSION_LEVEL = 1

# zlib's compressBound formula, plus the codec header. This also bounds the largest valid v4 BLOB.
_MAX_ZLIB_BYTES = (MAX_RAW_BYTES + (MAX_RAW_BYTES >> 12) + (MAX_RAW_BYTES >> 14)
                   + (MAX_RAW_BYTES >> 25) + 13)
MAX_ENCODED_BYTES = HEADER_BYTES + _MAX_ZLIB_BYTES


class PayloadCodecError(ValueError):
    """A stored codec value is malformed or exceeds the enforced decode bound."""


def has_codec_magic(value):
    """Whether a BLOB claims this codec's reserved magic prefix (including bad versions)."""
    return isinstance(value, bytes) and value.startswith((MAGIC_PREFIX, b'KAC', b'KAB', b'KAT'))


def decode_codec_if_magic(value):
    """Decode framed BLOBs, leaving ordinary legacy SQLite values/types untouched."""
    return _decode_framed(value) if has_codec_magic(value) else value


def encode_text(text):
    """Return exact TEXT unless a v1 zlib BLOB saves at least 128 bytes and 10 percent."""
    if not isinstance(text, str):
        raise TypeError('payload codec input must be JSON text')
    raw = text.encode('utf-8')
    if len(raw) > MAX_RAW_BYTES:
        # Preserve the old writer's ability to store large result records as plain TEXT.
        return text
    compressed = zlib.compress(raw, COMPRESSION_LEVEL)
    encoded = _HEADER.pack(MAGIC, len(raw), hashlib.sha256(raw).digest()) + compressed
    saving = len(raw) - len(encoded)
    if saving < MIN_SAVING_BYTES or saving / len(raw) < MIN_SAVING_RATIO:
        return text
    return encoded


def _decode_compressed(value):
    if len(value) > MAX_ENCODED_BYTES:
        raise PayloadCodecError('compressed payload exceeds the encoded-size bound')
    if len(value) < HEADER_BYTES:
        raise PayloadCodecError('compressed payload header is truncated')
    magic, raw_length, digest = _HEADER.unpack(value[:HEADER_BYTES])
    if magic != MAGIC:
        raise PayloadCodecError('unsupported compressed payload version')
    if raw_length > MAX_RAW_BYTES:
        raise PayloadCodecError('compressed payload declares more than the 8 MiB raw limit')
    compressed = value[HEADER_BYTES:]
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(compressed, raw_length + 1)
    except zlib.error as exc:
        raise PayloadCodecError('compressed payload stream is invalid') from exc
    if len(raw) > raw_length or inflater.unconsumed_tail:
        raise PayloadCodecError('compressed payload exceeds its declared raw length')
    if not inflater.eof:
        raise PayloadCodecError('compressed payload stream is truncated')
    if inflater.unused_data:
        raise PayloadCodecError('compressed payload has trailing data')
    if len(raw) != raw_length:
        raise PayloadCodecError('compressed payload length does not match its header')
    if not hmac.compare_digest(hashlib.sha256(raw).digest(), digest):
        raise PayloadCodecError('compressed payload checksum does not match')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise PayloadCodecError('compressed payload is not valid UTF-8') from exc


def decode_text(value):
    """Return legacy exact text or packed canonical JSON from a SQLite TEXT/BLOB cell.

    Non-magic BLOBs use the same encoding detection and ``surrogatepass`` error handler as
    ``json.loads(bytes)``.
    A reserved-magic BLOB is always treated as codec data and malformed versions fail closed.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        if has_codec_magic(value):
            return _decode_framed(value)
        try:
            # Match the encoding detection accepted by json.loads(bytes): legacy direct-SQL BLOBs
            # may carry UTF-8/16/32 or a byte-order mark even though application writers used TEXT.
            return value.decode(json.detect_encoding(value), 'surrogatepass')
        except (UnicodeDecodeError, LookupError) as exc:
            raise PayloadCodecError('legacy payload BLOB is not valid JSON text encoding') from exc
    raise PayloadCodecError('payload cell must be TEXT or BLOB, got %s' % type(value).__name__)


def decode_prefix(value, max_characters):
    """Decode at most ``max_characters`` Unicode codepoints, preserving TEXT substr semantics.

    Codec BLOBs are fully and strictly decoded under ``MAX_RAW_BYTES`` before slicing. Legacy raw
    JSON BLOB prefixes use json.loads' UTF-8/16/32 detection and may end mid-codepoint/code-unit;
    only an incomplete final character at the byte-prefix boundary is deferred. Other malformed
    bytes fail closed, except surrogate code units accepted by json.loads' surrogatepass behavior.
    """
    if isinstance(max_characters, bool) or not isinstance(max_characters, int) or max_characters < 0:
        raise ValueError('max_characters must be a non-negative integer')
    if value is None:
        return None
    if isinstance(value, str):
        return value[:max_characters]
    if not isinstance(value, bytes):
        raise PayloadCodecError('payload prefix must be TEXT or BLOB, got %s' % type(value).__name__)
    if has_codec_magic(value):
        return _decode_framed(value)[:max_characters]
    try:
        encoding = json.detect_encoding(value)
        # json.loads(bytes) uses surrogatepass, so preserve legacy direct-SQL BLOB behavior for
        # lone surrogate code units while still rejecting other malformed sequences.
        decoder = codecs.getincrementaldecoder(encoding)('surrogatepass')
        # final=False allows only a cut final code unit/character at the SQL byte-prefix edge.
        # Invalid bytes before that edge still raise rather than becoming silently replaced.
        text = decoder.decode(value, final=False)
    except (UnicodeDecodeError, LookupError) as exc:
        raise PayloadCodecError('legacy payload BLOB prefix is not valid JSON text encoding') from exc
    return text[:max_characters]

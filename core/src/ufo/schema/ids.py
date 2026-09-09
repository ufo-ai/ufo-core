"""Row identities that are not derived from a row's content."""

import os
import threading
import time
from uuid import UUID

_lock = threading.Lock()
_last_ms = 0
_seq = 0


def uuid7() -> UUID:
    """A time-ordered id (RFC 9562 UUIDv7): 48 bits of Unix milliseconds, then a 12-bit sequence
    that climbs within one millisecond so two ids minted back to back stay ordered, then 62 random
    bits. Inserts under a B-tree land at its right edge instead of scattering the way a
    content hash does. Python 3.12 ships no `uuid.uuid7`."""
    global _last_ms, _seq
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms <= _last_ms:
            now_ms = _last_ms
            _seq += 1
            if _seq >= 1 << 12:
                now_ms += 1
                _seq = 0
        else:
            _seq = int.from_bytes(os.urandom(2), "big") >> 5
        _last_ms = now_ms
        seq = _seq
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (now_ms << 80) | (0x7 << 76) | (seq << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)

"""UUIDv7 без внешней библиотеки. Метка времени стоит впереди, хвост случайный."""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    timestamp_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFF_FFFF_FFFF_FFFF
    value = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_id() -> str:
    return str(uuid7())

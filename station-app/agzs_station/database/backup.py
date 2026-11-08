"""Согласованная копия SQLite через API резервного копирования, не через копирование файла."""

import sqlite3
from pathlib import Path


def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()

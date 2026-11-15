"""Read-only access to the live apps' Postgres. Two independent safeguards:
the role you give us must be SELECT-only, AND every connection is opened read-only here.
Queries are fixed in code (never model-written) and parametrised."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


class ConnectorDisabled(RuntimeError):
    pass


@contextmanager
def ro_connect(url: str, name: str) -> Iterator[psycopg.Connection]:
    if not url:
        raise ConnectorDisabled(f"{name} read-only database URL is not configured")
    conn = psycopg.connect(url, row_factory=dict_row, options="-c default_transaction_read_only=on",
                           connect_timeout=10)
    try:
        conn.read_only = True
        yield conn
    finally:
        conn.rollback()
        conn.close()


def one(conn, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()


def rows(conn, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchall()

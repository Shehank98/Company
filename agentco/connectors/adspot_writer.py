"""Narrow, audited writer for AdSpot STATUS fields only. Needs ADSPOT_WRITE_DATABASE_URL, a role that can UPDATE
just these columns (see README). Not configured => AgentCo records state itself and tells the owner so.
Never touches customer fields, amounts or anything else."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from ..config import get_settings


class WriterDisabled(RuntimeError):
    pass


def _conn():
    url = get_settings().adspot_write_database_url
    if not url:
        raise WriterDisabled("ADSPOT_WRITE_DATABASE_URL is not configured")
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)


def mark_paid(booking_ref: str, reference: str | None = None) -> bool:
    """payment_status=completed, status=paid. Idempotent: only changes a booking that is not already completed."""
    with _conn() as c:
        n = c.execute("UPDATE bookings SET payment_status='completed', status='paid', payment_reference=COALESCE(%s, payment_reference), "
                      "updated_at=now() WHERE booking_id=%s AND payment_status IS DISTINCT FROM 'completed'",
                      (reference, booking_ref)).rowcount
    return n == 1


def mark_sent_to_publication(booking_ref: str) -> bool:
    with _conn() as c:
        return c.execute("UPDATE bookings SET sent_to_publication_at=now(), updated_at=now() "
                         "WHERE booking_id=%s AND sent_to_publication_at IS NULL", (booking_ref,)).rowcount == 1

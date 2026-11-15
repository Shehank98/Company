"""AdSpot Media connector (Railway Postgres, read-only). Column names verified against
adspot_media/server/migrations (see docs/systems-map.md). Numbers-only output: no customer PII."""
from __future__ import annotations

from ..config import get_settings
from .readonly import one, ro_connect, rows


def _counts(conn, col: str) -> dict:
    assert col in {"status", "payment_status", "receipt_status", "artwork_status"}  # fixed list, not user input
    return {r["k"]: r["n"] for r in rows(conn, f"SELECT COALESCE({col},'null') AS k, COUNT(*)::int AS n FROM bookings GROUP BY 1")}


def snapshot() -> dict:
    with ro_connect(get_settings().adspot_ro_database_url, "adspot") as c:
        out: dict = {
            "bookings_by_status": _counts(c, "status"),
            "bookings_by_payment_status": _counts(c, "payment_status"),
            "bookings_by_receipt_status": _counts(c, "receipt_status"),
            "bookings_by_artwork_status": _counts(c, "artwork_status"),
            "new_bookings_24h": one(c, "SELECT COUNT(*)::int AS n FROM bookings WHERE created_at >= now() - interval '24 hours'")["n"],
            "new_bookings_7d": one(c, "SELECT COUNT(*)::int AS n FROM bookings WHERE created_at >= now() - interval '7 days'")["n"],
            # ponytail: bookings has no paid_at column; updated_at is only a proxy for "when it was paid".
            "revenue_lkr_completed_7d": float(one(c, "SELECT COALESCE(SUM(total_amount),0) AS s FROM bookings WHERE payment_status='completed' AND updated_at >= now() - interval '7 days'")["s"]),
            "revenue_lkr_completed_30d": float(one(c, "SELECT COALESCE(SUM(total_amount),0) AS s FROM bookings WHERE payment_status='completed' AND updated_at >= now() - interval '30 days'")["s"]),
            "receipts_awaiting_review": one(c, "SELECT COUNT(*)::int AS n FROM bookings WHERE receipt_status='submitted'")["n"],
            "paid_not_sent_to_publication": one(c, "SELECT COUNT(*)::int AS n FROM bookings WHERE payment_status='completed' AND sent_to_publication_at IS NULL AND COALESCE(status,'') <> 'cancelled'")["n"],
            "sent_not_published": one(c, "SELECT COUNT(*)::int AS n FROM bookings WHERE sent_to_publication_at IS NOT NULL AND published_at IS NULL")["n"],
            "unpaid_older_than_3d": one(c, "SELECT COUNT(*)::int AS n FROM bookings WHERE payment_status='pending' AND COALESCE(status,'') <> 'cancelled' AND created_at < now() - interval '3 days'")["n"],
            "design_requests_pending": one(c, "SELECT COUNT(*)::int AS n FROM design_requests WHERE status='pending'")["n"],
            # items[].pubDate is a 'YYYY-MM-DD' string inside JSONB; CASE guards bad values before the cast.
            "bookings_publishing_next_7d": one(c, """
                SELECT COUNT(DISTINCT b.id)::int AS n FROM bookings b,
                  jsonb_array_elements(CASE WHEN jsonb_typeof(b.items)='array' THEN b.items ELSE '[]'::jsonb END) it
                WHERE COALESCE(b.status,'') <> 'cancelled'
                  AND (CASE WHEN it->>'pubDate' ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN (it->>'pubDate')::date END)
                      BETWEEN CURRENT_DATE AND CURRENT_DATE + 7""")["n"],
        }
    return out

"""Sidadiya connector (Railway Postgres, read-only). Tables/columns verified against
SBWEB/src/db/schema.sql + migrations (see docs/systems-map.md). Numbers-only output."""
from __future__ import annotations

from ..config import get_settings
from .readonly import one, ro_connect, rows


def snapshot() -> dict:
    with ro_connect(get_settings().sidadiya_ro_database_url, "sidadiya") as c:
        return {
            "shops_by_status": {r["k"]: r["n"] for r in rows(c, "SELECT COALESCE(status,'null') AS k, COUNT(*)::int AS n FROM businesses GROUP BY 1")},
            "signups_24h": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE created_at >= now() - interval '24 hours'")["n"],
            "signups_7d": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE created_at >= now() - interval '7 days'")["n"],
            "trials_ending_within_3d": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE status='TRIAL' AND trial_ends_at <= now() + interval '3 days'")["n"],
            "trials_expired_unconverted": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE status='TRIAL_EXPIRED'")["n"],
            "paid_active_shops": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE status IN ('ACTIVE','EXPIRING','GRACE_PERIOD')")["n"],
            "churn_suspended_or_cancelled": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE status IN ('SUSPENDED','CANCELLED')")["n"],
            "plan_slips_pending_review": one(c, "SELECT COUNT(*)::int AS n FROM payments WHERE status='PENDING'")["n"],
            "verification_pending": one(c, "SELECT COUNT(*)::int AS n FROM businesses WHERE verification_status='PENDING'")["n"],
            "revenue_lkr_approved_24h": float(one(c, "SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE status='APPROVED' AND reviewed_at >= now() - interval '24 hours'")["s"]),
            "revenue_lkr_approved_30d": float(one(c, "SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE status='APPROVED' AND reviewed_at >= now() - interval '30 days'")["s"]),
            # status values of the email outbox were not enumerated in the code audit, so group generically.
            "email_outbox_by_status": {r["k"]: r["n"] for r in rows(c, "SELECT COALESCE(status,'null') AS k, COUNT(*)::int AS n FROM notifications GROUP BY 1")},
            "latest_job_runs": [{"job": r["job"], "period": str(r["period"]), "finished": r["finished_at"] is not None}
                                for r in rows(c, "SELECT job, period, finished_at FROM job_runs ORDER BY started_at DESC LIMIT 4")],
        }

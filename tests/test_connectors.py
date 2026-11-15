import psycopg
import pytest

from agentco.config import get_settings
from agentco.connectors import adspot, sidadiya
from agentco.connectors.readonly import ConnectorDisabled, ro_connect


def test_adspot_snapshot_matches_fixture():
    s = adspot.snapshot()
    assert s["bookings_by_payment_status"] == {"pending": 2, "completed": 1}
    assert s["receipts_awaiting_review"] == 1
    assert s["paid_not_sent_to_publication"] == 1
    assert s["unpaid_older_than_3d"] == 1
    assert s["design_requests_pending"] == 1
    assert s["bookings_publishing_next_7d"] == 1            # bad pubDate strings are ignored, not fatal
    assert s["revenue_lkr_completed_30d"] == 12000.0
    assert "customer_email" not in str(s)                    # numbers only, no PII


def test_sidadiya_snapshot_matches_fixture():
    s = sidadiya.snapshot()
    assert s["shops_by_status"]["TRIAL"] == 1
    assert s["trials_ending_within_3d"] == 1 and s["plan_slips_pending_review"] == 1
    assert s["revenue_lkr_approved_30d"] == 1399.0
    assert s["email_outbox_by_status"] == {"SENT": 2, "FAILED": 1}


def test_connector_cannot_write():
    with ro_connect(get_settings().adspot_ro_database_url, "adspot") as c:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            c.execute("UPDATE bookings SET status='paid'")
    with ro_connect(get_settings().sidadiya_ro_database_url, "sidadiya") as c:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            c.execute("DELETE FROM payments")


def test_unconfigured_connector_is_disabled():
    with pytest.raises(ConnectorDisabled):
        with ro_connect("", "x"):
            pass

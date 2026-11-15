import pytest

from agentco.llm import router
from agentco.llm.base import LLMError, PolicyViolation
from agentco.models import DataClass
from agentco.scope import company_scope


def _call(s, dc, provider=None):
    return router.complete(scope=s, agent_id=None, run_id=None, task="t", data_class=dc, system="s",
                           prompt="hello", provider=provider)


def test_confidential_and_internal_never_go_to_omniroute():
    with company_scope("adspot") as s:
        for dc in (DataClass.CONFIDENTIAL, DataClass.INTERNAL):
            with pytest.raises(PolicyViolation):
                _call(s, dc, "omniroute")


def test_public_omniroute_not_built_yet():
    with company_scope("adspot") as s:
        with pytest.raises(LLMError):
            _call(s, DataClass.PUBLIC, "omniroute")


def test_every_call_is_costed_with_its_data_class():
    with company_scope("adspot") as s:
        _call(s, DataClass.CONFIDENTIAL)
        row = s.conn.execute("SELECT data_class, provider, cost_usd FROM cost_events").fetchone()
        assert row["data_class"] == "CONFIDENTIAL" and float(row["cost_usd"]) > 0

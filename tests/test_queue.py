from concurrent.futures import ThreadPoolExecutor

from agentco import db
from agentco.orchestrator import queue
from conftest import co_id


def test_skip_locked_never_hands_a_job_to_two_workers():
    with db.admin() as c:
        for i in range(40):
            queue.enqueue(c, company_id=co_id("adspot"), agent_id=None, kind="x", dedupe_key=f"k{i}")
    def drain():
        got = []
        with db.admin() as c:
            while (j := queue.claim_one(c)):
                got.append(j["id"])
        return got
    with ThreadPoolExecutor(4) as ex:
        results = [f.result() for f in [ex.submit(drain) for _ in range(4)]]
    flat = [i for r in results for i in r]
    assert len(flat) == 40 and len(set(flat)) == 40


def test_enqueue_is_idempotent():
    with db.admin() as c:
        a = queue.enqueue(c, company_id=co_id("adspot"), agent_id=None, kind="x", dedupe_key="same")
        b = queue.enqueue(c, company_id=co_id("adspot"), agent_id=None, kind="x", dedupe_key="same")
    assert a and b is None

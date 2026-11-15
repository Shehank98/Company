"""Worker service: claims jobs and runs them. `python -m agentco.worker`"""
import logging
import time

from . import db
from .agents.runner import run_job
from .orchestrator import queue

log = logging.getLogger("agentco.worker")


def work_once() -> bool:
    with db.admin() as conn:
        queue.requeue_stuck(conn)
        job = queue.claim_one(conn)
    if not job:
        return False
    try:
        outcome = run_job(job)
    except Exception as e:  # defensive: runner already converts expected failures
        outcome = f"failed:{type(e).__name__}"
        log.exception("job %s crashed", job["id"])
    with db.admin() as conn:
        queue.finish(conn, job["id"], outcome)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("worker started")
    while True:
        if not work_once():
            time.sleep(2)


if __name__ == "__main__":
    main()

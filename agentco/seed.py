"""Idempotent seed: two isolated companies and their departments' agents."""
from . import db
from .config import get_settings
from .scope import scope_by_id

# (dept, name, role, status, schedule)
AGENTS = {
    "adspot": [
        ("command", "Command", "command", "active", None),
        ("operations", "AdSpot Ops", "ops_daily", "active", "07:30"),
        ("customer_desk", "Customer Desk", "customer_desk", "paused", None),   # Phase 2
        ("marketing", "Marketing", "marketing", "paused", None),               # Phase 3
        ("finance", "Finance", "finance", "paused", None),                     # Phase 2
        ("engineering", "Engineering", "engineering", "paused", None),         # Phase 4
    ],
    "sidadiya": [
        ("command", "Command", "command", "active", None),
        ("insights", "Sidadiya Insights", "insights_daily", "active", "07:30"),
        ("operations", "Sidadiya Ops", "operations", "paused", None),          # Phase 3 (slip checker)
        ("marketing", "Marketing", "marketing", "paused", None),
        ("engineering", "Engineering", "engineering", "paused", None),
    ],
}
COMPANIES = {"adspot": "AdSpot Media", "sidadiya": "Sidadiya"}


def seed() -> None:
    budget = get_settings().default_agent_budget_usd
    with db.admin() as a:
        for slug, name in COMPANIES.items():
            a.execute("INSERT INTO companies (slug, name) VALUES (%s,%s) ON CONFLICT (slug) DO NOTHING", (slug, name))
        cos = {r["slug"]: r["id"] for r in a.execute("SELECT id, slug FROM companies").fetchall()}
    for slug, agents in AGENTS.items():
        with scope_by_id(cos[slug], slug) as s:
            for dept, name, role, status, sched in agents:
                s.conn.execute(
                    "INSERT INTO agents (company_id, dept, name, role, status, monthly_budget_usd, schedule_hhmm) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (company_id, name) DO NOTHING",
                    (cos[slug], dept, name, role, status, budget, sched))


if __name__ == "__main__":
    db.migrate()
    seed()
    print("seeded")

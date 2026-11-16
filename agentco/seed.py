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
        ("marketing", "Marketing", "marketing", "active", None),               # manual-publish packs
        ("finance", "Finance", "finance", "paused", None),                     # Phase 2
        ("engineering", "Engineering", "engineering", "paused", None),         # Phase 4
    ],
    "sidadiya": [
        ("command", "Command", "command", "active", None),
        ("insights", "Sidadiya Insights", "insights_daily", "active", "07:30"),
        ("operations", "Sidadiya Ops", "operations", "paused", None),          # Phase 3 (slip checker)
        ("marketing", "Marketing", "marketing", "active", None),
        ("engineering", "Engineering", "engineering", "paused", None),
    ],
}
# Only these facts (plus the post topic) feed marketing copy. REVIEW AND EDIT in the Posts tab.
# Deliberately no prices, discounts or guarantees: add them yourself if you want them used.
FACTS = {
    "adspot": {
        "about": "AdSpot Media is Sri Lanka's first online newspaper ad booking platform.",
        "website": "adspotmedia.lk",
        "how_it_works": "Pick a newspaper, design a box ad or write a classified ad, and book online in minutes.",
        "payment": "Pay by bank transfer or HelaPay QR. You get a confirmation email with a PDF quotation.",
        "audience": "Businesses and individuals in Sri Lanka, and Sri Lankans living overseas.",
        "tone": "Friendly, clear, practical. English first.",
    },
    "sidadiya": {
        "about": "Sidadiya helps small Sri Lankan sellers build their own online shop.",
        "website": "sidadiya.com",
        "trial": "Every new shop starts with a 14-day trial.",
        "plans": "Simple monthly plans after the trial.",
        "payments": "Shops can accept card payments, bank transfer or cash on delivery.",
        "tracking": "Buyers get order tracking links by email.",
        "tone": "Warm, encouraging, simple English. Aimed at small sellers.",
    },
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
            for k, v in FACTS[slug].items():
                s.conn.execute("INSERT INTO public_facts (company_id, key, value) VALUES (%s,%s,%s) "
                               "ON CONFLICT (company_id, key) DO NOTHING", (cos[slug], k, v))


if __name__ == "__main__":
    db.migrate()
    seed()
    print("seeded")

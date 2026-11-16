"""Manual-publish content packs: request -> Marketing agent drafts -> you copy it into Facebook yourself."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

from . import db
from .images import provider as images
from .agents import budget
from .llm import router
from .models import DataClass
from .orchestrator import queue
from .scope import CompanyScope, company_scope
from .security.public_guard import assert_public_safe, problems

ASPECTS = {"facebook": "1:1 (1080x1080) or 1.91:1 (1200x628)", "instagram": "4:5 (1080x1350)",
           "x": "16:9 (1600x900)", "linkedin": "1.91:1 (1200x627)"}


class AdSetup(BaseModel):
    objective: str = ""
    audience: str = ""
    placements: str = ""
    suggested_daily_budget_note: str = ""
    schedule_note: str = ""
    headline: str = ""
    primary_text: str = ""
    call_to_action: str = ""
    destination_url: str = ""


class PackDraft(BaseModel):
    facebook: str
    instagram: str = ""
    x: str = Field(default="", max_length=400)
    linkedin: str = ""
    hashtags: list[str] = []
    image_brief: str = ""
    image_prompt: str = ""
    ad_setup: AdSetup | None = None


class ClaimsCheck(BaseModel):
    ok: bool = True
    issues: list[str] = []


COPY_SYSTEM = (
    "You are the copywriter and designer for {company}. Write in simple, friendly English for Sri Lankan "
    "customers (and the overseas Sri Lankan community). Use ONLY the facts provided. Do NOT invent prices, "
    "discounts, guarantees, statistics, testimonials, deadlines or features. Keep every platform version "
    "native: X under 280 characters, Facebook/LinkedIn a little longer, Instagram a caption. Also write "
    "an image_brief (what the picture shows, mood, colours) and an exact image_prompt for an image model "
    "(no text inside the image, no real people's names, no customer data). For kind=ad also fill ad_setup "
    "as a sheet the owner will type into Facebook Ads Manager by hand. Return JSON only with keys: "
    "facebook, instagram, x, linkedin, hashtags (list), image_brief, image_prompt, ad_setup (object or null)."
)
CHECK_SYSTEM = (
    "You are a brand and claims checker. Compare the draft with the allowed facts. List every claim, number, "
    "price, guarantee or promise that is NOT supported by the facts, and anything off-brand. "
    'Return JSON only: {"ok": true|false, "issues": ["..."]}. Be strict.'
)


def request_pack(slug: str, kind: str, topic: str, image_mode: str = "flow_manual") -> int:
    """Create a pack row and queue the Marketing agent. Raises ValueError/LookupError for bad requests."""
    topic = (topic or "").strip()
    if not 3 <= len(topic) <= 300:
        raise ValueError("topic must be 3-300 characters")
    if kind not in ("post", "ad") or image_mode not in ("flow_manual", "gemini_image"):
        raise ValueError("bad kind or image_mode")
    assert_public_safe(topic, "topic")
    with company_scope(slug) as s:
        mk = next((a for a in s.list_agents() if a["role"] == "marketing"), None)
        if mk is None or mk["status"] != "active":
            raise LookupError("The Marketing agent is paused. Resume it on the Departments tab.")
        pack_id = s.create_pack(kind=kind, topic=topic, image_mode=image_mode, agent_id=mk["id"])
        company_id, agent_id = s.company_id, mk["id"]
    with db.admin() as a:
        queue.enqueue(a, company_id=company_id, agent_id=agent_id, kind="content_pack", payload={"pack_id": pack_id})
    return pack_id


def _facts_block(scope: CompanyScope) -> str:
    return "\n".join(f"- {f['key']}: {f['value']}" for f in scope.list_facts())


def build(scope: CompanyScope, agent: dict, run_id: int, pack_id: int, company_name: str) -> str:
    """Draft one pack. Returns a one-line summary for the chat. Model input is PUBLIC data only."""
    from .tools import registry
    pack = scope.get_pack(pack_id)
    if pack is None:
        raise LookupError("pack not found")
    facts = _facts_block(scope)
    prompt = f"kind: {pack['kind']}\ntopic: {pack['topic']}\nfacts:\n{facts}"

    budget.ensure(scope, agent)
    res = router.complete(scope=scope, agent_id=agent["id"], run_id=run_id, task="content_copy",
                          data_class=DataClass.PUBLIC, system=COPY_SYSTEM.format(company=company_name),
                          prompt=prompt, tier=agent["model_tier"], json_mode=True)
    try:
        draft = PackDraft.model_validate_json(res.text)
    except (ValidationError, ValueError) as e:
        raise ValueError("the model did not return a usable draft") from e

    issues = problems(json.dumps(draft.model_dump()))
    budget.ensure(scope, agent)
    chk = router.complete(scope=scope, agent_id=agent["id"], run_id=run_id, task="claims_check",
                          data_class=DataClass.PUBLIC, system=CHECK_SYSTEM,
                          prompt=f"facts:\n{facts}\n\ndraft:\n{json.dumps(draft.model_dump(), indent=1)}",
                          tier="flash", json_mode=True)
    try:
        c = ClaimsCheck.model_validate_json(chk.text)
        issues += c.issues
    except (ValidationError, ValueError):
        issues.append("claims check returned no usable result: review the text yourself")

    image_file, image_note = None, None
    if pack["image_mode"] == "gemini_image":
        try:
            assert_public_safe(draft.image_prompt, "image prompt")
            img = images.generate(draft.image_prompt, "1:1")
            from pathlib import Path
            import uuid
            from .config import get_settings
            folder = Path(get_settings().upload_dir) / "content"
            folder.mkdir(parents=True, exist_ok=True)
            image_file = f"{uuid.uuid4().hex}{img.ext}"
            (folder / image_file).write_bytes(img.data)
            scope.add_cost(agent_id=agent["id"], run_id=run_id, provider=img.provider, model=img.model, route="image",
                           data_class=DataClass.PUBLIC, task="content_image", tokens_in=0, tokens_out=0,
                           cost_usd=img.cost_usd)
        except Exception as e:  # the draft is still useful: fall back to the manual Flow prompt
            image_note = f"Automatic image failed ({type(e).__name__}); use the prompt in Google Flow."

    status = "needs_review" if issues else "draft"
    if image_note:
        issues.append(image_note)
    r = registry.call(scope, agent, run_id, "save_content_pack", {
        "pack_id": pack_id, "status": status, "copy": {k: getattr(draft, k) for k in ("facebook", "instagram", "x", "linkedin")},
        "hashtags": draft.hashtags, "image_brief": draft.image_brief, "image_prompt": draft.image_prompt,
        "aspect_ratios": ASPECTS, "ad_setup": draft.ad_setup.model_dump() if draft.ad_setup else None,
        "claims_check": {"ok": not issues, "issues": issues}})
    if r["status"] != "ok":
        raise RuntimeError(f"could not save pack: {r}")
    if image_file:
        scope.update_pack(pack_id, image_file=image_file)
    scope.emit("content.drafted", {"pack_id": pack_id}, agent["dept"])
    flag = f" ({len(issues)} thing(s) to check)" if issues else ""
    return f"{company_name}: {pack['kind']} draft #{pack_id} is ready in the Posts tab{flag}."

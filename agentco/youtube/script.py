"""Scriptwriter + Director for Shorts. Method from the vendored youtube-agent-skill: FIVE hooks written against its 21
formulas, each scored by its hookscore tool (never eyeballed), top two kept, best one used; then beats with an
ON-SCREEN line on every beat. Model input is PUBLIC data only (topic + brand facts); nothing is invented."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from ..agents import budget
from ..llm import router
from ..models import DataClass
from ..scope import CompanyScope
from ..security.public_guard import problems
from .vendor.yt_skill import hookscore

MAX_SPOKEN_WORDS = 125            # ~48 s at 2.6 words/s, leaves room for pauses under the 60 s Shorts limit
FORMULAS = [f"- {h['id']}: {h['shape']}" for h in hookscore.FORMULAS]


class HookOption(BaseModel):
    formula: str = Field(max_length=60)
    line: str = Field(min_length=8, max_length=220)


class HookSet(BaseModel):
    hooks: list[HookOption] = Field(min_length=3, max_length=8)


class Beat(BaseModel):
    spoken: str = Field(min_length=3, max_length=300)
    on_screen: str = Field(default="", max_length=90)
    visual_prompt: str = Field(min_length=5, max_length=600)


class ScriptOut(BaseModel):
    title: str = Field(min_length=3, max_length=100)
    description: str = Field(default="", max_length=1500)
    tags: list[str] = Field(default_factory=list, max_length=12)
    beats: list[Beat] = Field(min_length=3, max_length=8)


HOOK_SYSTEM = (
    "You are the hook writer for YouTube Shorts for {company}. Write EXACTLY five different hooks, each a single spoken line "
    "of at most 22 words, each built on a different formula from this list (use the formula id):\n{formulas}\n"
    "Rules: the first sentence confirms the topic; one idea only; specific beats big; address the viewer as 'you'; never invent "
    "a number, result or source (use only the facts given; if you have no figure, write the hook without one). No 'hey guys', "
    "no channel intro. Return JSON only: {{\"hooks\": [{{\"formula\": \"<id>\", \"line\": \"...\"}}]}}")

SCRIPT_SYSTEM = (
    "You are the script writer and director for a faceless YouTube Short for {company}. Voice and style come from the facts. "
    "Use ONLY the facts given: no invented numbers, prices, results, testimonials or promises. Write 4 to 7 beats. "
    "Beat 1's spoken line is the chosen hook line, word for word. The whole spoken script is at most 125 words. No channel intro, "
    "no 'before we start', no subscribe pitch; end with ONE soft ask (visit the website from the facts). For EVERY beat give: "
    "spoken (what the voice says), on_screen (a short label of at most 7 words, DIFFERENT words from the spoken line), and "
    "visual_prompt (an exact prompt for an AI video/image model: vertical 9:16, scene, motion, camera, style from the facts, at "
    "most 60 words, NO text or logos in the image, NO real people or famous faces). Also give title (at most 70 characters, "
    "must match the hook), description (2-3 short lines ending with the website) and up to 8 tags. Return JSON only: "
    "{{\"title\", \"description\", \"tags\", \"beats\": [{{\"spoken\", \"on_screen\", \"visual_prompt\"}}]}}")

CHECK_SYSTEM = (
    "You are a brand and claims checker. Compare the Short's script with the allowed facts. List every claim, number, price, "
    "guarantee or promise that is NOT supported by the facts, and anything off-brand. "
    'Return JSON only: {"ok": true|false, "issues": ["..."]}. Be strict.')


def _facts(scope: CompanyScope) -> str:
    return "\n".join(f"- {f['key']}: {f['value']}" for f in scope.list_facts())


def words(beats: list[Beat]) -> int:
    return sum(len(b.spoken.split()) for b in beats)


def score_hooks(options: list[HookOption]) -> list[dict]:
    out = []
    for o in options:
        parts, verdict, name, _ = hookscore.score(o.line)
        out.append({"formula": o.formula, "line": o.line, "score": verdict, "band": hookscore.band(verdict),
                    "detected_formula": name, "panel": parts, "blocked": problems(o.line)})
    return sorted(out, key=lambda h: (-h["score"],))


def build(scope: CompanyScope, agent: dict, run_id: int, pid: int, company_name: str) -> str:
    from ..tools import registry
    proj = scope.conn.execute("SELECT * FROM video_projects WHERE id=%s AND company_id=%s", (pid, scope.company_id)).fetchone()
    if proj is None:
        raise LookupError("video project not found")
    facts = _facts(scope)

    def ask(task: str, system: str, prompt: str):
        budget.ensure(scope, agent)                    # hard budget: check before EVERY model call, not once per job
        return router.complete(scope=scope, agent_id=agent["id"], run_id=run_id, task=task, data_class=DataClass.PUBLIC,
                               system=system, prompt=prompt, tier=agent["model_tier"], json_mode=True)

    # 1) five hooks, all scored by the skill's tool; the best one drives the script
    try:
        hooks = HookSet.model_validate_json(ask("video_hooks", HOOK_SYSTEM.format(company=company_name, formulas="\n".join(FORMULAS)),
                                                f"topic: {proj['topic']}\nfacts:\n{facts}").text)
    except (ValidationError, ValueError):
        raise ValueError("the model did not return usable hooks")
    scored = [h for h in score_hooks(hooks.hooks) if not h["blocked"]]
    if not scored:
        raise ValueError("every hook contained private details (email, phone or booking reference)")
    best = scored[0]

    # 2) the script around the best hook (one retry if it is too long for a Short)
    prompt = f"topic: {proj['topic']}\nhook line: {best['line']}\nfacts:\n{facts}"
    script = None
    for attempt in range(2):
        try:
            script = ScriptOut.model_validate_json(ask("video_script", SCRIPT_SYSTEM.format(company=company_name),
                                                       prompt + ("" if attempt == 0 else "\nIMPORTANT: it was too long. Cut it to at most 110 spoken words.")).text)
        except (ValidationError, ValueError):
            script = None
            continue
        if words(script.beats) <= MAX_SPOKEN_WORDS:
            break
        script = None
    if script is None:
        raise ValueError(f"could not get a usable script of at most {MAX_SPOKEN_WORDS} words")
    if best["line"].lower()[:30] not in script.beats[0].spoken.lower():
        script.beats[0].spoken = best["line"]                      # beat 1 is the hook, verbatim

    # 3) claims check + private-detail guard over everything that will be said, shown or published
    everything = json.dumps(script.model_dump())
    issues = problems(everything)
    chk = ask("video_claims_check", CHECK_SYSTEM, f"facts:\n{facts}\n\nscript:\n{everything}")
    try:
        issues += list(json.loads(chk.text).get("issues", []))
    except (ValueError, AttributeError):
        issues.append("claims check returned no usable result: review the script yourself")

    r = registry.call(scope, agent, run_id, "save_video_script", {
        "project_id": pid, "hook": best, "hook_options": scored[:5], "title": script.title, "description": script.description,
        "tags": script.tags, "beats": [b.model_dump() for b in script.beats],
        "claims_check": {"ok": not issues, "issues": issues}})
    if r["status"] != "ok":
        raise RuntimeError(f"could not save script: {r}")
    scope.emit("video.script_ready", {"project_id": pid}, agent["dept"])
    flag = f" ({len(issues)} thing(s) to check)" if issues else ""
    return f"{company_name}: Short script #{pid} is ready to review in Content > Shorts. Hook scored {best['score']} ({best['band']}){flag}."

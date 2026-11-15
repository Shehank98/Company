# Data classes and LLM routing

| Class | Examples | May go to |
|---|---|---|
| PUBLIC | brand voice, public product info, offers, post topics | Gemini; OmniRoute only if `OMNIROUTE_ENABLED=true` (Phase 3, public_facts only) |
| INTERNAL | revenue, margins, publication rates, daily report numbers | paid Gemini only |
| CONFIDENTIAL | customer names/emails/phones, receipts, mail bodies, payment data, chat text | paid Gemini only |

Enforced in code by `agentco/llm/router.py` (`ALLOWED`); a violation raises `PolicyViolation` and is covered by `tests/test_router.py`.
Every call is written to `cost_events` with its data class. Chat messages are treated as CONFIDENTIAL because they may name customers or bookings.

Untrusted input (emails, attachments, web pages, uploads) is wrapped by `agentco/security/untrusted.py`, and the model is told never to follow instructions inside it. The real protection is structural: tools are gated by department allowlists and tiers (`agentco/tools/registry.py`), so even a model that obeys an injected instruction cannot send, pay or publish (`tests/test_malicious_email.py`).

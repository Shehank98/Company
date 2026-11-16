"""Approved mail templates. Filled ONLY with verified booking data. No free-form model text except the one-line
greeting, which is validated hard (see greeting())."""
from __future__ import annotations

import re
from string import Template

from .agents import budget
from .llm import router
from .llm.base import LLMError
from .models import DataClass
from .scope import CompanyScope

_GREETING_OK = re.compile(r"^(Dear|Hello|Hi) [A-Za-z][A-Za-z .'-]{0,40}[,!]?$")


def first_name(full: str | None) -> str:
    p = (full or "").strip().split()
    return p[0].capitalize() if p else "Customer"


def greeting(scope: CompanyScope, agent: dict | None, run_id: int | None, customer_name: str | None) -> str:
    """The model may only personalise this single line. Anything that is not a plain greeting is discarded."""
    fallback = f"Dear {first_name(customer_name)},"
    if agent is None:
        return fallback
    try:
        budget.ensure(scope, agent)
        res = router.complete(scope=scope, agent_id=agent["id"], run_id=run_id, task="greeting",
                              data_class=DataClass.CONFIDENTIAL,
                              system='Write ONE short, warm greeting line for an email to this customer, using only the '
                                     'first name. Start with "Dear", "Hello" or "Hi". Return JSON {"greeting": "..."} only.',
                              prompt=f"first name: {first_name(customer_name)}", tier="flash", json_mode=True)
        import json
        g = str(json.loads(res.text).get("greeting", "")).strip()
        return g if _GREETING_OK.match(g) and first_name(customer_name).lower() in g.lower() else fallback
    except (LLMError, ValueError, budget.BudgetExceeded):
        return fallback
    except Exception:
        return fallback


def _lines(items: list[dict]) -> str:
    out = []
    for it in items or []:
        out.append(f"- {it.get('newspaperName', 'Newspaper')}, {it.get('adType', 'ad')} ad, publish date {it.get('pubDate', 'to be confirmed')}")
    return "\n".join(out) or "- (details on your quotation)"


def money(n) -> str:
    return f"Rs. {float(n):,.2f}"


PAYMENT = Template("""$greeting

We have received your payment of $amount for booking $booking_ref (quotation $quotation_ref).

Your ad:
$lines

We will now send your ad to the newspaper. You will get another email with the newspaper's receipt once it is issued.

Thank you for booking with AdSpot Media.
AdSpot Media - adspotmedia.lk
""")

RECEIPT = Template("""$greeting

The newspaper has issued a receipt for your booking $booking_ref (quotation $quotation_ref). It is attached to this email.

Your ad:
$lines

Thank you for booking with AdSpot Media.
AdSpot Media - adspotmedia.lk
""")

PUBLICATION = Template("""Dear $group_name team,

Please publish the following advertisement(s). Our reference: $quotation_ref (booking $booking_ref).

$blocks
Edition: not specified in our system, please use the regular edition for the date above.
The advertisement artwork or text is attached or linked above.

Kind regards,
AdSpot Media - adspotmedia.lk
""")


def payment_mail(b: dict, amount, greeting_line: str) -> tuple[str, str]:
    ref = b.get("quotation_ref") or b["booking_ref"]
    return (f"Payment received - {ref}",
            PAYMENT.substitute(greeting=greeting_line, amount=money(amount), booking_ref=b["booking_ref"],
                               quotation_ref=b.get("quotation_ref") or "-", lines=_lines(b["items"])))


def receipt_mail(b: dict, greeting_line: str) -> tuple[str, str]:
    ref = b.get("quotation_ref") or b["booking_ref"]
    return (f"Your ad is scheduled - {ref}",
            RECEIPT.substitute(greeting=greeting_line, booking_ref=b["booking_ref"],
                               quotation_ref=b.get("quotation_ref") or "-", lines=_lines(b["items"])))


def publication_mail(b: dict, group_name: str, items: list[dict]) -> tuple[str, str]:
    ref = b.get("quotation_ref") or b["booking_ref"]
    dates = sorted({i.get("pubDate", "") for i in items if i.get("pubDate")})
    blocks = []
    for it in items:
        d = it.get("details") or {}
        copy = d.get("text") or it.get("description") or ""
        size = f"{d.get('columns')} col x {d.get('height')} cm, {d.get('colorOption', '')}".strip(", ") if d.get("columns") else ""
        blocks.append("\n".join(x for x in [
            f"Newspaper: {it.get('newspaperName', '')}", f"Type: {it.get('adType', '')}",
            f"Publish date: {it.get('pubDate', '')}", f"Size: {size}" if size else "",
            f"Ad text:\n{copy}" if copy else "", f"Artwork: {it['adFileUrl']}" if it.get("adFileUrl") else ""] if x))
    return (f"Ad booking {ref} - publish {', '.join(dates) or 'date on request'}",
            PUBLICATION.substitute(group_name=group_name, quotation_ref=ref, booking_ref=b["booking_ref"],
                                   blocks="\n\n".join(blocks) + "\n"))

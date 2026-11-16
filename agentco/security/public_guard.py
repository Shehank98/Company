"""Anything that ends up in a PUBLIC post or an image prompt must not carry private identifiers."""
import re

_PATTERNS = {
    "an email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "a booking/quotation reference": re.compile(r"\b(?:BOOK-\d{8}-\d{3}|ADM-\d{4}-\d{4}|INV-\d{6,8}-\d{3,4})\b", re.I),
    "a phone number": re.compile(r"(?<!\d)(?:\+94|0)\s?7\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)"),
}


def problems(text: str) -> list[str]:
    return [f"contains {name}" for name, rx in _PATTERNS.items() if rx.search(text or "")]


def assert_public_safe(text: str, what: str = "text") -> None:
    p = problems(text)
    if p:
        raise ValueError(f"{what} {', '.join(p)}; keep customer details out of public content")

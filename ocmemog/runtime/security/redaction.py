"""Input redaction helpers owned by ocmemog."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")


def redact_text(text: str) -> tuple[str, bool]:
    if not isinstance(text, str):
        return "", False
    redacted = EMAIL_RE.sub("[redacted-email]", text)
    redacted = PHONE_RE.sub("[redacted-phone]", redacted)
    return redacted, redacted != text


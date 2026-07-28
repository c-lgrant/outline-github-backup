"""Attachment URL parsing for mirrored markdown.

Outline markdown references uploads as `/api/attachments.redirect?id=<uuid>`
(sometimes absolute, prefixed with the instance URL). The mirror stores the
downloaded bytes under `attachments/<uuid>` and keeps the markdown byte-faithful;
links are relativized only when building a restore zip.
"""

from __future__ import annotations

import re

ATTACHMENT_DIR = "attachments"

_URL_RE = re.compile(
    r"(?:https?://[^\s()<>\"']*)?/api/attachments\.redirect\?id=([0-9a-fA-F-]{36})"
)


def find_attachment_ids(markdown: str) -> list[str]:
    """Attachment ids referenced by the markdown, in order, deduplicated."""
    return list(dict.fromkeys(_URL_RE.findall(markdown)))


def attachment_path(attachment_id: str) -> str:
    return f"{ATTACHMENT_DIR}/{attachment_id}"


def rewrite_for_zip(markdown: str) -> str:
    """Relativize attachment URLs to the zip-root attachments/ folder."""
    return _URL_RE.sub(rf"{ATTACHMENT_DIR}/\1", markdown)

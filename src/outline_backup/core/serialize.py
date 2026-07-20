from __future__ import annotations

import json
import re
import unicodedata


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


def doc_slug(title: str, url_id: str) -> str:
    return f"{slugify(title) or 'untitled'}-{url_id}"


def comment_text(data: dict) -> str:
    """Best-effort plain text from ProseMirror JSON: paragraphs joined by newlines."""
    paragraphs: list[str] = []

    def walk(node: dict) -> str:
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(walk(child) for child in node.get("content", []))

    for block in data.get("content", []):
        paragraphs.append(walk(block))
    return "\n".join(p for p in paragraphs if p)


def comments_sidecar(comments: list[dict]) -> bytes:
    entries = []
    for c in sorted(comments, key=lambda c: (c.get("createdAt") or "", c["id"])):
        created_by = c.get("createdBy") or {}
        entries.append(
            {
                "id": c["id"],
                "createdAt": c.get("createdAt"),
                "parentCommentId": c.get("parentCommentId"),
                "authorName": created_by.get("name"),
                "text": comment_text(c.get("data") or {}),
                "data": c.get("data"),
            }
        )
    return (json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()

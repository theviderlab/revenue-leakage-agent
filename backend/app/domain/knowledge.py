"""Knowledge base (D16, D32): a markdown file, one chunk per `## key`, retrieved by key."""

import re
from functools import lru_cache
from pathlib import Path

from app.domain.errors import DomainError

KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "knowledge.md"


@lru_cache(maxsize=1)
def load_chunks() -> dict[str, str]:
    text = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    chunks: dict[str, str] = {}
    for m in re.finditer(r"^## (\w+)\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S):
        chunks[m.group(1)] = m.group(2).strip()
    return chunks


def concept_keys() -> list[str]:
    return list(load_chunks())


def get_concept(key: str) -> dict:
    chunks = load_chunks()
    k = key.strip().lower().replace("-", "_").replace(" ", "_")
    if k not in chunks:
        raise DomainError(f"unknown concept '{key}'; valid keys: {', '.join(chunks)}")
    return {"key": k, "text": chunks[k]}

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class MemoryItem:
    text: str
    created_at: str


class JsonlMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, text: str) -> MemoryItem:
        item = MemoryItem(text=text.strip(), created_at=datetime.now(timezone.utc).isoformat())
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.__dict__, ensure_ascii=False) + "\n")
        return item

    def all(self) -> list[MemoryItem]:
        if not self.path.exists():
            return []
        items: list[MemoryItem] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                raw = json.loads(line)
                items.append(MemoryItem(text=raw["text"], created_at=raw["created_at"]))
        return items

    def search(self, query: str, limit: int = 5) -> list[MemoryItem]:
        terms = [part.lower() for part in query.split() if part.strip()]
        if not terms:
            return self.all()[-limit:]
        scored: list[tuple[int, MemoryItem]] = []
        for item in self.all():
            haystack = item.text.lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def prompt_block(self, limit: int = 8) -> str:
        recent = self.all()[-limit:]
        if not recent:
            return "No persistent memories yet."
        return "\n".join(f"- {item.text}" for item in recent)


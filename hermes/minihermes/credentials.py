from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Credential:
    alias: str
    protocol: str
    username: str
    password: str
    host: str
    notes: str = ""
    updated_at: str = ""

    def public_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["password"] = "********" if self.password else ""
        return data


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        alias: str,
        protocol: str,
        username: str,
        password: str,
        host: str,
        notes: str = "",
    ) -> Credential:
        credential = Credential(
            alias=_normalize_alias(alias),
            protocol=protocol.strip(),
            username=username.strip(),
            password=password,
            host=host.strip(),
            notes=notes.strip(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        if not credential.alias:
            raise ValueError("alias is required.")
        if not credential.protocol:
            raise ValueError("protocol is required.")
        if not credential.host:
            raise ValueError("host is required.")

        data = self._read()
        data[credential.alias] = asdict(credential)
        self._write(data)
        return credential

    def get(self, alias: str) -> Credential | None:
        raw = self._read().get(_normalize_alias(alias))
        return Credential(**raw) if raw else None

    def delete(self, alias: str) -> bool:
        data = self._read()
        normalized = _normalize_alias(alias)
        if normalized not in data:
            return False
        del data[normalized]
        self._write(data)
        return True

    def list(self) -> list[Credential]:
        return [Credential(**raw) for _, raw in sorted(self._read().items())]

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def _normalize_alias(alias: str) -> str:
    return alias.strip().lower()


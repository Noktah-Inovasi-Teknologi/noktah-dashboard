"""
The Client Card definition (config/hub/card_v1.yaml): load, sync, and the pure
rules built on it: value shapes, emptiness, required fields, completeness.

Sync (at API startup): the YAML is written into `card_definitions`. A version
that any card value references is FROZEN (`frozen_at`); if its YAML changed, the
sync refuses loudly instead of silently reinterpreting stored values
(constitution XII, G-29). A changed field list is a new file and a new version.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import yaml

CURRENT_VERSION = "v1"
PARTS = ("profil", "guideline")


class DefinitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Definition:
    version: str
    body: Dict[str, Any]

    def fields(self, part: str) -> List[Dict[str, Any]]:
        return self.body["parts"][part]["fields"]

    def field(self, part: str, key: str) -> Dict[str, Any]:
        for f in self.fields(part):
            if f["key"] == key:
                return f
        raise KeyError(f"{part}.{key}")

    def has_field(self, part: str, key: str) -> bool:
        return part in PARTS and any(f["key"] == key for f in self.fields(part))

    def choice_keys(self, name: str) -> List[str]:
        return [c["key"] for c in self.body.get("choices", {}).get(name, [])]

    @property
    def always_banned(self) -> List[str]:
        return list(self.body.get("always_banned", []))


def load_file(directory: str, version: str = CURRENT_VERSION) -> Definition:
    path = Path(directory) / f"card_{version}.yaml"
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    if body.get("version") != version:
        raise DefinitionError(f"{path} declares version {body.get('version')!r}, expected {version!r}")
    return Definition(version, body)


async def sync(conn: asyncpg.Connection, definition: Definition) -> str:
    """Write the definition to the table. Returns 'inserted' | 'unchanged' | 'updated'."""
    row = await conn.fetchrow("SELECT body, frozen_at FROM card_definitions WHERE version = $1", definition.version)
    body_json = json.dumps(definition.body, ensure_ascii=False, sort_keys=True)
    if row is None:
        await conn.execute("INSERT INTO card_definitions (version, body) VALUES ($1, $2::text::jsonb)",
                           definition.version, body_json)
        return "inserted"
    stored = json.loads(row["body"]) if isinstance(row["body"], str) else row["body"]
    if json.dumps(stored, ensure_ascii=False, sort_keys=True) == body_json:
        return "unchanged"
    if row["frozen_at"] is not None:
        raise DefinitionError(
            f"card definition {definition.version} is frozen (values reference it) and its YAML changed. "
            "Add a new version file instead of editing this one."
        )
    await conn.execute("UPDATE card_definitions SET body = $2::text::jsonb, synced_at = now() WHERE version = $1",
                       definition.version, body_json)
    return "updated"


async def freeze(conn: asyncpg.Connection, version: str) -> None:
    await conn.execute("UPDATE card_definitions SET frozen_at = now() WHERE version = $1 AND frozen_at IS NULL",
                       version)


# ── Pure rules ────────────────────────────────────────────────────────────────

def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)):
        return all(is_empty(v) for v in value)
    if isinstance(value, dict):
        return all(is_empty(v) for v in value.values())
    return False


def _check_scalar(shape: str, value: Any, definition: Definition, spec: Dict[str, Any], where: str) -> None:
    if value is None:
        return
    if shape == "text":
        if not isinstance(value, str):
            raise ValueError(f"{where}: harus teks")
    elif shape == "list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{where}: harus daftar teks")
    elif shape == "rating":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError(f"{where}: harus angka 1–5")
    elif shape == "choice":
        allowed = definition.choice_keys(spec.get("choices", ""))
        if value not in allowed:
            raise ValueError(f"{where}: harus salah satu dari {allowed}")
    else:
        raise ValueError(f"{where}: bentuk {shape!r} tidak dikenal")


def validate_value(definition: Definition, part: str, key: str, value: Any) -> None:
    """Raise ValueError (Bahasa message) when `value` doesn't fit the field's shape."""
    if not definition.has_field(part, key):
        raise ValueError(f"Kolom {part}.{key} tidak ada di definisi kartu {definition.version}")
    spec = definition.field(part, key)
    shape = spec["shape"]
    where = f"{part}.{key}"
    subs = {s["key"]: s for s in spec.get("subfields", [])}
    if shape in ("text", "list", "rating", "choice"):
        _check_scalar(shape, value, definition, spec, where)
    elif shape == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{where}: harus objek dengan sub-kolom {list(subs)}")
        unknown = set(value) - set(subs)
        if unknown:
            raise ValueError(f"{where}: sub-kolom tidak dikenal {sorted(unknown)}")
        for k, v in value.items():
            _check_scalar(subs[k]["shape"], v, definition, subs[k], f"{where}.{k}")
    elif shape == "lines":
        if not isinstance(value, list):
            raise ValueError(f"{where}: harus daftar baris")
        for i, line in enumerate(value):
            if not isinstance(line, dict):
                raise ValueError(f"{where}[{i}]: harus objek")
            unknown = set(line) - set(subs)
            if unknown:
                raise ValueError(f"{where}[{i}]: sub-kolom tidak dikenal {sorted(unknown)}")
            for k, v in line.items():
                _check_scalar(subs[k]["shape"], v, definition, subs[k], f"{where}[{i}].{k}")
    else:
        raise ValueError(f"{where}: bentuk {shape!r} tidak dikenal")


def satisfies_required(spec: Dict[str, Any], value: Any) -> bool:
    if is_empty(value):
        return False
    wanted = spec.get("required_subfields")
    if wanted:
        return isinstance(value, dict) and all(not is_empty(value.get(k)) for k in wanted)
    return True


def completeness(definition: Definition, current: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Profil: required facts filled / required (G-24). Guideline: sections filled / 10 (G-30)."""
    profil_spec = definition.fields("profil")
    required = [f for f in profil_spec if f.get("required")]
    profil_ok = [f["key"] for f in required if satisfies_required(f, current.get("profil", {}).get(f["key"]))]
    guide_spec = definition.fields("guideline")
    guide_filled = [f["key"] for f in guide_spec if not is_empty(current.get("guideline", {}).get(f["key"]))]
    guide_required = [f for f in guide_spec if f.get("required")]
    guide_missing = [f["key"] for f in guide_required
                     if not satisfies_required(f, current.get("guideline", {}).get(f["key"]))]
    return {
        "profil": {"filled": len(profil_ok), "required": len(required),
                   "missing": [f["key"] for f in required if f["key"] not in profil_ok]},
        "guideline": {"filled": len(guide_filled), "total": len(guide_spec), "missing_required": guide_missing},
    }


def split_key(target: str) -> Tuple[str, Optional[str]]:
    part, _, key = target.partition(".")
    return part, key or None

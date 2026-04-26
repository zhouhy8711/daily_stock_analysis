# -*- coding: utf-8 -*-
"""Discover and load local Codex skills for Agent chat custom modes."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


_MAX_SKILL_BYTES = 256 * 1024
_MAX_INJECTED_CHARS = 24000


@dataclass(frozen=True)
class CodexSkill:
    """A local Codex skill descriptor."""

    id: str
    name: str
    description: str
    source: str
    relative_path: str
    path: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_roots() -> List[tuple[str, Path]]:
    codex_home = Path(os.getenv("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    root = _repo_root()
    candidates = [
        ("user", codex_home / "skills"),
        ("project", root / ".codex" / "skills"),
        ("project", root / ".agents" / "skills"),
    ]

    seen: set[Path] = set()
    result: List[tuple[str, Path]] = []
    for source, path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append((source, resolved))
    return result


def _iter_skill_files() -> Iterable[tuple[str, Path, Path]]:
    for source, root in _candidate_roots():
        if not root.is_dir():
            continue
        for skill_file in sorted(root.rglob("SKILL.md")):
            if not skill_file.is_file():
                continue
            try:
                skill_file.relative_to(root)
            except ValueError:
                continue
            yield source, root, skill_file


def _read_skill_text(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_SKILL_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _extract_frontmatter_value(frontmatter: str, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(frontmatter)
    if not match:
        return ""
    value = match.group(1).strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1].strip()
    return value


def _extract_metadata(text: str, fallback_name: str) -> tuple[str, str]:
    name = ""
    description = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            frontmatter = text[3:end]
            try:
                import yaml

                data = yaml.safe_load(frontmatter) or {}
            except Exception:
                data = {}
            if isinstance(data, dict):
                name = str(data.get("name") or "").strip()
                description = str(data.get("description") or "").strip()
            if not name:
                name = _extract_frontmatter_value(frontmatter, "name")
            if not description:
                description = _extract_frontmatter_value(frontmatter, "description")

    if not name:
        heading = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
        if heading:
            name = heading.group(1).strip()
    if not name:
        name = fallback_name

    if not description:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "---":
                continue
            if ":" in stripped and len(stripped.split(":", 1)[0]) < 24:
                continue
            description = stripped
            break

    return name[:80], description[:240]


def _skill_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def list_codex_skills() -> List[CodexSkill]:
    """Return local Codex skills without exposing full absolute paths."""
    skills: List[CodexSkill] = []
    for source, root, skill_file in _iter_skill_files():
        text = _read_skill_text(skill_file)
        if not text:
            continue
        skill_dir = skill_file.parent
        name, description = _extract_metadata(text, skill_dir.name)
        try:
            relative_path = str(skill_dir.relative_to(root))
        except ValueError:
            relative_path = skill_dir.name
        skills.append(
            CodexSkill(
                id=_skill_id(skill_file),
                name=name,
                description=description,
                source=source,
                relative_path=relative_path,
                path=skill_file.resolve(),
            )
        )

    return sorted(
        skills,
        key=lambda skill: (
            0 if skill.source == "project" else 1,
            skill.name.lower(),
            skill.relative_path,
        ),
    )


def get_codex_skill(skill_id: str) -> Optional[CodexSkill]:
    """Find one local Codex skill by id."""
    wanted = str(skill_id or "").strip()
    if not wanted:
        return None
    for skill in list_codex_skills():
        if skill.id == wanted:
            return skill
    return None


def load_codex_skill_instructions(skill_id: str) -> Optional[Dict[str, str]]:
    """Load bounded skill instructions for prompt injection."""
    skill = get_codex_skill(skill_id)
    if skill is None:
        return None
    text = _read_skill_text(skill.path)
    if not text:
        return None
    truncated = len(text) > _MAX_INJECTED_CHARS
    content = text[:_MAX_INJECTED_CHARS]
    if truncated:
        content += "\n\n[DSA note: skill content truncated for prompt size.]"
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "relative_path": skill.relative_path,
        "content": content,
    }

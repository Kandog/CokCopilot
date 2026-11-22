"""Skills: reusable instructions in Agent-Skills ``SKILL.md`` format, invoked with ``/name``."""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import config
from .models import new_id

SKILL_FILE = "SKILL.md"
MAX_EXTRA_BYTES = 32 * 1024
MAX_ZIP_BYTES = 50 * 1024 * 1024  # unpacked size of a skill .zip
MAX_ZIP_FILES = 1000
TEXT_EXT = {"md", "txt", "json", "yaml", "yml", "csv", "xml", "html", "py", "js", "ts", "rs", "sh", "toml"}


class SkillError(Exception):
    pass


class SkillExists(SkillError):
    def __init__(self, name: str):
        super().__init__(f'a skill named "{name}" is already installed')
        self.name = name


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    dir: Path

    def prompt_block(self) -> str:
        block = (
            f'The user invoked the skill "/{self.name}" ("{self.description}"). When a user message '
            f'starts with "/{self.name}", follow the skill\'s instructions below; the rest of that '
            f'message is the input for the skill.\n<skill name="{self.name}">\n'
            f"{self.instructions.strip()}\n</skill>"
        )
        extras = self.extra_files()
        if extras:
            block += "\n\nFiles bundled with this skill:"
            for rel, text in extras:
                block += f'\n<file path="{rel}">\n{text.rstrip()}\n</file>'
        return block

    def extra_files(self) -> list[tuple[str, str]]:
        out = []
        budget = MAX_EXTRA_BYTES
        for path in sorted(_collect_files(self.dir)):
            rel = path.relative_to(self.dir)
            if rel == Path(SKILL_FILE) or path.suffix.lower().lstrip(".") not in TEXT_EXT:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            size = len(text.encode("utf-8"))
            if size > budget:
                continue
            budget -= size
            out.append((rel.as_posix(), text))
        return out

    def to_dict(self, with_instructions: bool = False) -> dict:
        d = {"name": self.name, "description": self.description}
        if with_instructions:
            d["instructions"] = self.instructions
            d["files"] = [p.relative_to(self.dir).as_posix() for p in sorted(_collect_files(self.dir))]
        return d


def _collect_files(d: Path) -> list[Path]:
    out = []
    try:
        for e in d.iterdir():
            if e.name.startswith("."):
                continue
            if e.is_dir():
                out.extend(_collect_files(e))
            else:
                out.append(e)
    except OSError:
        pass
    return out


def sanitize_name(raw: str) -> str:
    name = "".join(c if (c.isalnum() or c in "-_") else "-" for c in raw.strip().lower())
    return re.sub(r"-{2,}", "-", name).strip("-")


def parse_skill_md(text: str) -> tuple[str | None, str, str]:
    """(name, description, body). Front matter is optional."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "", text
    name, description = None, ""
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body = "\n".join(lines[i + 1 :]).strip()
            return (name or None), description, body
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip().strip("\"'")
            if key.strip() == "name":
                name = value
            elif key.strip() == "description":
                description = value
    return None, "", text


def skill_md(name: str, description: str, instructions: str) -> str:
    desc = description.strip().replace("\n", " ")
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{instructions.strip()}\n"


def parse_invocation(text: str) -> tuple[str, str] | None:
    """``"/name rest"`` -> ``("name", "rest")``."""
    rest = text.lstrip()
    if not rest.startswith("/"):
        return None
    rest = rest[1:]
    m = re.match(r"\S+", rest)
    if not m:
        return None
    return m.group(0), rest[m.end() :].strip()


def _load_skill(d: Path) -> Skill | None:
    try:
        text = (d / SKILL_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    name, description, instructions = parse_skill_md(text)
    name = sanitize_name(name or d.name)
    return Skill(name, description, instructions, d) if name else None


def _find_skill_root(d: Path) -> Path | None:
    if (d / SKILL_FILE).is_file():
        return d
    subs = [p for p in d.iterdir() if p.is_dir() and (p / SKILL_FILE).is_file()]
    return subs[0] if len(subs) == 1 else None


class SkillStore:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or config.SKILLS_DIR
        self.use_trash = True
        self.skills: list[Skill] = []
        self.reload()

    def reload(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        found: dict[str, Skill] = {}
        for d in sorted(self.dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                s = _load_skill(d)
                if s and s.name not in found:
                    found[s.name] = s
        self.skills = sorted(found.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        name = name.lower()
        return next((s for s in self.skills if s.name == name), None)

    def install_path(self, source: Path, overwrite: bool = False) -> str:
        """Install from a folder, a SKILL.md/.md file, or a .zip."""
        self.dir.mkdir(parents=True, exist_ok=True)
        staging = self.dir / f".install-{new_id()}"
        try:
            if source.is_dir():
                root = _find_skill_root(source)
                if not root:
                    raise SkillError(f"no {SKILL_FILE} found in {source}")
            elif source.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(source) as z:
                        infos = z.infolist()
                        if len(infos) > MAX_ZIP_FILES or sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                            raise SkillError("the zip is too large for a skill")
                        for member in z.namelist():
                            target = (staging / member).resolve()
                            if not target.is_relative_to(staging.resolve()):
                                raise SkillError("the zip contains unsafe paths")
                        z.extractall(staging)
                except zipfile.BadZipFile as e:
                    raise SkillError(f"not a readable zip file ({e})") from e
                root = _find_skill_root(staging)
                if not root:
                    raise SkillError(f"the zip doesn't contain a {SKILL_FILE}")
            else:
                text = source.read_text(encoding="utf-8")
                name, _, _ = parse_skill_md(text)
                stem = source.stem
                if stem.lower() == "skill":
                    stem = source.parent.name
                root = staging / sanitize_name(name or stem)
                root.mkdir(parents=True)
                (root / SKILL_FILE).write_text(text, encoding="utf-8")
            skill = _load_skill(root)
            if not skill:
                raise SkillError(f"{SKILL_FILE} has no usable skill name")
            if not skill.instructions.strip():
                raise SkillError(f"{SKILL_FILE} has no instructions")
            target = self.dir / skill.name
            if target.exists():
                if not overwrite:
                    raise SkillExists(skill.name)
                shutil.rmtree(target)
            shutil.copytree(root, target)
            return skill.name
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            self.reload()

    def create(self, name: str, description: str, instructions: str, overwrite: bool = False) -> str:
        name = sanitize_name(name)
        if not name:
            raise SkillError("give the skill a name (letters, digits, - or _)")
        if not instructions.strip():
            raise SkillError("write the instructions the skill should follow")
        d = self.dir / name
        if (d / SKILL_FILE).exists() and not overwrite:
            raise SkillExists(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / SKILL_FILE).write_text(skill_md(name, description, instructions), encoding="utf-8")
        self.reload()
        return name

    def remove(self, name: str) -> None:
        skill = self.get(name)
        if skill:
            if self.use_trash:
                try:
                    from send2trash import send2trash

                    send2trash(str(skill.dir))
                    self.reload()
                    return
                except Exception:
                    pass
            shutil.rmtree(skill.dir, ignore_errors=True)
        self.reload()

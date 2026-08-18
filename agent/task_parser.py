"""
Task format parsing — spec section 9.

Tasks are plain Markdown with conventional headers (# Task, ## Requirements,
## Viewports, ## Acceptance Criteria). We parse leniently: headers are
matched case-insensitively and by prefix, and any section we don't
recognize is preserved under `extra_sections` rather than dropped, since
an operator may add project-specific notes the Planner should still see
even if this parser has no special field for them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskViewport:
    width: int
    height: int


@dataclass
class Task:
    task_id: str
    title: str
    description: str
    requirements: list[str] = field(default_factory=list)
    viewports: list[TaskViewport] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    extra_sections: dict[str, str] = field(default_factory=dict)
    source_path: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "requirements": self.requirements,
            "viewports": [{"width": v.width, "height": v.height} for v in self.viewports],
            "acceptance_criteria": self.acceptance_criteria,
            "extra_sections": self.extra_sections,
        }


class TaskParseError(Exception):
    pass


_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_VIEWPORT_RE = re.compile(r"(\d+)\s*[xX]\s*(\d+)")


def parse_task_file(path: str | Path) -> Task:
    path = Path(path)
    if not path.exists():
        raise TaskParseError(f"task file not found: {path}")
    text = path.read_text(encoding="utf-8")
    return parse_task_text(text, task_id=path.stem, source_path=str(path))


def parse_task_text(text: str, task_id: str, source_path: str = "") -> Task:
    headers = list(_HEADER_RE.finditer(text))
    if not headers:
        raise TaskParseError("no Markdown headers found — task file must start with '# Task' or similar")

    sections: dict[str, str] = {}
    title = ""
    for i, h in enumerate(headers):
        level = len(h.group(1))
        name = h.group(2).strip()
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end].strip()
        if level == 1 and not title:
            title = name if name.lower() != "task" else (body.splitlines()[0].strip() if body else task_id)
            if body and name.lower() == "task":
                sections["_description"] = body
            continue
        sections[name.lower()] = body

    description = sections.pop("_description", "") or sections.get("description", "")

    requirements = _extract_bullets(sections.pop("requirements", ""))
    acceptance_criteria = _extract_bullets(sections.pop("acceptance criteria", ""))
    viewport_text = sections.pop("viewports", "")
    viewports = [TaskViewport(int(w), int(h)) for w, h in _VIEWPORT_RE.findall(viewport_text)]

    if not description:
        # fall back: everything before the first ## header, minus the title line
        description = text[: headers[1].start() if len(headers) > 1 else len(text)]
        description = _HEADER_RE.sub("", description).strip()

    return Task(
        task_id=task_id,
        title=title or task_id,
        description=description,
        requirements=requirements,
        viewports=viewports,
        acceptance_criteria=acceptance_criteria,
        extra_sections=sections,
        source_path=source_path,
    )


def _extract_bullets(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            out.append(line[1:].strip())
        elif re.match(r"^\d+[.)]\s", line):
            out.append(re.sub(r"^\d+[.)]\s*", "", line))
    return out

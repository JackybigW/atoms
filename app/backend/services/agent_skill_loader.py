import re
from pathlib import Path
from typing import Optional

_DEFAULT_SKILLS_DIRS = [
    Path(__file__).parent.parent / "skills_docs",
    Path(__file__).parent.parent.parent.parent / "skills",
]

_DESCRIPTION_RE = re.compile(r"##\s+Description\s*\n(.*?)(?:\n##|\Z)", re.DOTALL)


def _extract_description(content: str) -> str:
    match = _DESCRIPTION_RE.search(content)
    if match:
        return match.group(1).strip().splitlines()[0].strip()
    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
    return lines[0] if lines else ""


class AgentSkillLoader:
    def __init__(self, skills_dirs: Optional[list[Path]] = None):
        self._dirs: list[Path] = skills_dirs if skills_dirs is not None else _DEFAULT_SKILLS_DIRS
        self._index: dict[str, Path] = {}
        self._build_index()

    def _build_index(self) -> None:
        for d in self._dirs:
            if not d.is_dir():
                continue
            for md_file in sorted(d.glob("*.md")):
                name = md_file.stem
                if name not in self._index:
                    self._index[name] = md_file

    def describe_available(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, path in self._index.items():
            try:
                content = path.read_text(encoding="utf-8")
                result[name] = _extract_description(content)
            except OSError:
                result[name] = ""
        return result

    def load(self, name: str) -> Optional[str]:
        path = self._index.get(name)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

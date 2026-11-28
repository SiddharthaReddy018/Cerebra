"""validate_skills.py – CI helper (referenced in ci.yml).

Validates every skill SKILL.md:
  - Frontmatter must be valid YAML with 'name' and 'description' keys.
  - 'name' must equal the containing folder name.
  - 'description' must be non-empty.

Exits 0 on success, 1 on any error.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml  # type: ignore[import]

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter between '---' delimiters."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm_text = "\n".join(lines[1:end])
    if not YAML_AVAILABLE:
        # Simple key: value parser (no nested structures needed here)
        result = {}
        for line in fm_text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
        return result
    return yaml.safe_load(fm_text) or {}


def validate_skills(skills_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_files = list(skills_dir.glob("*/SKILL.md"))
    if not skill_files:
        errors.append(f"No SKILL.md files found in {skills_dir}")
        return errors
    for skill_file in sorted(skill_files):
        folder_name = skill_file.parent.name
        text = skill_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if not fm:
            errors.append(f"{skill_file}: missing or invalid YAML frontmatter")
            continue
        if "name" not in fm:
            errors.append(f"{skill_file}: frontmatter missing 'name' key")
        elif fm["name"] != folder_name:
            errors.append(
                f"{skill_file}: frontmatter name={fm['name']!r} != folder={folder_name!r}"
            )
        if "description" not in fm or not str(fm.get("description", "")).strip():
            errors.append(f"{skill_file}: frontmatter missing or empty 'description'")
    return errors


if __name__ == "__main__":
    repo_root = Path(__file__).parent.parent
    skills_dir = repo_root / "skills"
    errors = validate_skills(skills_dir)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(list(skills_dir.glob('*/SKILL.md')))} skills validated.")
    sys.exit(0)

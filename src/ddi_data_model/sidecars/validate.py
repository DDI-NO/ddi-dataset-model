"""Validate generated display sidecar YAML files.

Lightweight validator; avoids YAML dependencies.

Checks:
- synonyms list exists and has 3–10 unique, non-empty strings
- semantics.level is within the closed list
- semantics.value_type is within the closed list
- sensitivity is within the closed list

Exit code:
- 0 if all checks pass
- 1 if any file fails validation
- 2 if no sidecars found
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


VALUE_TYPES = {"categorical", "numeric", "date", "text", "boolean"}
LEVELS = {"subject", "visit", "sample", "experiment"}
SENSITIVITY = {"clinical", "pii", "none"}


@dataclass
class FieldIssues:
    field_id: str
    problems: list[str]


def _iter_sidecars(sidecars_root: Path) -> list[Path]:
    return sorted(sidecars_root.glob("**/*.meta.yaml"))


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def validate_sidecar(path: Path) -> list[FieldIssues]:
    lines = path.read_text(encoding="utf-8").splitlines()
    issues: list[FieldIssues] = []

    current_field_id: str | None = None
    current_problems: list[str] = []

    in_synonyms = False
    synonyms: list[str] = []

    level: str | None = None
    value_type: str | None = None
    sensitivity: str | None = None

    def flush_field() -> None:
        nonlocal current_field_id, current_problems, in_synonyms, synonyms, level, value_type, sensitivity
        if current_field_id is None:
            return

        if not synonyms:
            current_problems.append("missing synonyms list")
        else:
            uniq: list[str] = []
            seen: set[str] = set()
            for s in synonyms:
                if not s:
                    current_problems.append("synonyms contain empty string")
                    continue
                if s in seen:
                    continue
                seen.add(s)
                uniq.append(s)
            if len(uniq) != len([s for s in synonyms if s]):
                current_problems.append("synonyms contain duplicates")

        if level is None:
            current_problems.append("missing semantics.level")
        elif level not in LEVELS:
            current_problems.append(f"invalid semantics.level: {level}")

        if value_type is None:
            current_problems.append("missing semantics.value_type")
        elif value_type not in VALUE_TYPES:
            current_problems.append(f"invalid semantics.value_type: {value_type}")

        if sensitivity is None:
            current_problems.append("missing sensitivity")
        elif sensitivity not in SENSITIVITY:
            current_problems.append(f"invalid sensitivity: {sensitivity}")

        if current_problems:
            issues.append(FieldIssues(field_id=current_field_id, problems=current_problems))

        current_field_id = None
        current_problems = []
        in_synonyms = False
        synonyms = []
        level = None
        value_type = None
        sensitivity = None

    saw_annotations = False
    for raw in lines:
        line = raw.rstrip("\n")
        if line.strip() == "display_field_annotations:":
            saw_annotations = True
            continue

        if saw_annotations and line.strip() == "{}":
            return []

        if saw_annotations and line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            flush_field()
            current_field_id = line.strip()[:-1]
            continue

        if current_field_id is None:
            continue

        if line.strip() == "synonyms:":
            in_synonyms = True
            synonyms = []
            continue

        if in_synonyms:
            if line.strip().startswith("-"):
                value = line.split("-", 1)[1].strip()
                synonyms.append(_strip_quotes(value))
                continue
            in_synonyms = False

        if line.strip().startswith("level:"):
            level = _strip_quotes(line.split(":", 1)[1].strip())
            continue
        if line.strip().startswith("value_type:"):
            value_type = _strip_quotes(line.split(":", 1)[1].strip())
            continue
        if line.strip().startswith("sensitivity:"):
            sensitivity = _strip_quotes(line.split(":", 1)[1].strip())
            continue

    flush_field()
    return issues


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    default_sidecars = repo_root / "sidecars"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sidecars",
        type=Path,
        default=default_sidecars,
        help="Root directory containing sidecars (default: sidecars/)",
    )
    args = parser.parse_args(argv)

    sidecars_root: Path = args.sidecars
    sidecars = _iter_sidecars(sidecars_root)
    if not sidecars:
        print("No sidecar YAML files found.")
        return 2

    any_fail = False
    for path in sidecars:
        field_issues = validate_sidecar(path)
        if not field_issues:
            continue
        any_fail = True
        print(f"FAIL {path}")
        for fi in field_issues:
            print(f"  - {fi.field_id}: {', '.join(fi.problems)}")

    if any_fail:
        return 1
    print(f"OK ({len(sidecars)} sidecar files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

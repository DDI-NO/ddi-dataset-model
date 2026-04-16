from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceRepo:
    name: str
    path: Path
    schemas_dir: Path


@dataclass(frozen=True)
class Config:
    sources: list[SourceRepo]


def _strip_comments(line: str) -> str:
    if "#" not in line:
        return line
    before, _hash, _after = line.partition("#")
    return before


def _parse_scalar(value: str) -> str:
    v = value.strip()
    if not v:
        return ""
    if (len(v) >= 2) and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def load_config(path: Path) -> Config:
    """Load `config/source_repos.yaml`.

    This intentionally supports only a very small YAML subset:

    sources:
      - name: ...
        path: ...
        schemas_dir: ...

    Values may be quoted or unquoted.
    """

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    lines = [_strip_comments(l).rstrip() for l in raw_lines]

    in_sources = False
    current: dict[str, str] | None = None
    items: list[dict[str, str]] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        if any(k in current for k in ("name", "path", "schemas_dir")):
            items.append(current)
        current = None

    for raw in lines:
        if not raw.strip():
            continue

        if raw.strip() == "sources:" or raw.startswith("sources:"):
            in_sources = True
            continue

        if not in_sources:
            continue

        stripped = raw.lstrip(" ")
        if stripped.startswith("-"):
            flush()
            current = {}
            rest = stripped[1:].strip()
            if rest:
                if ":" not in rest:
                    raise ValueError(f"Invalid list item line: {raw!r}")
                k, v = rest.split(":", 1)
                current[k.strip()] = _parse_scalar(v)
            continue

        if current is None:
            continue

        if ":" not in stripped:
            raise ValueError(f"Invalid mapping line: {raw!r}")
        k, v = stripped.split(":", 1)
        current[k.strip()] = _parse_scalar(v)

    flush()

    sources: list[SourceRepo] = []
    for it in items:
        name = (it.get("name") or "").strip()
        repo_path = (it.get("path") or "").strip()
        schemas_dir = (it.get("schemas_dir") or "").strip()
        if not name or not repo_path or not schemas_dir:
            raise ValueError(f"Each source must include name/path/schemas_dir; got: {it}")
        sources.append(
            SourceRepo(
                name=name,
                path=Path(repo_path).expanduser(),
                schemas_dir=Path(schemas_dir),
            )
        )

    if not sources:
        raise ValueError("No sources configured under `sources:`")

    return Config(sources=sources)

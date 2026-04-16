from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class DisplayXmlRef:
    source_name: str
    source_root: Path
    schemas_root: Path
    domain: str
    xml_name: str
    xml_path: Path


@dataclass(frozen=True)
class SourceIndex:
    by_domain_and_name: dict[tuple[str, str], DisplayXmlRef]


def _infer_domain_from_path(xml_path: Path) -> str:
    parts = list(xml_path.parts)
    try:
        idx = parts.index("schemas")
        return parts[idx + 1]
    except Exception:
        return "unknown"


def iter_display_xmls(schemas_root: Path) -> list[Path]:
    return sorted(schemas_root.glob("**/display/*.xml"))


def build_source_index(config: Config, *, config_dir: Path) -> SourceIndex:
    """Index all display XMLs from all configured sources.

    `path` in config may be relative; it is resolved relative to `config_dir`.
    """

    by_key: dict[tuple[str, str], DisplayXmlRef] = {}

    for src in config.sources:
        source_root = (config_dir / src.path).resolve() if not src.path.is_absolute() else src.path.resolve()
        schemas_root = (source_root / src.schemas_dir).resolve()

        if not schemas_root.exists():
            raise FileNotFoundError(f"schemas_dir not found for source '{src.name}': {schemas_root}")

        for xml_path in iter_display_xmls(schemas_root):
            domain = _infer_domain_from_path(xml_path)
            xml_name = xml_path.name
            key = (domain, xml_name)
            ref = DisplayXmlRef(
                source_name=src.name,
                source_root=source_root,
                schemas_root=schemas_root,
                domain=domain,
                xml_name=xml_name,
                xml_path=xml_path,
            )
            if key in by_key:
                prev = by_key[key]
                raise ValueError(
                    "Duplicate display XML detected for key "
                    f"{domain}/{xml_name}: {prev.xml_path} and {xml_path}"
                )
            by_key[key] = ref

    return SourceIndex(by_domain_and_name=by_key)

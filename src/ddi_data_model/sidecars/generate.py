"""Generate extractor-friendly YAML sidecars for XNAT display XMLs.

This is a refactor of the tooling previously hosted in `xnat-apgem-plugin/tools/`.

Key differences vs the original:
- Inputs (display XML + XSD) are read from one-or-more *source repos* configured
  via `config/source_repos.yaml`.
- Outputs are written to this repository under `sidecars/<domain>/`.

Design goals:
- No external dependencies (YAML emitted via deterministic string formatting).
- Preserve existing sidecar manual edits (description/definition/synonyms/units)
  when regenerating.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ddi_data_model.config import load_config
from ddi_data_model.sources import build_source_index


DUMP_VERSION_NAME = "dump"

TODO_DATATYPE_DESCRIPTION = "TODO: Add datatype description."
TODO_FIELD_DEFINITION = "TODO: Define semantic meaning."

VALUE_TYPES = {"categorical", "numeric", "date", "text", "boolean"}
LEVELS = {"subject", "visit", "sample", "experiment"}
SENSITIVITY = {"clinical", "pii", "none"}


@dataclass(frozen=True)
class DisplayFieldElement:
    name: str
    schema_element: str | None
    view_name: str | None
    view_column: str | None


@dataclass(frozen=True)
class DisplayField:
    id: str
    header: str | None
    data_type: str | None
    xsi_type: str | None
    elements: list[DisplayFieldElement]
    sql_content: str | None


@dataclass(frozen=True)
class ExistingSidecarEdits:
    description: str | None
    field_definitions: dict[str, str]
    field_synonyms: dict[str, list[str]]
    field_units: dict[str, str]


def _strip_xmlns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _findall(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _strip_xmlns(el.tag) == name]


def _first(iterable: Iterable[Any], default: Any = None) -> Any:
    for item in iterable:
        return item
    return default


def _text_or_none(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    txt = el.text.strip()
    return txt if txt else None


def _xml_attr(el: ET.Element, name: str) -> str | None:
    val = el.get(name)
    if val is None:
        return None
    val = val.strip()
    return val if val else None


def _infer_value_type(
    *,
    field_id: str,
    data_type: str | None,
    derivation_kind: str,
    schema_element_hint: str | None,
) -> str:
    if derivation_kind == "sql_case":
        return "categorical"

    if data_type is not None:
        dt = data_type.strip().lower()
        if dt in {"integer", "int", "float", "double", "numeric", "number"}:
            return "numeric"
        if dt in {"date", "datetime"}:
            return "date"
        if dt in {"boolean", "bool"}:
            return "boolean"
        return "text"

    fid = field_id.strip()
    fid_upper = fid.upper()
    if fid_upper.endswith("DATE") or fid_upper.endswith("_DATE") or fid_upper in {"DATE", "DOB"}:
        return "date"
    if schema_element_hint:
        hint = schema_element_hint.strip().lower()
        if hint.endswith("/date") or hint.endswith(".date") or hint.endswith("_date"):
            return "date"

    numeric_markers = (
        "AGE",
        "YEARS",
        "YEAR",
        "VOL",
        "VOLUME",
        "MEAN",
        "RATIO",
        "SCORE",
        "TOTAL",
        "CV",
        "ICV",
        "NVOXELS",
        "VOXELS",
        "MMSE",
        "T_SCORE",
    )
    if any(marker in fid_upper for marker in numeric_markers):
        return "numeric"

    return "text"


def _canonical_name_from_id(field_id: str) -> str:
    s = field_id.strip()
    s = s.lstrip("_")
    s = re.sub(r"[^A-Za-z0-9_]+", "_", s)
    s = re.sub(r"__+", "_", s)
    return s.lower() if s else field_id.lower()


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_quote(str(value))


def _yaml_block_scalar_lines(*, indent: int, key: str, text: str) -> list[str]:
    if "\n" not in text and "\r" not in text:
        return [" " * indent + f"{key}: " + _yaml_scalar(text)]

    if text == "":
        return [" " * indent + f'{key}: ""']

    out: list[str] = []
    out.append(" " * indent + f"{key}: |-")
    content_indent = " " * (indent + 2)
    for line in text.splitlines():
        out.append(content_indent + line)
    return out


def _yaml_unquote_scalar(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == '"':
        out_chars: list[str] = []
        i = 1
        while i < len(token):
            ch = token[i]
            if ch == '"':
                break
            if ch == "\\" and i + 1 < len(token):
                nxt = token[i + 1]
                if nxt in {'"', "\\"}:
                    out_chars.append(nxt)
                    i += 2
                    continue
            out_chars.append(ch)
            i += 1
        return "".join(out_chars)
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1]
    return token


def _parse_yaml_value_from_line(lines: list[str], i: int) -> tuple[str | None, int]:
    line = lines[i]
    indent = len(line) - len(line.lstrip(" "))
    if ":" not in line:
        return None, i + 1
    _k, rest = line.split(":", 1)
    rest = rest.strip()
    if rest == "null":
        return None, i + 1

    if rest in {"|", "|-", ">", ">-"}:
        block_indent = indent + 2
        content: list[str] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.startswith(" " * block_indent):
                content.append(nxt[block_indent:])
                j += 1
                continue
            break
        return "\n".join(content), j

    if rest == '""':
        return "", i + 1

    return _yaml_unquote_scalar(rest), i + 1


FIELD_START_RE = re.compile(r"^  (?P<id>[^\s:#]+):\s*$")


def _parse_field_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    blocks: dict[str, tuple[int, int]] = {}
    start: int | None = None
    fid: str | None = None
    in_annotations = False

    for idx, line in enumerate(lines):
        if line.strip() == "display_field_annotations:":
            in_annotations = True
            continue
        if not in_annotations:
            continue
        if line.strip() == "{}":
            return {}

        m = FIELD_START_RE.match(line)
        if m:
            if start is not None and fid is not None:
                blocks[fid] = (start, idx)
            fid = m.group("id")
            start = idx

    if start is not None and fid is not None:
        blocks[fid] = (start, len(lines))

    return blocks


def _extract_existing_description(lines: list[str]) -> str | None:
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            value, _ = _parse_yaml_value_from_line(lines, i)
            return value
    return None


def _extract_existing_definition(block_lines: list[str]) -> str | None:
    for i, line in enumerate(block_lines):
        if line.strip().startswith("definition:"):
            value, _ = _parse_yaml_value_from_line(block_lines, i)
            return value
    return None


def _extract_existing_synonyms(block_lines: list[str]) -> list[str] | None:
    for i, line in enumerate(block_lines):
        if line.strip() == "synonyms:":
            indent = len(line) - len(line.lstrip(" "))
            list_indent = indent + 2
            out: list[str] = []
            j = i + 1
            while j < len(block_lines):
                nxt = block_lines[j]
                if len(nxt) - len(nxt.lstrip(" ")) < list_indent:
                    break
                if nxt.strip().startswith("-"):
                    token = nxt.split("-", 1)[1].strip()
                    if token == "null":
                        j += 1
                        continue
                    out.append(_yaml_unquote_scalar(token))
                    j += 1
                    continue
                break
            return out
    return None


def _extract_existing_units(block_lines: list[str]) -> str | None:
    for i, line in enumerate(block_lines):
        if line.strip().startswith("units:"):
            value, _ = _parse_yaml_value_from_line(block_lines, i)
            return value
    return None


def load_existing_sidecar_edits(path: Path) -> ExistingSidecarEdits:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ExistingSidecarEdits(description=None, field_definitions={}, field_synonyms={}, field_units={})

    description = _extract_existing_description(lines)
    blocks = _parse_field_blocks(lines)

    definitions: dict[str, str] = {}
    synonyms: dict[str, list[str]] = {}
    units: dict[str, str] = {}
    for fid, (s, e) in blocks.items():
        block = lines[s:e]
        d = _extract_existing_definition(block)
        if d is not None:
            definitions[fid] = d
        syn = _extract_existing_synonyms(block)
        if syn:
            synonyms[fid] = syn
        u = _extract_existing_units(block)
        if u is not None:
            units[fid] = u

    return ExistingSidecarEdits(
        description=description,
        field_definitions=definitions,
        field_synonyms=synonyms,
        field_units=units,
    )


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        norm = item.strip()
        if not norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _generate_synonyms(field_id: str, header: str | None, canonical_name: str) -> list[str]:
    candidates: list[str] = []
    if header:
        candidates.extend(
            [
                header,
                header.lower(),
                header.replace("_", " "),
                header.replace("_", " ").lower(),
            ]
        )
    candidates.extend([field_id, field_id.lower(), canonical_name])
    candidates = _dedupe_preserve_order(candidates)
    if len(candidates) < 3:
        canon_spaced = canonical_name.replace("_", " ")
        candidates.extend([canon_spaced, canon_spaced.title(), canonical_name.upper()])
    candidates = _dedupe_preserve_order(candidates)
    return candidates[:10]


CASE_WHEN_RE = re.compile(
    r"WHEN\s+(?P<when>.+?)\s+THEN\s+(?P<then>(?:'[^']*')|(?:\"[^\"]*\")|(?:[^\s]+))",
    flags=re.IGNORECASE | re.DOTALL,
)
CASE_ELSE_RE = re.compile(r"ELSE\s+(?P<else>(?:'[^']*')|(?:\"[^\"]*\")|(?:[^\s]+))", flags=re.IGNORECASE)


def _unquote_sql_literal(token: str) -> str | None:
    token = token.strip()
    if token.upper() == "NULL":
        return None
    if len(token) >= 2 and ((token[0] == "'" and token[-1] == "'") or (token[0] == '"' and token[-1] == '"')):
        return token[1:-1]
    return token


def _parse_sql_case_mapping(sql: str) -> tuple[dict[str, str], str | None]:
    mapping: dict[str, str] = {}
    for m in CASE_WHEN_RE.finditer(sql):
        when_expr = m.group("when").strip()
        then_token = m.group("then").strip()
        then_val = _unquote_sql_literal(then_token)
        if then_val is None:
            continue
        mapping[when_expr] = then_val
    else_match = CASE_ELSE_RE.search(sql)
    else_value = _unquote_sql_literal(else_match.group("else")) if else_match else None
    return mapping, else_value


def _derivation_kind(sql: str | None) -> str:
    if not sql:
        return "none"
    if re.search(r"\bCASE\b", sql, flags=re.IGNORECASE):
        return "sql_case"
    return "sql"


def _extract_display_fields(root: ET.Element) -> dict[str, DisplayField]:
    out: dict[str, DisplayField] = {}
    for df in _findall(root, "DisplayField"):
        field_id = _xml_attr(df, "id")
        if not field_id:
            continue
        header = _xml_attr(df, "header")
        data_type = _xml_attr(df, "data-type")
        xsi_type = df.get("{http://www.w3.org/2001/XMLSchema-instance}type") or _xml_attr(df, "xsi:type")

        elements: list[DisplayFieldElement] = []
        for dfe in [el for el in df if _strip_xmlns(el.tag) == "DisplayFieldElement"]:
            elements.append(
                DisplayFieldElement(
                    name=_xml_attr(dfe, "name") or "",
                    schema_element=_xml_attr(dfe, "schema-element"),
                    view_name=_xml_attr(dfe, "viewName"),
                    view_column=_xml_attr(dfe, "viewColumn"),
                )
            )

        sql_el = _first(
            [
                el
                for el in df
                if _strip_xmlns(el.tag) == "Content" and (_xml_attr(el, "type") or "").lower() == "sql"
            ],
            None,
        )
        sql_content = _text_or_none(sql_el)
        out[field_id] = DisplayField(
            id=field_id,
            header=header,
            data_type=data_type,
            xsi_type=xsi_type,
            elements=elements,
            sql_content=sql_content,
        )
    return out


def _extract_dump_field_refs(root: ET.Element) -> list[str]:
    dump_versions = [
        el
        for el in _findall(root, "DisplayVersion")
        if (_xml_attr(el, "versionName") or "") == DUMP_VERSION_NAME
    ]
    if not dump_versions:
        return []

    refs: list[str] = []
    for dv in dump_versions:
        for ref in [el for el in dv if _strip_xmlns(el.tag) == "DisplayFieldRef"]:
            rid = _xml_attr(ref, "id")
            if rid:
                refs.append(rid)
    return _dedupe_preserve_order(refs)


def _display_source_schema_element(root: ET.Element) -> str | None:
    for el in root.iter():
        if _strip_xmlns(el.tag) == "Displays":
            return _xml_attr(el, "schema-element")
    return None


def _schema_type_name(schema_element: str | None) -> str | None:
    if not schema_element:
        return None
    if ":" in schema_element:
        return schema_element.split(":", 1)[1].strip() or None
    return schema_element.strip() or None


def _find_schema_file(xml_path: Path, *, domain: str) -> Path | None:
    display_dir = xml_path.parent
    schema_dir = display_dir.parent
    if not schema_dir.is_dir():
        return None

    preferred = schema_dir / f"{domain}.xsd"
    if preferred.exists():
        return preferred

    xsds = sorted(schema_dir.glob("*.xsd"))
    if len(xsds) == 1:
        return xsds[0]
    return xsds[0] if xsds else None


def _extract_schema_description(schema_path: Path, *, schema_element: str | None) -> str | None:
    try:
        tree = ET.parse(schema_path)
    except ET.ParseError:
        return None
    root = tree.getroot()
    type_name = _schema_type_name(schema_element)

    complex_type: ET.Element | None = None
    if type_name:
        for ct in _findall(root, "complexType"):
            if (_xml_attr(ct, "name") or "") == type_name:
                complex_type = ct
                break

    if complex_type is None:
        complex_type = _first(_findall(root, "complexType"), None)
    if complex_type is None:
        return None

    docs: list[str] = []
    for child in list(complex_type):
        if _strip_xmlns(child.tag) != "annotation":
            continue
        for doc in list(child):
            if _strip_xmlns(doc.tag) != "documentation":
                continue
            text = _text_or_none(doc)
            if text:
                docs.append(text)
    if not docs:
        return None
    return " ".join(docs).strip() or None


def _is_core_field(field: DisplayField, *, large_mode: bool) -> bool:
    if not large_mode:
        return True
    if field.xsi_type and field.xsi_type.strip() == "SubQueryField":
        return False

    sql = field.sql_content
    if not sql:
        return True

    if _derivation_kind(sql) == "sql_case":
        return False

    placeholders = set(re.findall(r"@[A-Za-z0-9_]+", sql))
    if len(placeholders) >= 2:
        return False
    return True


def _yaml_lines_for_field(
    field_id: str,
    field: DisplayField | None,
    *,
    domain: str,
    included_via: str | None,
    large_mode: bool,
    existing_definition: str | None = None,
    existing_synonyms: list[str] | None = None,
    existing_units: str | None = None,
) -> list[str]:
    header = field.header if field else None
    canonical = _canonical_name_from_id(field_id)

    sql = field.sql_content if field else None
    deriv_kind = _derivation_kind(sql)

    mapping: dict[str, str] = {}
    else_val: str | None = None
    if deriv_kind == "sql_case" and sql:
        mapping, else_val = _parse_sql_case_mapping(sql)

    schema_element_first = None
    display_field_elements: dict[str, str] = {}
    if field:
        for el in field.elements:
            key = el.name or ""
            if el.schema_element:
                display_field_elements[key] = el.schema_element
                if schema_element_first is None:
                    schema_element_first = el.schema_element
            elif el.view_name and el.view_column:
                display_field_elements[key] = f"view:{el.view_name}.{el.view_column}"
            else:
                display_field_elements[key] = "null"

    value_type = _infer_value_type(
        field_id=field_id,
        data_type=(field.data_type if field else None),
        derivation_kind=deriv_kind,
        schema_element_hint=schema_element_first,
    )
    if value_type not in VALUE_TYPES:
        value_type = "text"

    generated_synonyms = _generate_synonyms(field_id, header, canonical)
    synonyms = existing_synonyms if existing_synonyms else generated_synonyms
    synonyms = _dedupe_preserve_order(list(synonyms))

    definition = TODO_FIELD_DEFINITION
    if deriv_kind == "sql_case" and field and (field.id.lower().endswith("_text") or "text" in (field.header or "").lower()):
        definition = "Label representation derived from coded values."
    if definition == TODO_FIELD_DEFINITION and existing_definition and existing_definition != TODO_FIELD_DEFINITION:
        definition = existing_definition

    allowed_values: list[str] = []
    if deriv_kind == "sql_case" and mapping:
        allowed_values = _dedupe_preserve_order(list(mapping.values()) + ([else_val] if else_val is not None else []))

    lines: list[str] = []
    lines.append(f"  {field_id}:")
    lines.append("    export:")
    lines.append("      column: null")
    if included_via is None:
        lines.append("      included_via_display_version: null")
    else:
        lines.append(f"      included_via_display_version: {_yaml_scalar(included_via)}")
    lines.append(f"    label: {_yaml_scalar(header) if header is not None else 'null'}")
    lines.extend(_yaml_block_scalar_lines(indent=4, key="definition", text=definition))
    lines.append("    source:")
    lines.append(f"      schema_element: {_yaml_scalar(schema_element_first) if schema_element_first else 'null'}")
    lines.append("      display_field_elements:")
    if display_field_elements:
        for name, val in display_field_elements.items():
            lines.append(f"        {name}: {_yaml_scalar(val) if val != 'null' else 'null'}")
    else:
        lines.append("        {}")

    lines.append("    derivation:")
    lines.append(f"      kind: {_yaml_scalar(deriv_kind)}")
    if deriv_kind == "none":
        lines.append("      input: null")
    else:
        lines.extend(_yaml_block_scalar_lines(indent=6, key="input", text=(sql or "")))

    if deriv_kind == "sql_case":
        lines.append("      mapping:")
        if mapping:
            for k, v in mapping.items():
                lines.append(f"        {_yaml_quote(k)}: {_yaml_scalar(v)}")
        else:
            lines.append("        {}")
        lines.append(f"      else: {_yaml_scalar(else_val)}")
    else:
        lines.append("      mapping: {}")
        lines.append("      else: null")
    lines.append("      depends_on_display_fields: []")

    lines.append("    semantics:")
    lines.append(f"      level: {_yaml_scalar('visit')}")
    lines.append(f"      domain: {_yaml_scalar(domain)}")
    lines.append(f"      value_type: {_yaml_scalar(value_type)}")
    if existing_units is None:
        lines.append("      units: null")
    else:
        lines.append(f"      units: {_yaml_scalar(existing_units)}")
    lines.append(f"      preferred_representation: {_yaml_scalar('raw')}")

    lines.append("    resolution:")
    lines.append(f"      canonical_name: {_yaml_scalar(canonical)}")
    lines.append("      synonyms:")
    for s in synonyms:
        lines.append(f"        - {_yaml_scalar(s)}")

    lines.append("    allowed_values:")
    if allowed_values:
        for v in allowed_values:
            lines.append(f"      - {_yaml_scalar(v)}")
    else:
        lines.append("      []")

    lines.append(f"    sensitivity: {_yaml_scalar('none')}")
    lines.append("    quality:")
    notes: str | None = None
    if field is None:
        notes = "Referenced by dump DisplayVersion but DisplayField definition not found in XML."
    elif large_mode and not _is_core_field(field, large_mode=True):
        notes = "Omitted by large-display core-field filter."
    lines.append(f"      notes: {_yaml_scalar(notes)}")

    return lines


def generate_sidecar_text_for_xml(
    xml_path: Path,
    *,
    domain: str,
    sidecar_path: Path,
    large_threshold: int,
    preserve_manual: bool = True,
) -> str | None:
    existing: ExistingSidecarEdits | None = None
    if preserve_manual and sidecar_path.exists():
        existing = load_existing_sidecar_edits(sidecar_path)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    schema_element = _display_source_schema_element(root)
    schema_path = _find_schema_file(xml_path, domain=domain)
    schema_description = None
    if schema_path:
        schema_description = _extract_schema_description(schema_path, schema_element=schema_element)

    dump_ids = _extract_dump_field_refs(root)
    has_dump = len(dump_ids) > 0
    if not has_dump:
        return None
    large_mode = len(dump_ids) >= large_threshold

    fields = _extract_display_fields(root)

    if large_mode and has_dump:
        filtered: list[str] = []
        for fid in dump_ids:
            f = fields.get(fid)
            if f is None:
                filtered.append(fid)
                continue
            if _is_core_field(f, large_mode=True):
                filtered.append(fid)
        dump_ids = filtered

    out_lines: list[str] = []
    out_lines.append(f"display: {_yaml_scalar(xml_path.name)}")

    description_text = schema_description or TODO_DATATYPE_DESCRIPTION
    if (
        description_text == TODO_DATATYPE_DESCRIPTION
        and existing is not None
        and existing.description
        and existing.description != TODO_DATATYPE_DESCRIPTION
    ):
        description_text = existing.description
    out_lines.extend(_yaml_block_scalar_lines(indent=0, key="description", text=description_text))
    out_lines.append("")
    out_lines.append("display_versions:")
    desc = 'Fields referenced by DisplayVersion versionName="dump".'
    if large_mode:
        desc = 'Fields referenced by DisplayVersion versionName="dump" (core-only for large displays).'
    out_lines.append("  dump:")
    out_lines.append(f"    description: {_yaml_scalar(desc)}")
    if schema_element:
        out_lines.append("")
        out_lines.append("# source_schema_element: " + schema_element)

    out_lines.append("")
    out_lines.append("display_field_annotations:")
    if not dump_ids:
        out_lines.append("  {}")
    else:
        for fid in dump_ids:
            f = fields.get(fid)
            existing_definition = existing.field_definitions.get(fid) if existing is not None else None
            existing_synonyms = existing.field_synonyms.get(fid) if existing is not None else None
            existing_units = existing.field_units.get(fid) if existing is not None else None
            out_lines.extend(
                _yaml_lines_for_field(
                    fid,
                    f,
                    domain=domain,
                    included_via=DUMP_VERSION_NAME,
                    large_mode=large_mode,
                    existing_definition=existing_definition,
                    existing_synonyms=existing_synonyms,
                    existing_units=existing_units,
                )
            )

    return "\n".join(out_lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    default_config = repo_root / "config" / "source_repos.yaml"
    default_out = repo_root / "sidecars"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help="Path to config/source_repos.yaml",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_out,
        help="Output directory for sidecars (default: sidecars/)",
    )
    parser.add_argument(
        "--large-threshold",
        type=int,
        default=250,
        help="If dump field count >= threshold, apply core-field filter",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report only; do not write files",
    )
    parser.add_argument(
        "--no-preserve-manual",
        action="store_true",
        help="Do not preserve manual edits from existing sidecars; fully overwrite",
    )
    args = parser.parse_args(argv)

    config_path: Path = args.config
    out_dir: Path = args.out

    config = load_config(config_path)
    index = build_source_index(config, config_dir=config_path.parent)

    refs = sorted(index.by_domain_and_name.values(), key=lambda r: (r.domain, r.xml_name))
    if not refs:
        print("No display XML files found from configured sources.", file=sys.stderr)
        return 2

    wrote = 0
    skipped_no_dump = 0
    for ref in refs:
        sidecar_path = out_dir / ref.domain / (Path(ref.xml_name).stem + ".meta.yaml")
        text = generate_sidecar_text_for_xml(
            ref.xml_path,
            domain=ref.domain,
            sidecar_path=sidecar_path,
            large_threshold=args.large_threshold,
            preserve_manual=(not args.no_preserve_manual),
        )
        if text is None:
            skipped_no_dump += 1
            if args.dry_run:
                print(f"[dry-run] skipped (no dump DisplayVersion): {ref.xml_path}")
            continue
        if args.dry_run:
            rel = sidecar_path
            print(f"[dry-run] {ref.xml_path} -> {rel} ({len(text)} bytes)")
            continue
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(text, encoding="utf-8")
        wrote += 1

    if args.dry_run:
        print(f"[dry-run] skipped {skipped_no_dump} XML files with no dump DisplayVersion.")
    else:
        print(f"Wrote {wrote} sidecar YAML files.")
        print(f"Skipped {skipped_no_dump} XML files with no dump DisplayVersion.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

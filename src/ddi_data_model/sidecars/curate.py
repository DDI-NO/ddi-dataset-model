"""Repo-wide curation pass for generated display sidecar YAML files.

Edits existing `*.meta.yaml` sidecars in-place WITHOUT changing:
- top-level keys
- per-field keys/structure

It only updates values for a limited set of "important" fields:
- IDs/labels (LABEL, SUBJECT_ID, EXPT_ID, VISIT_ID)
- dates (DATE, *_DATE)
- ages (AGE, *_AGE)
- staging/diagnosis codes (fields containing STAG/STAGE/DIAG/DIAGNOSIS)

This is a refactor of the tooling previously hosted in `xnat-apgem-plugin/tools/`.

Key differences vs the original:
- Sidecars live under `sidecars/<domain>/*.meta.yaml`.
- XSD type inference is performed against the configured source repos
  (via `config/source_repos.yaml`).

No external dependencies.
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ddi_data_model.config import load_config
from ddi_data_model.sources import build_source_index


SIDE_CAR_GLOB = "**/*.meta.yaml"

SKIP_FILENAMES = {
    "amypet_AmyloidPETReportData_display.meta.yaml",
    "diag_DiagnosisData_display.meta.yaml",
}

VALUE_TYPES = {"categorical", "numeric", "date", "text", "boolean"}


@dataclass(frozen=True)
class XsdIndex:
    # Map: typeName -> elementName -> xsType (e.g. xs:date)
    types: dict[str, dict[str, str]]


def iter_sidecars(sidecars_root: Path) -> list[Path]:
    return sorted(sidecars_root.glob(SIDE_CAR_GLOB))


def domain_from_sidecar_path(path: Path) -> str:
    # sidecars/<domain>/<file>.meta.yaml
    return path.parent.name


def load_xsd_index_from_xml(xml_path: Path, domain: str) -> XsdIndex | None:
    # xml_path is .../schemas/<domain>/display/<file>.xml
    schema_dir = xml_path.parent.parent
    xsd_path = schema_dir / f"{domain}.xsd"
    if not xsd_path.exists():
        xsds = sorted(schema_dir.glob("*.xsd"))
        xsd_path = xsds[0] if xsds else xsd_path

    if not xsd_path.exists():
        return None

    try:
        tree = ET.parse(xsd_path)
    except Exception:
        return None

    root = tree.getroot()
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}

    types: dict[str, dict[str, str]] = {}
    for ctype in root.findall(".//xs:complexType", ns):
        type_name = ctype.get("name")
        if not type_name:
            continue
        element_map: dict[str, str] = {}
        for el in ctype.findall(".//xs:element", ns):
            el_name = el.get("name")
            el_type = el.get("type")
            if not el_name:
                continue
            if el_type:
                element_map[el_name] = el_type
            else:
                restriction = el.find(".//xs:restriction", ns)
                if restriction is not None and restriction.get("base"):
                    element_map[el_name] = restriction.get("base")  # type: ignore[arg-type]
                else:
                    element_map[el_name] = "xs:string"
        if element_map:
            types[type_name] = element_map

    return XsdIndex(types=types)


def schema_element_to_type_and_field(schema_element: str) -> tuple[str | None, str | None]:
    if not schema_element or ":" not in schema_element:
        return None, None

    after_prefix = schema_element.split(":", 1)[1]

    type_name = after_prefix
    sep = None
    for candidate in ("/", "."):
        if candidate in after_prefix:
            idx = after_prefix.index(candidate)
            type_name = after_prefix[:idx]
            sep = candidate
            break

    if sep is None:
        return type_name.strip(), None

    remainder = after_prefix.split(sep, 1)[1]
    field = remainder.split("/")[-1].split(".")[-1].strip()
    return type_name.strip(), field or None


def xs_type_to_value_type(xs_type: str) -> str:
    t = (xs_type or "").strip().lower()
    if t.endswith(":date") or t == "xs:date":
        return "date"
    if t.endswith(":datetime") or t == "xs:datetime":
        return "date"
    if t.endswith(":boolean") or t == "xs:boolean":
        return "boolean"
    if t.endswith(":integer") or t == "xs:integer":
        return "numeric"
    if t.endswith(":int") or t == "xs:int":
        return "numeric"
    if t.endswith(":decimal") or t == "xs:decimal":
        return "numeric"
    if t.endswith(":float") or t == "xs:float":
        return "numeric"
    if t.endswith(":double") or t == "xs:double":
        return "numeric"
    return "text"


IMPORTANT_ID_RE = re.compile(r"^(LABEL|SUBJECT_ID|EXPT_ID|VISIT_ID|DATE|AGE)$")
IMPORTANT_DATE_RE = re.compile(r"(^|_)(DATE)(_|$)")
IMPORTANT_AGE_RE = re.compile(r"(^|_)(AGE)(_|$)")
IMPORTANT_STAGING_DIAG_RE = re.compile(r"(STAG|STAGE|DIAG|DIAGNOSIS)", flags=re.IGNORECASE)


def is_important_field(field_id: str) -> bool:
    fid = field_id.strip()
    if IMPORTANT_ID_RE.match(fid):
        return True
    if IMPORTANT_DATE_RE.search(fid.upper()):
        return True
    if IMPORTANT_AGE_RE.search(fid.upper()):
        return True
    if IMPORTANT_STAGING_DIAG_RE.search(fid):
        return True
    return False


def curated_definition(field_id: str, label: str | None) -> str | None:
    fid = field_id.upper()

    if fid == "LABEL":
        return "Human-readable label for this record/assessment (falls back to the internal ID when missing)."
    if fid == "SUBJECT_ID":
        return "Participant (subject) identifier in XNAT."
    if fid in {"EXPT_ID", "ID"}:
        return "Unique XNAT assessor/experiment identifier for this record."
    if fid == "VISIT_ID":
        return "Visit identifier/timepoint label associated with this assessment."
    if fid == "DATE":
        return "Assessment/acquisition date for this record (visit date)."
    if fid == "AGE":
        return "Participant age (years) at the record date; derived from the record date and DOB/YOB."

    if fid.endswith("_DATE") or ("_DATE_" in fid) or fid.endswith("DATE"):
        if label:
            return f"{label} (date)."
        return "Relevant date for this record (see label for context)."

    if fid.endswith("_AGE") or ("_AGE_" in fid) or fid.endswith("AGE"):
        if label:
            return f"{label} (age in years)."
        return "Relevant age (years) for this record (see label for context)."

    if "STAG" in fid or "STAGE" in fid:
        if fid.endswith("_TEXT") or fid.endswith("_LABEL"):
            return "Clinical staging label derived from a coded staging field."
        return "Clinical staging code (integer/categorical)."

    if fid.startswith("DIAG") or "DIAG" in fid or "DIAGNOSIS" in fid:
        if fid.endswith("_TEXT") or fid.endswith("_LABEL"):
            return "Etiological diagnosis label derived from a coded diagnosis field."
        return "Etiological diagnosis code (integer/categorical)."

    return None


def curated_synonyms(field_id: str, label: str | None) -> list[str] | None:
    fid = field_id.strip()
    u = fid.upper()

    if u == "LABEL":
        return ["Label", "assessor_label", "session_label", "record_label", "display_label"]
    if u == "SUBJECT_ID":
        return ["Subject", "subject_id", "participant_id", "xnat_subject_id", "participant"]
    if u in {"EXPT_ID", "ID"}:
        return ["ID", "assessor_id", "experiment_id", "session_id", "record_id"]
    if u == "VISIT_ID":
        return ["Visit_ID", "visit_id", "visit", "timepoint", "encounter_id"]
    if u == "DATE":
        return ["Date", "visit_date", "assessment_date", "session_date", "acquisition_date"]
    if u == "AGE":
        return ["Age", "age_years", "age_at_visit", "age_at_assessment", "age_at_session"]

    if u.endswith("_DATE") or ("_DATE_" in u) or u.endswith("DATE"):
        base = fid.lower()
        spaced = base.replace("_", " ")
        out = []
        if label:
            out.append(label)
        out.extend([base, spaced, "assessment_date", "date"])
        return dedupe_limit(out, 3, 8)

    if u.endswith("_AGE") or ("_AGE_" in u) or u.endswith("AGE"):
        base = fid.lower()
        spaced = base.replace("_", " ")
        out = []
        if label:
            out.append(label)
        out.extend([base, spaced, "age_years", "age"])
        return dedupe_limit(out, 3, 8)

    if IMPORTANT_STAGING_DIAG_RE.search(fid):
        base = fid.lower()
        spaced = base.replace("_", " ")
        out = []
        if label:
            out.append(label)
        out.extend([base, spaced])
        if "stag" in base or "stage" in base:
            out.extend(["clinical_stage", "staging", "stage"])
        if "diag" in base or "diagnosis" in base:
            out.extend(["diagnosis", "etiology", "etiologic_diagnosis"])
        return dedupe_limit(out, 3, 8)

    return None


def dedupe_limit(items: list[str], min_n: int, max_n: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    if len(out) < min_n and out:
        base = out[0]
        out.extend([base.lower(), base.upper(), base.replace("_", " ")])
        out = dedupe_limit(out, min_n, max_n)
    return out[:max_n]


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


FIELD_START_RE = re.compile(r"^  (?P<id>[^\s:#]+):\s*$")


def parse_field_blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    blocks: dict[str, tuple[int, int]] = {}
    start = None
    fid = None
    in_annotations = False

    for i, line in enumerate(lines):
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
                blocks[fid] = (start, i)
            fid = m.group("id")
            start = i

    if start is not None and fid is not None:
        blocks[fid] = (start, len(lines))

    return blocks


def get_scalar(lines: list[str], key: str) -> str | None:
    for line in lines:
        if line.strip().startswith(key + ":"):
            value = line.split(":", 1)[1].strip()
            if value == "null":
                return None
            return value.strip('"')
    return None


def get_display_xml_name(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith("display:"):
            value = line.split(":", 1)[1].strip()
            return value.strip('"').strip("'")
    return None


CASE_KEY_NUM_RE = re.compile(r"@Field\d+\s*=\s*(?P<num>\d+)")


def infer_codes_from_text_field(block_lines: list[str]) -> list[str]:
    codes: list[int] = []
    in_mapping = False
    for raw in block_lines:
        line = raw.rstrip("\n")
        if line.strip() == "mapping:":
            in_mapping = True
            continue
        if in_mapping:
            if line.startswith("      ") and not line.startswith("        "):
                break
            m = CASE_KEY_NUM_RE.search(line)
            if m:
                try:
                    codes.append(int(m.group("num")))
                except Exception:
                    pass
    uniq = sorted({c for c in codes})
    return [str(c) for c in uniq]


def replace_block_section(
    block_lines: list[str],
    *,
    new_definition: str | None,
    new_value_type: str | None,
    new_synonyms: list[str] | None,
    new_allowed_values: list[str] | None,
) -> list[str]:
    out: list[str] = []

    i = 0
    while i < len(block_lines):
        line = block_lines[i]

        if new_definition is not None and line.strip().startswith("definition:"):
            out.append("    definition: \"" + new_definition.replace("\\", "\\\\").replace('"', "\\\"") + "\"")
            i += 1
            continue

        if new_value_type is not None and line.strip().startswith("value_type:"):
            out.append("      value_type: \"" + new_value_type + "\"")
            i += 1
            continue

        if new_synonyms is not None and line.strip() == "synonyms:":
            out.append(line)
            i += 1
            while i < len(block_lines) and block_lines[i].strip().startswith("-"):
                i += 1
            for s in dedupe_preserve_order(new_synonyms):
                escaped = s.replace("\\", "\\\\").replace('"', "\\\"")
                out.append(f"        - \"{escaped}\"")
            continue

        if new_allowed_values is not None and line.strip() == "allowed_values:":
            out.append(line)
            i += 1
            while i < len(block_lines) and (block_lines[i].strip() == "[]" or block_lines[i].strip().startswith("-")):
                i += 1
            if not new_allowed_values:
                out.append("      []")
            else:
                for v in dedupe_preserve_order(new_allowed_values):
                    escaped = v.replace("\\", "\\\\").replace('"', "\\\"")
                    out.append(f"      - \"{escaped}\"")
            continue

        out.append(line)
        i += 1

    return out


def curate_sidecar_text(
    *,
    lines: list[str],
    domain: str,
    xsd_index: XsdIndex | None,
    include_skipped: bool,
    filename: str,
) -> tuple[bool, list[str]]:
    if (not include_skipped) and filename in SKIP_FILENAMES:
        return False, lines

    blocks = parse_field_blocks(lines)
    if not blocks:
        return False, lines

    inferred_codes_by_prefix: dict[str, list[str]] = {}
    for fid, (s, e) in blocks.items():
        if not (fid.upper().endswith("_TEXT") or fid.endswith("_text")):
            continue
        block_lines = lines[s:e]
        if not any(l.strip() == 'kind: "sql_case"' for l in block_lines):
            continue
        codes = infer_codes_from_text_field(block_lines)
        if not codes:
            continue
        prefix = fid[:-5]
        inferred_codes_by_prefix[prefix] = codes

    changed = False
    new_lines = list(lines)

    for fid, (start, end) in sorted(blocks.items(), key=lambda kv: kv[1][0], reverse=True):
        if not is_important_field(fid):
            continue

        block = new_lines[start:end]
        current_def = get_scalar(block, "definition")
        label = get_scalar(block, "label")
        schema_el = get_scalar(block, "schema_element")

        new_value_type = None
        if schema_el and xsd_index is not None:
            type_name, field_name = schema_element_to_type_and_field(schema_el)
            if type_name and field_name and type_name in xsd_index.types:
                xs_type = xsd_index.types[type_name].get(field_name)
                if xs_type:
                    new_value_type = xs_type_to_value_type(xs_type)

        if IMPORTANT_STAGING_DIAG_RE.search(fid):
            if not (fid.upper().endswith("_TEXT") or fid.endswith("_text")):
                new_value_type = "categorical"

        if fid.upper() == "DATE" or fid.upper().endswith("_DATE"):
            new_value_type = "date"
        if fid.upper() == "AGE" or fid.upper().endswith("_AGE"):
            new_value_type = "numeric"

        if new_value_type is not None and new_value_type not in VALUE_TYPES:
            new_value_type = None

        new_definition = None
        if current_def and "TODO: Define semantic meaning." in current_def:
            new_definition = curated_definition(fid, label)

        new_synonyms = curated_synonyms(fid, label)

        new_allowed_values = None
        if IMPORTANT_STAGING_DIAG_RE.search(fid) and not (fid.upper().endswith("_TEXT") or fid.endswith("_text")):
            codes = inferred_codes_by_prefix.get(fid)
            if codes:
                new_allowed_values = codes

        updated_block = replace_block_section(
            block,
            new_definition=new_definition,
            new_value_type=new_value_type,
            new_synonyms=new_synonyms,
            new_allowed_values=new_allowed_values,
        )

        if updated_block != block:
            new_lines[start:end] = updated_block
            changed = True

    return changed, new_lines


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    default_config = repo_root / "config" / "source_repos.yaml"
    default_sidecars = repo_root / "sidecars"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help="Path to config/source_repos.yaml",
    )
    parser.add_argument(
        "--sidecars",
        type=Path,
        default=default_sidecars,
        help="Root directory containing sidecars (default: sidecars/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute what would change but do not write files",
    )
    parser.add_argument(
        "--include-skipped",
        action="store_true",
        help="Also curate sidecars that are normally skipped (AmyPET + Diagnosis)",
    )

    args = parser.parse_args(argv)

    config = load_config(args.config)
    index = build_source_index(config, config_dir=args.config.parent)

    sidecars_root: Path = args.sidecars
    sidecars = iter_sidecars(sidecars_root)
    if not sidecars:
        print("No sidecar files found.")
        return 2

    xsd_cache: dict[tuple[str, str], XsdIndex | None] = {}
    changed_paths: list[Path] = []

    for path in sidecars:
        if (not args.include_skipped) and path.name in SKIP_FILENAMES:
            continue

        domain = domain_from_sidecar_path(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        xml_name = get_display_xml_name(lines)
        if not xml_name:
            continue

        ref = index.by_domain_and_name.get((domain, xml_name))
        if ref is None:
            # sidecar doesn't map to configured sources
            continue

        cache_key = (str(ref.schemas_root), domain)
        if cache_key not in xsd_cache:
            xsd_cache[cache_key] = load_xsd_index_from_xml(ref.xml_path, domain)
        xsd_index = xsd_cache[cache_key]

        changed, new_lines = curate_sidecar_text(
            lines=lines,
            domain=domain,
            xsd_index=xsd_index,
            include_skipped=args.include_skipped,
            filename=path.name,
        )
        if not changed:
            continue

        changed_paths.append(path)
        if not args.dry_run:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    if args.dry_run:
        print(f"Would update {len(changed_paths)} sidecar files.")
    else:
        print(f"Updated {len(changed_paths)} sidecar files.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# ddi-dataset-model

The DDI dataset model is a curated set of metadata sidecars generated from XNAT display definitions, designed to be extractor-friendly for downstream use cases like documentation and AI agents.

Sidecars are YAML files that capture key metadata about XNAT display definitions in a structured, consistent format. It allows us to decouple metadata curation from the original XML definitions, enabling easier maintenance and more flexible downstream consumption. The metadata captured in sidecars includes field names, types, descriptions, enhanced semantics, and relationships, curated to be human-readable and machine-friendly.

Sidecars are generated from the original XNAT display definitions using Python tooling in this repository, and then curated to ensure they are accurate, consistent, and useful for downstream applications. The curated sidecars are stored in this repository as the authoritative source of metadata for XNAT displays.

This repository contains:

- Python tooling for generating and curating extractor-friendly metadata (YAML sidecars) from XNAT display definitions.
- The generated/curated sidecars themselves (authoritative copy).

## Inputs

The authoritative XSD schemas and display XMLs currently live in the XNAT plugin repository (e.g. `xnat-apgem-plugin`).
This repo reads those inputs via local filesystem paths configured in `config/source_repos.yaml`.

## Output layout

Sidecars are written to:

- `sidecars/<domain>/<display_xml_basename>.meta.yaml`

Example:

- `sidecars/diag/diag_DiagnosisData_display.meta.yaml`

## Commands

- Generate sidecars: `python scripts/generate_display_sidecars.py --config config/source_repos.yaml`
- Curate important fields: `python scripts/curate_sidecars_repo_wide.py --config config/source_repos.yaml`
- Validate sidecars: `python scripts/validate_display_sidecars.py`

All tools support `--dry-run`.

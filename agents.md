# agents.md

This repository contains source code for generating curated metadata sidecars from XNAT schemas and display definitions, and the curated sidecar outputs.

## Goal for automated agents

When making changes in this repo, prefer small, reviewable patches that:

- Keep sidecar generation deterministic and reproducible.
- Avoid introducing unnecessary runtime dependencies.
- Preserve existing folder conventions for schemas, scripts, and sidecars.
- Update documentation/config alongside behavior changes.

## Project layout (high-level)

- `pyproject.toml`: Python package metadata and build configuration.
- `README.md`: Project purpose, input/output conventions, and core commands.
- `config/source_repos.yaml`: Local source-repo paths used by sidecar tooling.
- `schemas/`: Source sidecar schema material used in generation workflows.
- `scripts/`: Main CLI scripts for generation, curation, and validation.
	- `generate_display_sidecars.py`
	- `curate_sidecars_repo_wide.py`
	- `validate_display_sidecars.py`
- `sidecars/`: Authoritative curated sidecars written as `<domain>/<name>.meta.yaml`.
- `src/ddi_data_model/`: Python package source code.

## Common tasks

### Generate sidecars

- `python scripts/generate_display_sidecars.py --config config/source_repos.yaml`

### Curate sidecars repo-wide

- `python scripts/curate_sidecars_repo_wide.py --config config/source_repos.yaml`

### Validate sidecars

- `python scripts/validate_display_sidecars.py`

All tools support `--dry-run` where applicable.

## Change guidelines

- Avoid broad reformatting unrelated to the requested change.
- Keep sidecar schema/field naming stable unless explicitly requested.
- Prefer additive metadata changes over destructive renames/removals.
- If generation behavior changes, include both:
	- a script/code update, and
	- a note in `README.md` (or relevant docs) describing the new behavior.

## Security and secrets

- Do not commit credentials, tokens, hostnames, or private filesystem paths.
- Keep `config/source_repos.yaml` free of environment-specific secrets.
- If new configuration is needed, prefer documented environment variables or local-only config patterns.

## What to include in a PR

- Short summary of what changed and why.
- Commands used to validate (generation/curation/validation).
- Scope of sidecar impact (which domains/files changed).
- Any follow-up migration or manual curation notes, if needed.


"""Seed data output: one minified JSON file per resource at {Type}/{id}.json,
plus a MANIFEST.md.

Keys are sorted and files written in sorted order so regenerated output
diffs cleanly. MANIFEST.md uses a non-.json extension because the server
parses every .json file under its data directories as a FHIR resource.
"""

import shutil
from pathlib import Path

import orjson


def write_seed_data(resources, output_dir, clean=True):
    output_dir = Path(output_dir)
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)  # drop any files left by a previous run
    output_dir.mkdir(parents=True, exist_ok=True)
    for resource in sorted(resources, key=lambda r: (r["resourceType"], r["id"])):
        type_dir = output_dir / resource["resourceType"]
        type_dir.mkdir(parents=True, exist_ok=True)
        path = type_dir / f"{resource['id']}.json"
        path.write_bytes(orjson.dumps(resource, option=orjson.OPT_SORT_KEYS))


def write_manifest(output_dir, info):
    """Written after validation so the validation summary can be included."""
    (Path(output_dir) / "MANIFEST.md").write_text(_manifest_markdown(info))


def _manifest_markdown(info):
    lines = [
        "# Generation Manifest",
        "",
        f"Source release date: {info.get('release_date', 'unknown')}",
        f"Generated: {info.get('generated_at', 'unknown')}",
        "",
        "## Source files",
        "",
    ]
    lines += [f"- {name}" for name in info.get("source_files", [])]
    lines += ["", "## Parameters", ""]
    lines += [f"- {key}: {value}" for key, value in sorted(info.get("parameters", {}).items())]
    lines += ["", "## Resource counts", ""]
    lines += [f"- {key}: {value}" for key, value in sorted(info.get("counts", {}).items())]
    dropped = info.get("dropped_targets", [])
    lines += ["", f"## Stripped reference targets ({len(dropped)})", ""]
    lines += [f"- {ref}" for ref in sorted(dropped)]
    lines += ["", "## Validation", ""]
    errors = info.get("validation_errors")
    if errors is None:
        lines.append("- skipped")
    else:
        lines.append(f"- errors: {errors}")
        lines.append("- reports: validation.html, validation.txt")
        categories = info.get("error_categories") or []
        if categories:
            lines += ["", "### Errors by category", ""]
            lines += [f"- {count}: {template}" for template, count in categories]
    lines.append("")
    return "\n".join(lines)

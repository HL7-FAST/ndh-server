"""Runs the steps in order: download, subset, clean up, add samples, check, validate, save."""

import copy
import hashlib
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import download
from .check import find_dangling_references
from .cleanup import strip_unresolved, transform_resource
from .constants import RESOURCE_TYPES
from .output import write_individual, write_manifest, write_seed_data
from .samples import SamplesConfig, build_samples
from .subset import SubsetConfig, run_subset
from .terminology import NUCC_SYSTEM, collect_codes, fetch_nucc_displays, load_display_map
from .validate import run_validator, summarize_validation

log = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    cities: list
    state: str
    output_dir: Path = None
    input_dir: Path = None    # pre-downloaded NDJSON dir; skips network fetch
    cache_dir: Path = None    # download cache used when input_dir is not set
    max_resources: int = 15000
    partof_depth_cap: int = 3
    raw: bool = False       # port CMS resources as-is, skipping NDH conversion
    ndjson: bool = False    # write one NDJSON file per type instead of per-resource files
    samples: bool = True
    append: bool = False    # add to output_dir instead of clearing it first
    min_counts: dict = field(default_factory=dict)
    skip_validate: bool = False
    validator_jar: Path = None
    ndh_package: Path = None
    java: str = "java"


def discover_sources(directory):
    """Group NDJSON files in a directory by their leading type token.

    The comparison is exact because a Practitioner* glob would also match
    PractitionerRole files.
    """
    sources = {resource_type: [] for resource_type in RESOURCE_TYPES}
    for path in sorted(Path(directory).iterdir()):
        name = path.name
        if not (name.endswith(".ndjson") or name.endswith(".ndjson.zst")):
            continue
        type_token = name.split("_")[0].split(".")[0]
        if type_token in sources:
            sources[type_token].append(path)
    return {resource_type: paths for resource_type, paths in sources.items() if paths}


def _repair_role_contacts(kept):
    """Enforce us-core pd-1: a PractitionerRole must have telecom or an endpoint.

    Roles with neither get telecom copied from their organization or
    practitioner; roles with no contact source are dropped.
    """
    repaired = 0
    dropped = 0
    for ref in sorted(kept):
        resource = kept[ref]
        if resource.get("resourceType") != "PractitionerRole":
            continue
        if resource.get("telecom") or resource.get("endpoint"):
            continue
        source = None
        for key in ("organization", "practitioner"):
            target = kept.get(resource.get(key, {}).get("reference"))
            if target and target.get("telecom"):
                source = target["telecom"]
                break
        if source:
            resource["telecom"] = copy.deepcopy(source[:1])
            repaired += 1
        else:
            log.warning("dropping %s: no telecom, no endpoint, and no contact source", ref)
            del kept[ref]
            dropped += 1
    return repaired, dropped


def run_pipeline(config):
    release_date = "unknown"
    if config.input_dir:
        sources = discover_sources(config.input_dir)
    else:
        release = download.fetch_release()
        release_date = release.get("release_date", "unknown")
        log.info("CMS NPD release %s", release_date)
        sources = {}
        for entry in release.get("files", []):
            resource_type = entry.get("resource_name")
            if resource_type not in RESOURCE_TYPES:
                continue
            path = download.download_file(entry, config.cache_dir)
            sources.setdefault(resource_type, []).append(path)
    source_files = sorted(path.name for paths in sources.values() for path in paths)

    subset = run_subset(
        sources,
        SubsetConfig(
            cities=config.cities,
            state=config.state,
            max_resources=config.max_resources,
            partof_depth_cap=config.partof_depth_cap,
        ),
    )

    # Raw mode ports CMS resources unchanged; cleanup converts them to NDH 2.0.
    # Either way, references outside the subset are still stripped for integrity.
    if config.raw:
        kept = dict(subset.kept)
    else:
        # Authoritative displays for correcting CMS display values: package
        # code systems (FaCeT, NDH) plus NUCC looked up from tx.fhir.org.
        display_map = {}
        if config.ndh_package and Path(config.ndh_package).exists():
            display_map = load_display_map(config.ndh_package)
        if collect_codes(subset.kept.values(), NUCC_SYSTEM):
            cache_root = Path(config.cache_dir) if config.cache_dir else Path.home() / ".cache" / "npd"
            display_map.update(fetch_nucc_displays(cache_root / "nucc-displays.json"))
        kept = {
            ref: transform_resource(resource, display_map)
            for ref, resource in subset.kept.items()
        }
    kept_refs = set(kept)
    kept = {ref: strip_unresolved(resource, kept_refs) for ref, resource in kept.items()}
    roles_repaired = roles_dropped = 0
    if not config.raw:
        roles_repaired, roles_dropped = _repair_role_contacts(kept)

    new_resources = []
    if config.samples:
        new_resources = build_samples(
            kept, subset.roles, SamplesConfig(cities=config.cities, state=config.state)
        )

    resources = list(kept.values()) + new_resources
    dangling = find_dangling_references(resources)
    if dangling:
        raise RuntimeError(f"{len(dangling)} dangling references, first: {dangling[:10]}")

    counts = {}
    for resource in resources:
        counts[resource["resourceType"]] = counts.get(resource["resourceType"], 0) + 1
    for resource_type, minimum in config.min_counts.items():
        actual = counts.get(resource_type, 0)
        if actual < minimum:
            raise RuntimeError(
                f"{resource_type}: {actual} kept, minimum {minimum}. "
                "Widen the geography or lower the minimum."
            )

    parameters = {
        "cities": config.cities,
        "state": config.state,
        "max_resources": config.max_resources,
        "partof_depth_cap": config.partof_depth_cap,
        "raw": config.raw,
        "ndjson": config.ndjson,
        "samples": config.samples,
    }
    if config.ndh_package and Path(config.ndh_package).exists():
        # Record which package snapshot this run validated against.
        parameters["ndh_package_sha256"] = hashlib.sha256(
            Path(config.ndh_package).read_bytes()
        ).hexdigest()

    write_seed_data(resources, config.output_dir, clean=not config.append, ndjson=config.ndjson)

    validation_errors = None
    error_categories = []
    if not config.skip_validate:
        # The validator reads one resource per file, so in NDJSON mode stage a
        # temporary per-resource copy for it.
        if config.ndjson:
            validate_root = Path(tempfile.mkdtemp(prefix="npd-validate-"))
            write_individual(resources, validate_root)
        else:
            validate_root = Path(config.output_dir)
        # Per-type subdirectories only, so the validator never sees MANIFEST.md.
        type_dirs = sorted(p for p in validate_root.iterdir() if p.is_dir())
        html_path = Path(config.output_dir) / "validation.html"
        compact_path = Path(config.output_dir) / "validation.txt"
        ok, output = run_validator(
            type_dirs,
            ig_package=config.ndh_package,
            validator_jar=config.validator_jar,
            java=config.java,
            html_output=html_path,
            compact_output=compact_path,
        )
        if config.ndjson:
            shutil.rmtree(validate_root, ignore_errors=True)
        report = compact_path.read_text() if compact_path.exists() else ""
        validation_errors, error_categories = summarize_validation(report)
        if not ok and validation_errors == 0:
            # Non-zero exit with no reported errors means the validator itself failed.
            raise RuntimeError(f"validator did not run:\n{output[-4000:]}")
        log.info(
            "validation found %d errors; reports at %s and %s",
            validation_errors, html_path, compact_path,
        )

    write_manifest(
        config.output_dir,
        {
            "release_date": release_date,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_files": source_files,
            "parameters": parameters,
            "counts": counts,
            "dropped_targets": sorted(subset.dropped_targets),
            "validation_errors": validation_errors,
            "error_categories": error_categories,
        },
    )

    summary = {
        "release_date": release_date,
        "total": len(resources),
        "counts": dict(sorted(counts.items())),
        "samples": len(new_resources),
        "roles_repaired": roles_repaired,
        "roles_dropped": roles_dropped,
        "dangling_references": len(dangling),
        "dropped_targets": len(subset.dropped_targets),
        "validation_errors": validation_errors,
        "subset_stats": subset.stats,
    }
    log.info("pipeline complete: %s", summary)
    return summary

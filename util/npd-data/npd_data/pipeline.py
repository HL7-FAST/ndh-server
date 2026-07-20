"""Runs the steps in order: download, subset, clean up, add samples, check, validate, save."""

import copy
import hashlib
import logging
import multiprocessing
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import orjson

from . import download
from .check import find_dangling_references
from .cleanup import strip_unresolved, transform_resource
from .conformance import conformance_keep
from .constants import RESOURCE_TYPES
from .ndjson import iter_ndjson, loads
from .output import ndjson_filename, write_individual, write_manifest, write_seed_data
from .samples import SamplesConfig, build_samples
from .subset import SubsetConfig, run_subset
from .terminology import NUCC_SYSTEM, collect_codes, fetch_nucc_displays, load_display_map
from .validate import run_validator_sharded, summarize_validation

log = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    cities: list = None
    state: str = None
    all_records: bool = False   # transform all records, streaming, no city filter
    output_dir: Path = None
    input_dir: Path = None    # pre-downloaded NDJSON dir; skips network fetch
    cache_dir: Path = None    # download cache used when input_dir is not set
    max_resources: int = 15000
    partof_depth_cap: int = 3
    raw: bool = False       # port CMS resources as-is, skipping NDH conversion
    ndjson: bool = False    # write one NDJSON file per type instead of per-resource files
    samples: bool = False   # include the generated sample resources
    append: bool = False    # add to output_dir instead of clearing it first
    min_counts: dict = field(default_factory=dict)
    skip_validate: bool = False
    validator_jar: Path = None
    ndh_package: Path = None
    force_download: bool = False  # re-fetch the cached NDH package and validator jar
    java: str = "java"
    shards: int = None        # parallel validator processes; None = auto (scale to cores)
    tx_cache: Path = None     # root for per-shard terminology caches
    validator_xmx: str = "2g"  # java max heap per validator process
    html: bool = False        # also write validation.html (forces a single validator process)


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


def _load_sources(config):
    if config.input_dir:
        return discover_sources(config.input_dir), "unknown"
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
    return sources, release_date


def _cache_root(config):
    return Path(config.cache_dir) if config.cache_dir else Path.home() / ".cache" / "npd"


def _drop_reason(resource, raw):
    """Why a record cannot survive the full-data pipeline, or None to keep it."""
    if not conformance_keep(resource):
        return "inactive or non-active status"
    # The subset pipeline repairs contactless roles from their organization or
    # practitioner; streaming has no lookup source, so such roles are dropped
    # instead (us-core pd-1 requires telecom or endpoint).
    if not raw and resource.get("resourceType") == "PractitionerRole":
        if not (resource.get("telecom") or resource.get("endpoint")):
            return "PractitionerRole without telecom or endpoint"
    return None


# Transform state shared with pass-2 workers. Populated in the parent before
# the pool forks; workers read it as inherited memory, so nothing is pickled.
_worker = {}


def _transform_chunk(lines):
    """Transform a chunk of raw NDJSON lines into output bytes.

    Runs in a pass-2 worker process: parse, drop non-survivors, transform,
    strip unresolved references, and serialize, so the parent process does
    nothing but file I/O.
    """
    out = []
    # Stripped references split by why the target is absent: dropped by the
    # conformance pass, or never present in the source files at all.
    stripped_dropped = 0
    stripped_missing = 0
    for line in lines:
        resource = loads(line)
        if f"{resource['resourceType']}/{resource['id']}" not in _worker["kept_refs"]:
            continue
        if not _worker["raw"]:
            resource = transform_resource(resource, _worker["display_map"])
        for ref in strip_unresolved(resource, _worker["kept_refs"]):
            if ref in _worker["dropped_refs"]:
                stripped_dropped += 1
            else:
                stripped_missing += 1
        out.append(orjson.dumps(resource, option=orjson.OPT_SORT_KEYS))
    return b"\n".join(out) + (b"\n" if out else b""), len(out), stripped_dropped, stripped_missing


def _chunk_lines(paths, size):
    chunk = []
    for path in paths:
        for line in iter_ndjson(path, raw=True):
            chunk.append(line)
            if len(chunk) == size:
                yield chunk
                chunk = []
    if chunk:
        yield chunk


def _transform_jobs():
    if multiprocessing.get_start_method() != "fork":
        return 1   # workers rely on state inherited at fork
    cores = os.cpu_count() or 1
    # Capped: refcount updates during set lookups gradually dirty the shared
    # ref set's copy-on-write pages, costing up to its full size per worker.
    return max(1, min(cores - 1, 8))


def run_full(config, sources, source_files, release_date):
    """Stream all records through the conformance transform, no subsetting.

    Two passes, so reference stripping knows the full survivor set without
    holding resources in memory: pass 1 collects surviving refs (a set of
    strings), pass 2 transforms each survivor and appends it to its per-type
    NDJSON file. Pass 2 fans the CPU-heavy per-record work out to worker
    processes; the parent only reads lines and writes results, in order.
    """
    display_map = {}
    if not config.raw:
        display_map = load_display_map(config.ndh_package)
        display_map.update(fetch_nucc_displays(_cache_root(config) / "nucc-displays.json"))
    log.info("pass 1/2: collecting conformant resources")
    kept_refs = set()
    dropped_refs = set()
    drop_counts = {}
    # Telecom-less roles satisfy pd-1 only through their endpoints; whether
    # those endpoints survive is not known until pass 1 has seen every type.
    contactless_roles = {}
    for resource_type in sorted(sources):
        before_kept, before_dropped = len(kept_refs), sum(drop_counts.values())
        for path in sources[resource_type]:
            for resource in iter_ndjson(path):
                reason = _drop_reason(resource, config.raw)
                if reason is None:
                    ref = f"{resource['resourceType']}/{resource['id']}"
                    kept_refs.add(ref)
                    if (
                        not config.raw
                        and resource.get("resourceType") == "PractitionerRole"
                        and not resource.get("telecom")
                    ):
                        contactless_roles[ref] = [
                            endpoint.get("reference")
                            for endpoint in resource.get("endpoint", [])
                            if isinstance(endpoint, dict)
                        ]
                else:
                    drop_counts[reason] = drop_counts.get(reason, 0) + 1
                    dropped_refs.add(f"{resource['resourceType']}/{resource['id']}")
        log.info(
            "  %s: %d kept, %d dropped",
            resource_type, len(kept_refs) - before_kept, sum(drop_counts.values()) - before_dropped,
        )
    unrepairable = [
        ref for ref, endpoints in contactless_roles.items()
        if not any(endpoint in kept_refs for endpoint in endpoints)
    ]
    if unrepairable:
        log.info(
            "  dropping %d role(s) whose only contact endpoints did not survive",
            len(unrepairable),
        )
        kept_refs.difference_update(unrepairable)
        dropped_refs.update(unrepairable)
        drop_counts["PractitionerRole whose only contact endpoints were dropped"] = len(unrepairable)
    dropped = sum(drop_counts.values())

    output_dir = Path(config.output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    jobs = _transform_jobs()
    _worker.update(
        kept_refs=kept_refs, dropped_refs=dropped_refs, display_map=display_map, raw=config.raw
    )
    log.info(
        "pass 2/2: transforming and writing %d resources (%d worker process(es))",
        len(kept_refs), jobs,
    )
    refs_to_dropped = 0
    refs_missing = 0
    for resource_type in sorted(sources):
        written = 0
        with open(output_dir / ndjson_filename(resource_type), "wb") as out:
            chunks = _chunk_lines(sources[resource_type], 500)
            if jobs == 1:
                results = map(_transform_chunk, chunks)
                for data, count, to_dropped, missing in results:
                    out.write(data)
                    written += count
                    refs_to_dropped += to_dropped
                    refs_missing += missing
            else:
                # A pool per type keeps worker lifetimes short, releasing the
                # copy-on-write growth between types.
                with multiprocessing.Pool(jobs) as pool:
                    for data, count, to_dropped, missing in pool.imap(_transform_chunk, chunks):
                        out.write(data)
                        written += count
                        refs_to_dropped += to_dropped
                        refs_missing += missing
        counts[resource_type] = written
        log.info("  %s: %d written", resource_type, written)

    for resource_type, minimum in config.min_counts.items():
        actual = counts.get(resource_type, 0)
        if actual < minimum:
            raise RuntimeError(f"{resource_type}: {actual} kept, minimum {minimum}.")

    parameters = {
        "all_records": True,
        "raw": config.raw,
        "ndjson": True,
        "nonconformant_dropped": dropped,
    }
    # Per-reason keys share the total's prefix so they sort directly under it.
    for reason, count in sorted(drop_counts.items()):
        parameters[f"nonconformant_dropped ({reason})"] = count
    parameters["references_stripped"] = refs_to_dropped + refs_missing
    parameters["references_stripped (target dropped as nonconformant)"] = refs_to_dropped
    parameters["references_stripped (target missing from source data)"] = refs_missing
    if config.ndh_package and Path(config.ndh_package).exists():
        parameters["ndh_package_sha256"] = hashlib.sha256(
            Path(config.ndh_package).read_bytes()
        ).hexdigest()
    write_manifest(
        output_dir,
        {
            "release_date": release_date,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_files": source_files,
            "parameters": parameters,
            "counts": counts,
            "dropped_targets": None,
            "validation_errors": None,
        },
    )
    summary = {
        "release_date": release_date,
        "total": sum(counts.values()),
        "counts": dict(sorted(counts.items())),
        "nonconformant_dropped": dropped,
        "nonconformant_dropped_by_reason": dict(sorted(drop_counts.items())),
        "references_stripped": refs_to_dropped + refs_missing,
        "references_stripped_by_reason": {
            "target dropped as nonconformant": refs_to_dropped,
            "target missing from source data": refs_missing,
        },
        "validation_errors": None,
    }
    log.info("pipeline complete: %s", summary)
    return summary


def run_pipeline(config):
    # The package serves the transform (display corrections) and validation;
    # only a raw, unvalidated run gets by without it. Both tools are resolved
    # before the multi-gigabyte source download so a bad spec fails fast.
    if not config.raw or not config.skip_validate:
        config.ndh_package = download.fetch_ndh_package(
            config.ndh_package, _cache_root(config), force=config.force_download
        )
    if not config.skip_validate:
        config.validator_jar = download.fetch_validator_jar(
            config.validator_jar, _cache_root(config), force=config.force_download
        )

    sources, release_date = _load_sources(config)
    source_files = sorted(path.name for paths in sources.values() for path in paths)

    if config.all_records:
        return run_full(config, sources, source_files, release_date)

    subset = run_subset(
        sources,
        SubsetConfig(
            cities=config.cities,
            state=config.state,
            max_resources=config.max_resources,
            partof_depth_cap=config.partof_depth_cap,
        ),
    )

    log.info("cleaning up %d resources", len(subset.kept))
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
            display_map.update(fetch_nucc_displays(_cache_root(config) / "nucc-displays.json"))
        kept = {
            ref: transform_resource(resource, display_map)
            for ref, resource in subset.kept.items()
        }
    kept_refs = set(kept)
    for resource in kept.values():
        strip_unresolved(resource, kept_refs)
    roles_repaired = roles_dropped = 0
    if not config.raw:
        roles_repaired, roles_dropped = _repair_role_contacts(kept)

    new_resources = []
    if config.samples:
        log.info("adding sample resources")
        new_resources = build_samples(
            kept, subset.roles, SamplesConfig(cities=config.cities, state=config.state)
        )

    resources = list(kept.values()) + new_resources
    log.info("checking references across %d resources", len(resources))
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
        "shards": config.shards if config.shards else "auto",
    }
    if config.ndh_package and Path(config.ndh_package).exists():
        # Record which package snapshot this run validated against.
        parameters["ndh_package_sha256"] = hashlib.sha256(
            Path(config.ndh_package).read_bytes()
        ).hexdigest()

    log.info("writing %d resources to %s", len(resources), config.output_dir)
    write_seed_data(resources, config.output_dir, clean=not config.append, ndjson=config.ndjson)

    validation_errors = None
    error_categories = []
    if not config.skip_validate:
        # NDJSON output needs a per-resource staging tree; JSON output already has one.
        staging = (
            Path(tempfile.mkdtemp(prefix="npd-validate-"))
            if config.ndjson else Path(config.output_dir)
        )
        if config.ndjson:
            write_individual(resources, staging)
        tx_cache_root = Path(config.tx_cache) if config.tx_cache else _cache_root(config) / "tx"
        try:
            report = run_validator_sharded(
                staging,
                ig_package=config.ndh_package,
                validator_jar=config.validator_jar,
                java=config.java,
                shards=config.shards,
                tx_cache_root=tx_cache_root,
                xmx=config.validator_xmx,
                html_output=(Path(config.output_dir) / "validation.html") if config.html else None,
            )
        finally:
            shutil.rmtree(
                staging if config.ndjson else staging / "_shards",
                ignore_errors=True,
            )
        compact_path = Path(config.output_dir) / "validation.txt"
        compact_path.write_text(report)
        validation_errors, error_categories = summarize_validation(report)
        log.info("validation found %d errors; report at %s", validation_errors, compact_path)

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

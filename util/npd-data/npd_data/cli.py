"""Command line interface.

Example:
    python -m npd_data --city Aiken --state SC --output-dir ./out
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .pipeline import PipelineConfig, run_pipeline


def build_config(argv=None):
    parser = argparse.ArgumentParser(
        prog="npd_data",
        description=(
            "Generate NDH 2.0 conformant seed data from the CMS National "
            "Provider Directory bulk NDJSON publication."
        ),
    )
    parser.add_argument(
        "--city",
        action="append",
        dest="cities",
        help="Target city; repeatable for multi-city subsets",
    )
    parser.add_argument("--state", help="Two-letter state code")
    parser.add_argument(
        "--all", "--all-records", action="store_true", dest="all_records",
        help=(
            "Transform all records instead of a city subset; "
            "implies --ndjson and --skip-validate"
        ),
    )
    parser.add_argument("--output-dir", required=True, help="Directory for generated seed data")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input-dir", help="Directory of pre-downloaded NDJSON[.zst] files")
    source.add_argument(
        "--cache-dir",
        help="Download cache directory, fetches current release (default ~/.cache/npd)",
    )
    parser.add_argument("--max-resources", type=int, default=15000)
    parser.add_argument("--partof-depth-cap", type=int, default=3)
    parser.add_argument(
        "--raw", action="store_true",
        help="Port CMS resources as-is, skipping NDH conversion (filtering and references still apply)",
    )
    parser.add_argument(
        "--ndjson", action="store_true",
        help="Write one NDJSON file per resource type instead of one JSON file per resource",
    )
    parser.add_argument(
        "--samples", action="store_true",
        help=(
            "Include sample resources for the types CMS does not publish "
            "(networks, healthcare services, insurance plans, verifications, "
            "and a provider group)"
        ),
    )
    parser.add_argument(
        "--append", action="store_true", help="Add to the output directory instead of clearing it"
    )
    parser.add_argument(
        "--min-count",
        action="append",
        default=[],
        metavar="TYPE=N",
        help="Fail generation if fewer than N resources of TYPE are kept; repeatable",
    )
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument(
        "--validator-jar",
        help=(
            "Path or URL to HAPI validator_cli.jar "
            "(default: download the latest release into the cache)"
        ),
    )
    parser.add_argument(
        "--ndh-package",
        help=(
            "Path or URL to a hl7.fhir.us.ndh package.tgz "
            "(default: download the current IG build into the cache)"
        ),
    )
    parser.add_argument(
        "--force-download", action="store_true",
        help="Re-download the cached NDH package and validator jar",
    )
    parser.add_argument("--java", default="java")
    parser.add_argument(
        "--shards", default="auto",
        help="Parallel validator processes, or 'auto' (default) to scale to CPU cores",
    )
    parser.add_argument(
        "--tx-cache",
        help="Root for per-shard terminology caches (default: <cache-dir>/tx or ~/.cache/npd/tx)",
    )
    parser.add_argument(
        "--validator-xmx", default="2g", help="Java max heap per validator process (default 2g)"
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Also write validation.html; forces a single validator process (no sharding)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.input_dir and not args.cache_dir:
        args.cache_dir = str(Path.home() / ".cache" / "npd")

    if args.all_records:
        if args.cities or args.state:
            parser.error("--city/--state have no effect with --all")
        if args.append:
            parser.error("--append is not supported with --all")
        if args.samples:
            parser.error("--samples has no effect with --all")
        # Per-resource files are not viable for all records, and validating
        # them all takes weeks; validate a city subset for a conformance signal.
        args.ndjson = True
        args.skip_validate = True
    elif not (args.cities and args.state):
        parser.error("--city and --state are required unless --all is set")

    # Check paths and dependencies up front so a typo fails in a second
    # instead of after the download and subsetting work.
    if args.input_dir and not Path(args.input_dir).is_dir():
        parser.error(f"--input-dir not found: {args.input_dir}")
    # The output directory is cleared before writing; sources must not live
    # in or around it or the run destroys its own input.
    output_dir = Path(args.output_dir).resolve()
    for source_option in ("input_dir", "cache_dir"):
        value = getattr(args, source_option)
        if not value:
            continue
        source_dir = Path(value).resolve()
        if (
            source_dir == output_dir
            or source_dir in output_dir.parents
            or output_dir in source_dir.parents
        ):
            parser.error(
                f"--output-dir must not overlap --{source_option.replace('_', '-')}"
            )
    if not args.skip_validate and shutil.which(args.java) is None:
        parser.error(f"java executable not found: {args.java}")
    for option in ("validator_jar", "ndh_package"):
        spec = getattr(args, option)
        if spec and not str(spec).startswith(("http://", "https://")) and not Path(spec).is_file():
            parser.error(f"--{option.replace('_', '-')} not found: {spec}")

    shards = None
    if args.shards != "auto":
        try:
            shards = int(args.shards)
        except ValueError:
            parser.error(f"--shards must be an integer or 'auto': {args.shards!r}")
        if shards < 1:
            parser.error("--shards must be >= 1")

    min_counts = {}
    for spec in args.min_count:
        resource_type, _, count = spec.partition("=")
        try:
            min_counts[resource_type] = int(count)
        except ValueError:
            parser.error(f"invalid --min-count {spec!r}; expected TYPE=N")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    return PipelineConfig(
        cities=args.cities,
        state=args.state,
        all_records=args.all_records,
        output_dir=args.output_dir,
        input_dir=args.input_dir,
        cache_dir=args.cache_dir,
        max_resources=args.max_resources,
        partof_depth_cap=args.partof_depth_cap,
        raw=args.raw,
        ndjson=args.ndjson,
        samples=args.samples,
        append=args.append,
        min_counts=min_counts,
        skip_validate=args.skip_validate,
        validator_jar=args.validator_jar,
        ndh_package=args.ndh_package,
        force_download=args.force_download,
        java=args.java,
        shards=shards,
        tx_cache=args.tx_cache,
        validator_xmx=args.validator_xmx,
        html=args.html,
    )


def main(argv=None):
    config = build_config(argv)
    summary = run_pipeline(config)
    print()
    print(f"Release:      {summary['release_date']}")
    print(f"Resources:    {summary['total']} ({summary.get('samples', 0)} samples)")
    for resource_type, count in summary["counts"].items():
        print(f"  {resource_type}: {count}")
    if "subset_stats" in summary:
        print(f"Roles:        {summary['subset_stats']['by_role']}")
        print(f"Stripped refs: {summary['dropped_targets']}")
    else:
        print(f"Nonconformant dropped: {summary['nonconformant_dropped']}")
        for reason, count in summary.get("nonconformant_dropped_by_reason", {}).items():
            print(f"  {reason}: {count}")
        print(f"References stripped: {summary['references_stripped']}")
        for reason, count in summary.get("references_stripped_by_reason", {}).items():
            print(f"  {reason}: {count}")
    if summary["validation_errors"] is not None:
        print(f"Validation:   {summary['validation_errors']} errors ({config.output_dir}/validation.txt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

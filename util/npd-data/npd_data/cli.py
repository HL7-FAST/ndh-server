"""Command line interface.

Example:
    python -m npd_data --city Aiken --state SC \\
        --cache-dir ~/.cache/npd --output-dir ./out \\
        --validator-jar ~/tools/validator_cli.jar --ndh-package ~/tools/ndh-2.0.0-current.tgz
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
        required=True,
        dest="cities",
        help="Target city; repeatable for multi-city subsets",
    )
    parser.add_argument("--state", required=True, help="Two-letter state code")
    parser.add_argument("--output-dir", required=True, help="Directory for generated seed data")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-dir", help="Directory of pre-downloaded NDJSON[.zst] files")
    source.add_argument("--cache-dir", help="Download cache directory (fetches current release)")
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
    parser.add_argument("--no-samples", action="store_true", help="Skip the added sample resources")
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
    parser.add_argument("--validator-jar", help="Path to HAPI validator_cli.jar")
    parser.add_argument("--ndh-package", help="Path to pinned hl7.fhir.us.ndh package.tgz")
    parser.add_argument("--java", default="java")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    # Check paths and dependencies up front so a typo fails in a second
    # instead of after the download and subsetting work.
    if args.input_dir and not Path(args.input_dir).is_dir():
        parser.error(f"--input-dir not found: {args.input_dir}")
    if not args.skip_validate:
        if not (args.validator_jar and args.ndh_package):
            parser.error("--validator-jar and --ndh-package are required unless --skip-validate is set")
        if not Path(args.validator_jar).is_file():
            parser.error(f"--validator-jar not found: {args.validator_jar}")
        if not Path(args.ndh_package).is_file():
            parser.error(f"--ndh-package not found: {args.ndh_package}")
        if shutil.which(args.java) is None:
            parser.error(f"java executable not found: {args.java}")

    min_counts = {}
    for spec in args.min_count:
        resource_type, _, count = spec.partition("=")
        try:
            min_counts[resource_type] = int(count)
        except ValueError:
            parser.error(f"invalid --min-count {spec!r}; expected TYPE=N")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    return PipelineConfig(
        cities=args.cities,
        state=args.state,
        output_dir=args.output_dir,
        input_dir=args.input_dir,
        cache_dir=args.cache_dir,
        max_resources=args.max_resources,
        partof_depth_cap=args.partof_depth_cap,
        raw=args.raw,
        ndjson=args.ndjson,
        samples=not args.no_samples,
        append=args.append,
        min_counts=min_counts,
        skip_validate=args.skip_validate,
        validator_jar=args.validator_jar,
        ndh_package=args.ndh_package,
        java=args.java,
    )


def main(argv=None):
    config = build_config(argv)
    summary = run_pipeline(config)
    print()
    print(f"Release:      {summary['release_date']}")
    print(f"Resources:    {summary['total']} ({summary['samples']} samples)")
    for resource_type, count in summary["counts"].items():
        print(f"  {resource_type}: {count}")
    print(f"Roles:        {summary['subset_stats']['by_role']}")
    print(f"Stripped refs: {summary['dropped_targets']}")
    if summary["validation_errors"] is not None:
        print(f"Validation:   {summary['validation_errors']} errors ({config.output_dir}/validation.html)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

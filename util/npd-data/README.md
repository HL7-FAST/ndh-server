# npd-data

Builds sample seed data for the NDH reference server from the CMS National
Provider Directory files published at https://directory.cms.gov/

The tool downloads the published files, keeps the records for a chosen city,
cleans them up to conform to the NDH implementation guide, validates
everything, and writes one JSON file per resource in the layout the server
loads at startup. With `--samples` it also adds a small set of sample
resources for the types CMS does not publish (networks, healthcare services,
insurance plans, verifications, and a provider group).

## Requirements

- Python 3.11+ (managed with uv: `uv sync`)
- For validation: Java 17+

The NDH IG package and the HAPI validator jar are downloaded into the
cache automatically; pass `--ndh-package` or `--validator-jar` (a file
path or URL) to use specific ones.

## Usage

Install the dependencies:

```bash
uv sync
```

Then generate a city subset:

```bash
uv run python -m npd_data --city "Helena" --state MT --output-dir ./out
```

The first run downloads the current CMS release to `--cache-dir`
(default `~/.cache/npd`). Later runs reuse the cache. Use `--input-dir`
instead if the files are already downloaded.

To run the conformance transform over all records
instead of a city subset, for example when a new CMS release lands or the
IG changes:

```bash
uv run python -m npd_data --all --output-dir ./out-all
```

`--all` implies `--ndjson` (avoids creating millions of individual files)
and `--skip-validate` (to avoid validating millions of records).

This streams the source files in two passes (collect the surviving
references, then transform and write), so memory use is a few GB for the
reference set rather than the full data. The transform work in the second
pass runs across several worker processes. Roles that fail us-core pd-1
(no telecom and no endpoint) are dropped rather than repaired, since
streaming has no lookup source to copy contact details from.

### Options

Selecting records:

- `--city` can be given more than once; matching is case-insensitive and
  always combined with `--state`.
- `--all` (or `--all-records`) transforms every conformant record instead
  of a city subset. Implies `--ndjson` and `--skip-validate`.
- `--min-count Practitioner=200` fails the run if a resource type comes up
  short, a sign the chosen city is too small.
- `--max-resources` (default 15000) fails the run instead of producing an
  oversized data set.

Transforming:

- `--raw` ports the CMS resources unchanged, skipping the NDH conversion
  (code systems, extensions, etc.). Filtering and reference handling still
  apply. Off by default.
- `--samples` adds sample resources for the types CMS does not publish
  (networks, healthcare services, insurance plans, verifications, and a
  provider group). Off by default.

Writing output:

- `--ndjson` writes one NDJSON file per resource type instead of one JSON
  file per resource. The server's DataInitializer loads both layouts. File
  names carry a numeric prefix (`01-Organization.ndjson`,
  `07-PractitionerRole.ndjson`, ...) so a server loading files in name order
  sees referenced types before the types that reference them.
- `--append` adds to the output directory instead of clearing it, so you can
  build up several cities across runs.

Validating:

- `--skip-validate` skips validation; use only while iterating.
- `--shards` sets how many validator processes run in parallel, or `auto`
  (default) to scale to the machine's CPU cores and available memory (each
  shard is a JVM needing `--validator-xmx` plus overhead).
- `--validator-xmx` sets the Java max heap per validator process (default `2g`).
- `--tx-cache` sets the directory holding the per-shard terminology caches
  (default `<cache-dir>/tx` or `~/.cache/npd/tx`); reused across runs.
- `--html` also writes `validation.html`. That report is one aggregate document
  the validator cannot merge across shards, so it forces a single validator
  process (slower, but you get the browsable HTML report).

Downloading tools:

- `--ndh-package` takes a file path or URL to a specific NDH package.tgz.
  By default the current IG build is downloaded into the cache and reused.
- `--validator-jar` takes a file path or URL to a specific HAPI
  validator_cli.jar. By default the latest release is downloaded into the
  cache and reused.
- `--force-download` re-downloads the cached NDH package and validator jar,
  picking up a newer IG build or validator release.

## Installing the output

After reviewing the generated data, copy it into the server and list
`npd-data` in the `initial-data` section of `application.yaml`:

```bash
rm -rf ../../src/main/resources/npd-data
cp -r out ../../src/main/resources/npd-data
```

## Steps

Each step lives in the matching source file under `npd_data/`.

1. **Download** (`download.py`): get the current CMS data files.
2. **Pick the city** (`subset.py`): keep the practitioners, organizations, and locations in the chosen city.
3. **Add their connections** (`subset.py`): keep the roles and affiliations that link those records.
4. **Fill in references** (`subset.py`): pull in everything those records point to, so nothing is left dangling.
5. **Clean up** (`cleanup.py`): fix the CMS data so it conforms to the NDH implementation guide.
6. **Add samples** (`samples.py`): with `--samples`, create sample resources for the types CMS does not publish.
7. **Check links** (`check.py`): confirm every reference resolves inside the set.
8. **Validate** (`validate.py`): run the FHIR validator against the NDH package, sharded across CPU cores (the resources are split into N groups, each validated by its own process with its own terminology cache), and concatenate the per-shard eslint-compact output into `validation.txt` next to `MANIFEST.md`. Validation errors are counted and reported, not treated as fatal; review the report.
9. **Save** (`output.py`): write one JSON file per resource, plus a MANIFEST.md.

`pipeline.py` runs these in order; `cli.py` handles the command line.

# npd-data

Builds sample seed data for the NDH reference server from the CMS National
Provider Directory files published at https://directory.cms.gov/

The tool downloads the published files, keeps the records for a chosen city,
cleans them up to conform to the NDH implementation guide, adds a small set
of sample resources for the types CMS does not publish (networks, healthcare
services, insurance plans, verifications, and a provider group), validates
everything, and writes one JSON file per resource in the layout the server
loads at startup.

## Requirements

- Python 3.11+ (managed with uv: `uv sync`)
- For validation: Java 17+, the HAPI validator
  ([validator_cli.jar](https://github.com/hapifhir/org.hl7.fhir.core/releases)),
  and a downloaded copy of the NDH package
  (`curl -L -o ndh-2.0.0-current.tgz https://build.fhir.org/ig/HL7/fhir-us-ndh/package.tgz`)

## Usage

```bash
cd util/npd-data
uv sync

uv run python -m npd_data \
  --city "Helena" --state MT \
  --cache-dir ~/.cache/npd \
  --output-dir ./out \
  --validator-jar ~/tools/validator_cli.jar \
  --ndh-package ~/tools/ndh-2.0.0-current.tgz
```

The first run downloads the current CMS release. Later runs reuse
the cache. Use `--input-dir` instead of `--cache-dir` if the files are
already downloaded.

Options:

- `--city` can be given more than once; matching is case-insensitive and
  always combined with `--state`.
- `--min-count Practitioner=200` fails the run if a resource type comes up
  short, a sign the chosen city is too small.
- `--max-resources` (default 15000) fails the run instead of producing an
  oversized data set.
- `--raw` ports the CMS resources unchanged, skipping the NDH conversion
  (code systems, extensions, etc.). Filtering and reference handling still
  apply. Off by default. Pair with `--no-samples` for a pure CMS subset.
- `--no-samples` skips the added sample resources.
- `--append` adds to the output directory instead of clearing it, so you can
  build up several cities across runs.
- `--skip-validate` skips validation; use only while iterating.

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
6. **Add samples** (`samples.py`): create sample resources for the types CMS does not publish.
7. **Check links** (`check.py`): confirm every reference resolves inside the set.
8. **Validate** (`validate.py`): run the FHIR validator against the NDH package, saving an HTML report (`validation.html`) and an eslint-compact text report (`validation.txt`) next to `MANIFEST.md`. Validation errors are counted and reported, not treated as fatal; review the reports.
9. **Save** (`output.py`): write one JSON file per resource, plus a MANIFEST.md.

`pipeline.py` runs these in order; `cli.py` handles the command line.

"""HAPI validator CLI wrapper.

Validation is sharded across a few CPU cores: the resources are split into N
groups, each validated by its own validator process with its own terminology
cache, and the eslint-compact reports are concatenated. eslint lines embed the
file path, so the merged report needs no ordering.

The shard count is kept conservative on purpose. Each shard is a full, memory-
and CPU-heavy validator JVM, and the validator's FHIRPath regex check has a
hardcoded 500ms timeout (not configurable) that fires spuriously when too many
JVMs starve the CPU. A handful of shards gives most of the speedup without that.

Requires Java 17+ and network access to tx.fhir.org. The IG package is supplied
as a downloaded package.tgz path.
"""

import collections
import concurrent.futures
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

_ERROR_LINE = re.compile(r", (?:Error|Fatal) - (.+)$", re.MULTILINE)


def _bytes(text):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmMgG]?)", str(text).strip())
    if not match:
        return None
    unit = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2).lower()]
    return int(float(match.group(1)) * unit)


def _available_memory():
    """Memory available without swapping, including reclaimable caches.

    Free-page counts (sysconf) understate badly right after streaming the
    multi-gigabyte source files, since the page cache is reclaimable; psutil
    reports the platform's real availability metric on Linux/macOS/Windows.
    """
    try:
        return psutil.virtual_memory().available
    except Exception:
        return None


def _gib(size):
    return f"{size / 1024**3:.1f}g"


def shard_count(cap=4, reserve=1, xmx=None):
    """Conservative parallel validator count from usable cores and, when xmx
    is given, available memory. Cross-platform.

    Capped low because each shard is a heavy validator JVM; over-sharding
    starves the CPU and trips the validator's 500ms regex timeout (spurious
    errors). Shards that do not fit in memory are worse still: a swapping
    JVM runs several times slower than the sharding gains.
    """
    if hasattr(os, "process_cpu_count"):        # Python 3.13+, affinity-aware
        cores = os.process_cpu_count() or 1
    elif hasattr(os, "sched_getaffinity"):      # Linux
        cores = len(os.sched_getaffinity(0))
    else:                                        # Windows / macOS
        cores = os.cpu_count() or 1
    n = max(1, min(cores - reserve, cap))
    heap = _bytes(xmx) if xmx else None
    available = _available_memory()
    if heap and available:
        # A JVM's footprint runs past its heap (metaspace, code cache, stacks).
        footprint = heap + 512 * 1024**2
        by_memory = max(1, int(available // footprint))
        if by_memory < n:
            log.info(
                "validation: available memory %s fits %d shard(s) of %s heap plus "
                "overhead (cores allow %d); lower --validator-xmx to run more in parallel",
                _gib(available), by_memory, xmx, n,
            )
            n = by_memory
    return n


def summarize_validation(report):
    """Parse an eslint-compact report into (total_errors, [(template, count)]).

    Each error message is reduced to a template by replacing its variable
    parts (quoted values, URLs, numbers) so similar errors group together,
    e.g. many "Unknown code 'X' in the CodeSystem 'Y'" collapse into one row.
    """
    counts = collections.Counter()
    for line in report.splitlines():
        match = _ERROR_LINE.search(line)
        if match:
            counts[_categorize(match.group(1))] += 1
    return sum(counts.values()), counts.most_common()


def _categorize(message):
    m = re.sub(r"'[^']*'", "'_'", message)
    m = re.sub(r'"[^"]*"', '"_"', m)
    m = re.sub(r"https?://[^\s)]+", "_url_", m)
    m = re.sub(r"\d+", "N", m)
    return re.sub(r"\s+", " ", m).strip()


def _link(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)          # cheap; falls back to copy across filesystems
    except OSError:
        shutil.copy2(src, dst)


def run_validator_sharded(
    staged_dir, ig_package, validator_jar=None, java="java",
    shards=None, tx_cache_root=None, xmx="2g", html_output=None,
):
    """Validate every resource under staged_dir with N parallel processes and
    return the merged eslint-compact report text.

    staged_dir holds one file per resource at {Type}/{id}.json. Files are split
    into N shard directories (flat, since the validator does not recurse into
    subdirectories), each validated by its own process with its own terminology
    cache (the validator's cache has no cross-process lock). Shard paths in the
    report are normalized to {Type}/{id}.json. Raises if any process fails to
    run (exit code other than 0 = clean or 1 = issues).

    html_output forces a single validator process (the validator writes one
    aggregate HTML report that cannot be merged across shards) and writes the
    HTML to that path in addition to returning the eslint-compact text.
    """
    staged_dir = Path(staged_dir).resolve()
    shard_root = staged_dir / "_shards"
    tx_root = (
        Path(tx_cache_root).resolve() if tx_cache_root else staged_dir / "_txcache"
    )
    # A tx cache at or above the staged directory would exclude every resource
    # from the file glob below and silently validate nothing.
    if tx_root == staged_dir or tx_root in staged_dir.parents:
        raise ValueError(
            f"--tx-cache ({tx_root}) must not equal or contain the resource directory ({staged_dir})"
        )
    shutil.rmtree(shard_root, ignore_errors=True)
    files = sorted(
        path
        for path in staged_dir.rglob("*.json")
        if shard_root not in path.parents and tx_root not in path.parents
    )
    if not files:
        log.warning("validation: no resources staged; nothing to validate")
        return ""

    n = shards or shard_count(xmx=xmx)
    n = max(1, min(n, len(files)))
    if html_output and n > 1:
        log.info("validation: HTML output requires a single validator; ignoring shard count")
        n = 1
    how = "single process (--html)" if html_output else (
        f"--shards {shards}" if shards else f"auto ({n}, capped by cores and memory)"
    )
    log.info(
        "validation: %d resources across %d shard(s) [%s], java heap %s per shard",
        len(files), n, how, xmx,
    )
    heap = _bytes(xmx)
    available = _available_memory()
    if heap and available and heap * n > available:
        log.warning(
            "validation: requested heap %s across %d shard(s) exceeds available memory %s",
            _gib(heap * n), n, _gib(available),
        )

    # Interleave rather than slice contiguously: validation cost varies wildly
    # by type (multi-address Organizations run 10-20x slower than Endpoints),
    # and sorted order clusters each type, so contiguous slices give one shard
    # most of the expensive resources and the others idle time.
    groups = [files[k::n] for k in range(n)]
    report_paths = {}
    for k, group in enumerate(groups):
        shard_dir = shard_root / f"shard-{k}"
        # Flat layout: the validator reads files in the given directory but not
        # in subdirectories. Filenames encode the original {Type}/{id}.json.
        for path in group:
            rel = path.relative_to(staged_dir)
            shard_path = shard_dir / "__".join(rel.parts)
            report_paths[(k, shard_path.name)] = rel.as_posix()
            _link(path, shard_path)
        log.info("  shard %d/%d: %d resources -> %s", k + 1, n, len(group), shard_dir.name)

    def run_shard(k):
        shard_dir = shard_root / f"shard-{k}"
        out_file = shard_root / f"shard-{k}.txt"
        tx_dir = tx_root / f"shard-{k}"
        tx_dir.mkdir(parents=True, exist_ok=True)
        command = [
            java, f"-Xmx{xmx}", "-jar", str(validator_jar),
            "-version", "4.0.1", "-ig", str(ig_package),
            "-allow-example-urls", "true", "-txCache", str(tx_dir),
            "-level", "error",
            "-output-style", "eslint-compact", "-output", str(out_file),
        ]
        if html_output:
            command += ["-html-output", str(html_output)]
        command.append(str(shard_dir))
        log.info("  shard %d/%d starting (txCache %s)", k + 1, n, tx_dir)
        start = time.monotonic()
        completed = subprocess.run(command, capture_output=True, text=True)
        text = out_file.read_text() if out_file.exists() else ""
        errors = sum(1 for line in text.splitlines() if _ERROR_LINE.search(line))
        log.info(
            "  shard %d/%d done in %.0fs (exit %d, %d error/fatal lines)",
            k + 1, n, time.monotonic() - start, completed.returncode, errors,
        )
        return k, completed.returncode, completed.stdout + completed.stderr, text

    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(run_shard, range(n)))
    log.info("validation: %d shard(s) complete in %.0fs", n, time.monotonic() - start)

    merged = []
    for k, returncode, console, text in results:
        # 0 = no issues, 1 = validation issues found; anything else is a failure.
        if returncode not in (0, 1) or (returncode == 1 and not _ERROR_LINE.search(text)):
            raise RuntimeError(f"validator shard {k} failed (exit {returncode}):\n{console[-4000:]}")
        prefix = str(shard_root / f"shard-{k}")
        for line in text.splitlines():
            if line.startswith(prefix):
                line = line[len(prefix):].lstrip("/\\")
            name, sep, rest = line.partition(":")
            if sep:
                line = f"{report_paths.get((k, name), name)}:{rest}"
            merged.append(line)
    return "\n".join(merged) + ("\n" if merged else "")

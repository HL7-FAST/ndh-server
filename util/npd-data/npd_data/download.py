"""Release discovery and resumable downloads from directory.cms.gov.

- /downloads/manifest.json is the file inventory: a dict of
  {Type}_{date}_{time}.ndjson names to size info; the actual downloads are
  the zstd-compressed .ndjson.zst variants and compressed_bytes is their
  size.
- /downloads/{file} redirects to a presigned S3 URL valid for one hour; the
  redirect is re-followed on every attempt and never cached. HEAD is
  rejected; Range GETs work.
- No checksums are published, so integrity checking is size-based.
"""

import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://directory.cms.gov"


def fetch_release():
    """Fetch the manifest, normalized to the entry shape download_file expects.

    release_date comes from the timestamp embedded in the filenames since the
    manifest no longer carries one.
    """
    response = requests.get(DEFAULT_BASE_URL + "/downloads/manifest.json", timeout=30)
    response.raise_for_status()
    manifest = response.json()
    files = []
    release_date = "unknown"
    for name, info in sorted(manifest.get("files", {}).items()):
        stem, _, _ = name.partition(".")
        resource_name, _, release_date = stem.partition("_")
        files.append(
            {
                "resource_name": resource_name,
                "filename": name + ".zst",
                "download_path": "/downloads/" + name + ".zst",
                "compressed_bytes": info.get("compressed_bytes"),
            }
        )
    return {"release_date": release_date, "files": files}


def download_file(entry, cache_dir):
    """Download one release file into cache_dir, resuming partial transfers.

    A complete, size-verified cached file short-circuits without any network
    traffic. Partial downloads live at {filename}.part and resume via Range
    requests against a freshly issued redirect.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    expected = entry.get("compressed_bytes")
    final_path = cache_dir / entry["filename"]
    if final_path.exists():
        if expected is None or final_path.stat().st_size == expected:
            return final_path
        log.warning("cached %s has unexpected size; re-downloading", final_path.name)
        final_path.unlink()

    part_path = cache_dir / (entry["filename"] + ".part")
    if expected is not None and part_path.exists():
        part_size = part_path.stat().st_size
        if part_size == expected:
            part_path.rename(final_path)
            return final_path
        if part_size > expected:
            log.warning("partial %s has unexpected size; re-downloading", part_path.name)
            part_path.unlink()

    attempts = 0
    while True:
        offset = part_path.stat().st_size if part_path.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            response = requests.get(
                DEFAULT_BASE_URL + entry["download_path"], headers=headers, stream=True, timeout=60
            )
            response.raise_for_status()
            # A 200 despite a Range request means the server restarted the body.
            mode = "ab" if offset and response.status_code == 206 else "wb"
            with open(part_path, mode) as fh:
                for chunk in response.iter_content(1 << 20):
                    fh.write(chunk)
            break
        except (requests.RequestException, OSError) as exc:
            attempts += 1
            if attempts >= 5:
                raise
            log.warning("download of %s interrupted (%s); resuming", entry["filename"], exc)

    size = part_path.stat().st_size
    if expected is not None and size != expected:
        raise ValueError(
            f"size mismatch for {entry['filename']}: downloaded {size}, manifest says {expected}"
        )
    part_path.rename(final_path)
    log.info("downloaded %s (%d bytes)", final_path.name, size)
    return final_path

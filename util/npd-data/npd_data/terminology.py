"""Load code-system displays for display correction.

Package code systems (FaCeT, NDH) come from the package tgz. NUCC is external,
so its code system is fetched once from tx.fhir.org (the same source the
validator checks against) and cached to disk.
"""

import json
import logging
import tarfile
from pathlib import Path

import requests

log = logging.getLogger(__name__)

NUCC_SYSTEM = "http://nucc.org/provider-taxonomy"
_TX_CODESYSTEM = "https://tx.fhir.org/r4/CodeSystem"


def load_display_map(package_path):
    """Return {(system, code): display} for every concept in the package's
    CodeSystem resources that defines a display.
    """
    displays = {}
    with tarfile.open(package_path, "r:gz") as tar:
        for member in tar.getmembers():
            if "/CodeSystem-" not in member.name or not member.name.endswith(".json"):
                continue
            cs = json.load(tar.extractfile(member))
            system = cs.get("url")
            if not system:
                continue
            for concept in _walk_concepts(cs.get("concept", [])):
                code, display = concept.get("code"), concept.get("display")
                if code and display:
                    displays[(system, code)] = display
    return displays


def _walk_concepts(concepts):
    for concept in concepts:
        yield concept
        yield from _walk_concepts(concept.get("concept", []))


def collect_codes(resources, system):
    """Return the set of codes used on the given coding system across resources."""
    codes = set()
    stack = list(resources)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("system") == system and node.get("code"):
                codes.add(node["code"])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return codes


def fetch_nucc_displays(cache_path=None):
    """Return {(NUCC_SYSTEM, code): display} for all of NUCC.

    The whole code system is fetched from tx.fhir.org in a single request and
    cached to cache_path (JSON), so a run makes at most one tx call rather than
    one per code. Returns {} if the fetch fails (displays left uncorrected).
    """
    cache = None
    if cache_path and Path(cache_path).exists():
        cache = json.loads(Path(cache_path).read_text())
    if cache is None:
        cache = _fetch_nucc_codesystem()
        if cache and cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(json.dumps(cache, sort_keys=True))
    return {(NUCC_SYSTEM, code): display for code, display in (cache or {}).items()}


def _fetch_nucc_codesystem():
    """Fetch the full NUCC CodeSystem from tx.fhir.org as {code: display}."""
    log.info("fetching the NUCC code system from tx.fhir.org (one request)")
    try:
        response = requests.get(
            _TX_CODESYSTEM,
            params={"url": NUCC_SYSTEM, "_format": "json"},
            headers={"Accept": "application/fhir+json"},
            timeout=60,
        )
        response.raise_for_status()
        for entry in response.json().get("entry", []):
            cs = entry.get("resource", {})
            if cs.get("resourceType") == "CodeSystem" and cs.get("concept"):
                displays = {
                    c["code"]: c["display"]
                    for c in _walk_concepts(cs["concept"])
                    if c.get("code") and c.get("display")
                }
                if displays:
                    return displays
    except requests.RequestException as exc:
        log.warning("could not fetch NUCC from tx.fhir.org (%s); NUCC displays left as-is", exc)
        return {}
    log.warning("tx.fhir.org returned no NUCC concepts; NUCC displays left as-is")
    return {}

"""NDH 2.0 conversion rules for CMS NPD records.

Each rule mutates a resource in place and returns it. transform_resource()
applies the full rule set to a deep copy, so callers' inputs are never
mutated.
"""

import copy

from .constants import (
    ACCEPTING_PATIENTS_SYSTEM,
    ACTIVE_FLAG_TYPES,
    EXT_ENDPOINT_REFERENCE,
    EXT_NEWPATIENTS,
    EXTENSION_URL_DROPS,
    EXTENSION_URL_REMAPS,
    NPI_SYSTEM_CMS,
    NPI_SYSTEM_STANDARD,
    ndh_profile,
)

_REMOVE = object()


def strip_empty_arrays(resource):
    """Remove empty arrays and objects bottom-up; they are invalid in FHIR JSON."""
    _strip_empties(resource)
    return resource


def _strip_empties(node):
    if isinstance(node, dict):
        for key in list(node):
            _strip_empties(node[key])
            if node[key] == [] or node[key] == {}:
                del node[key]
    elif isinstance(node, list):
        for item in node:
            _strip_empties(item)
        node[:] = [item for item in node if item != [] and item != {}]


def fix_telecom_use(resource):
    """Rewrite the invalid ContactPointUse code "practice" to "work"."""
    _walk_dicts(resource, _fix_practice_use)
    return resource


def _fix_practice_use(node):
    if node.get("use") == "practice":
        node["use"] = "work"


def fix_npi_system(resource):
    """Rewrite the nonstandard NPI system URI to the standard one."""
    _walk_dicts(resource, _fix_npi)
    return resource


def _fix_npi(node):
    if node.get("system") == NPI_SYSTEM_CMS:
        node["system"] = NPI_SYSTEM_STANDARD


def remap_extensions(resource):
    """Rename CMS extension URLs to their NDH equivalents; drop the unmappable ones."""
    _walk_dicts(resource, _remap_extension_list)
    return resource


def _remap_extension_list(node):
    extensions = node.get("extension")
    if not isinstance(extensions, list):
        return
    kept = []
    for ext in extensions:
        url = ext.get("url") if isinstance(ext, dict) else None
        if url in EXTENSION_URL_DROPS:
            continue
        if url in EXTENSION_URL_REMAPS:
            ext["url"] = EXTENSION_URL_REMAPS[url]
        kept.append(ext)
    if kept:
        node["extension"] = kept
    else:
        node.pop("extension", None)


def flatten_endpoint_reference(resource):
    """Convert the complex endpoint-reference extension form to NDH's simple
    valueReference form, ordered by rank (rank itself is dropped)."""
    extensions = resource.get("extension")
    if not isinstance(extensions, list):
        return resource
    others = []
    endpoint_refs = []
    first_index = None
    for index, ext in enumerate(extensions):
        if isinstance(ext, dict) and ext.get("url") == EXT_ENDPOINT_REFERENCE and "extension" in ext:
            if first_index is None:
                first_index = len(others)
            rank = None
            reference = None
            for sub in ext["extension"]:
                if sub.get("url") == "endpoint":
                    reference = sub.get("valueReference")
                elif sub.get("url") == "rank":
                    rank = sub.get("valuePositiveInt")
            if reference is not None:
                endpoint_refs.append((rank if rank is not None else 1 << 30, reference))
        else:
            others.append(ext)
    if first_index is None:
        return resource
    flattened = [
        {"url": EXT_ENDPOINT_REFERENCE, "valueReference": reference}
        for _, reference in sorted(endpoint_refs, key=lambda pair: pair[0])
    ]
    resource["extension"] = others[:first_index] + flattened + others[first_index:]
    return resource


def expand_newpatients(resource):
    """Expand a bare-boolean newpatients extension into NDH's complex form."""
    for ext in resource.get("extension", []):
        if isinstance(ext, dict) and ext.get("url") == EXT_NEWPATIENTS and "valueBoolean" in ext:
            accepting = ext.pop("valueBoolean")
            ext["extension"] = [
                {
                    "url": "acceptingPatients",
                    "valueCodeableConcept": {
                        "coding": [
                            {
                                "system": ACCEPTING_PATIENTS_SYSTEM,
                                "code": "newpt" if accepting else "nopt",
                            }
                        ]
                    },
                }
            ]
    return resource


def ensure_location_name(resource):
    """Location.name is required in NDH; derive it from the address when absent."""
    if resource.get("resourceType") != "Location" or resource.get("name"):
        return resource
    address = resource.get("address") or {}
    lines = address.get("line") or []
    resource["name"] = lines[0] if lines else address.get("city", "Location")
    return resource


def ensure_active_flags(resource):
    """Add active=true where NDH requires it and the record lacks it."""
    if resource.get("resourceType") in ACTIVE_FLAG_TYPES and "active" not in resource:
        resource["active"] = True
    return resource


def inject_profile(resource):
    meta = resource.setdefault("meta", {})
    meta["profile"] = [ndh_profile(resource["resourceType"])]
    return resource


def correct_displays(resource, display_map):
    """Replace coding displays with the code system's value where known.

    display_map is {(system, code): display}. Codings whose system/code is
    not in the map (e.g. external NUCC, or invalid codes) are left untouched.
    """
    _walk_dicts(resource, lambda node: _correct_displays(node, display_map))
    return resource


def _correct_displays(node, display_map):
    codings = node.get("coding")
    if not isinstance(codings, list):
        return
    for coding in codings:
        if isinstance(coding, dict):
            display = display_map.get((coding.get("system"), coding.get("code")))
            if display is not None:
                coding["display"] = display


_RULES = [
    strip_empty_arrays,
    fix_telecom_use,
    fix_npi_system,
    remap_extensions,
    flatten_endpoint_reference,
    expand_newpatients,
    ensure_location_name,
    ensure_active_flags,
    inject_profile,
]


def transform_resource(resource, display_map=None):
    out = copy.deepcopy(resource)
    for rule in _RULES:
        out = rule(out)
    if display_map:
        correct_displays(out, display_map)
    return out


def strip_unresolved(resource, kept_refs):
    """Remove every reference whose target is outside the kept set, in place.

    Containers emptied by a removal (arrays, or extension entries left with
    only a url) are removed too.
    """
    _prune(resource, kept_refs, is_root=True)
    return resource


def _prune(node, kept_refs, is_root=False):
    if isinstance(node, dict):
        reference = node.get("reference")
        if not is_root and isinstance(reference, str) and reference not in kept_refs:
            return _REMOVE
        removed_any = False
        for key in list(node):
            if _prune(node[key], kept_refs) is _REMOVE:
                del node[key]
                removed_any = True
        # Collapse a container only if our removal emptied it; leave
        # pre-existing empties untouched so raw output stays faithful.
        if not is_root and removed_any and (not node or set(node) == {"url"}):
            return _REMOVE
        return node
    if isinstance(node, list):
        had_items = bool(node)
        node[:] = [item for item in node if _prune(item, kept_refs) is not _REMOVE]
        if had_items and not node:
            return _REMOVE
        return node
    return node


def _walk_dicts(node, visit):
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            _walk_dicts(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk_dicts(item, visit)

"""Keeps the records in the target city and pulls in everything they reference.

Each kept record has a role:
- anchored: a Practitioner, Organization, or Location whose address is in the city.
- related: a PractitionerRole or OrganizationAffiliation that links to an anchor.
- boundary: a record pulled in only to satisfy a reference from a kept record;
  it follows only the integrity-critical edges (partOf, managingOrganization,
  qualification.issuer).

References that cannot be kept (nonconformant, depth-capped, or missing from the
source files) are recorded in dropped_targets for later stripping.
"""

import logging
from dataclasses import dataclass, field

from .addresses import AnchorMatcher
from .constants import EXT_ENDPOINT_REFERENCE
from .conformance import conformance_keep
from .ndjson import iter_ndjson, loads

log = logging.getLogger(__name__)

ANCHOR_TYPES = ("Practitioner", "Organization", "Location")
RELATION_TYPES = ("PractitionerRole", "OrganizationAffiliation")


class BudgetExceeded(Exception):
    pass


@dataclass
class SubsetConfig:
    cities: list
    state: str
    max_resources: int = 15000
    partof_depth_cap: int = 3


@dataclass
class SubsetResult:
    kept: dict
    roles: dict
    dropped_targets: set
    stats: dict = field(default_factory=dict)


def run_subset(sources, config):
    matcher = AnchorMatcher(config.cities, config.state)
    kept = {}
    roles = {}
    dropped = set()
    capped = set()

    def check_budget():
        if len(kept) > config.max_resources:
            counts = _counts_by_type(kept)
            raise BudgetExceeded(
                f"subset grew to {len(kept)} resources "
                f"(limit {config.max_resources}); per-type: {counts}"
            )

    for resource_type in ANCHOR_TYPES:
        for path in sources.get(resource_type, []):
            for line in iter_ndjson(path, raw=True):
                if not matcher.line_might_match(line.lower()):
                    continue
                resource = loads(line)
                if not conformance_keep(resource):
                    continue
                if matcher.matches(resource):
                    ref = _ref(resource)
                    kept[ref] = resource
                    roles[ref] = "anchored"
        check_budget()
    anchored = set(kept)
    log.info("anchored %d resources for %s, %s", len(anchored), config.cities, config.state)

    for resource_type in RELATION_TYPES:
        for path in sources.get(resource_type, []):
            for resource in iter_ndjson(path):
                if not conformance_keep(resource):
                    continue
                if any(target in anchored for target in _relation_refs(resource)):
                    ref = _ref(resource)
                    kept[ref] = resource
                    roles[ref] = "related"
        check_budget()
    log.info("relation selection kept %d resources total", len(kept))

    # wanted: type -> {ref: partOf depth}; depth counts Organization.partOf
    # hops only, every other edge resets to 0.
    wanted = {}

    def add_wanted(target_ref, depth):
        if target_ref in kept or target_ref in dropped:
            return
        target_type = target_ref.split("/", 1)[0]
        per_type = wanted.setdefault(target_type, {})
        if target_ref not in per_type or depth < per_type[target_ref]:
            per_type[target_ref] = depth

    def chase(resource, role, own_depth):
        for target_ref, via_partof in _chase_edges(resource, role):
            depth = own_depth + 1 if via_partof else 0
            if via_partof and depth > config.partof_depth_cap:
                log.warning(
                    "partOf chain from %s exceeds depth cap %d; %s will be stripped",
                    _ref(resource), config.partof_depth_cap, target_ref,
                )
                capped.add(target_ref)
                continue
            add_wanted(target_ref, depth)

    for ref, resource in list(kept.items()):
        chase(resource, roles[ref], 0)

    while wanted:
        current, wanted = wanted, {}
        for resource_type, targets in current.items():
            remaining = dict(targets)
            for path in sources.get(resource_type, []):
                if not remaining:
                    break
                for resource in iter_ndjson(path):
                    ref = _ref(resource)
                    if ref not in remaining:
                        continue
                    depth = remaining.pop(ref)
                    if conformance_keep(resource):
                        kept[ref] = resource
                        roles[ref] = "boundary"
                        chase(resource, "boundary", depth)
                    else:
                        dropped.add(ref)
                    if not remaining:
                        break
            for ref in remaining:
                log.warning("reference target %s not found in source files", ref)
                dropped.add(ref)
        check_budget()

    dropped_targets = dropped | (capped - set(kept))
    stats = {
        "total": len(kept),
        "by_type": _counts_by_type(kept),
        "by_role": _counts_by_role(roles),
        "dropped_targets": len(dropped_targets),
    }
    log.info("subset complete: %s", stats)
    return SubsetResult(kept=kept, roles=roles, dropped_targets=dropped_targets, stats=stats)


def _ref(resource):
    return f"{resource['resourceType']}/{resource['id']}"


def _refs_under(resource, keys):
    refs = []
    for key in keys:
        value = resource.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("reference"), str):
                refs.append(item["reference"])
    return refs


def _issuer_refs(resource):
    return [
        qualification["issuer"]["reference"]
        for qualification in resource.get("qualification", [])
        if isinstance(qualification.get("issuer"), dict)
        and isinstance(qualification["issuer"].get("reference"), str)
    ]


def _endpoint_extension_refs(resource):
    refs = []
    for ext in resource.get("extension", []):
        if not isinstance(ext, dict) or ext.get("url") != EXT_ENDPOINT_REFERENCE:
            continue
        value = ext.get("valueReference")
        if isinstance(value, dict) and isinstance(value.get("reference"), str):
            refs.append(value["reference"])
        for sub in ext.get("extension", []):
            sub_value = sub.get("valueReference") if sub.get("url") == "endpoint" else None
            if isinstance(sub_value, dict) and isinstance(sub_value.get("reference"), str):
                refs.append(sub_value["reference"])
    return refs


def _relation_refs(resource):
    """References on a relation row that must point at an anchor to keep the row.

    A PractitionerRole qualifies through its practitioner or locations; an
    OrganizationAffiliation through its participating organization.
    """
    # A role is kept via its practitioner or location, not its organization:
    # a city-registered org would otherwise pull in all of its roles, including
    # out-of-city ones. The org itself is still kept if its own address is in
    # the city.
    if resource.get("resourceType") == "PractitionerRole":
        return _refs_under(resource, ["practitioner", "location"])
    return _refs_under(resource, ["participatingOrganization"])


def _chase_edges(resource, role):
    """Return (target_ref, via_partof) edges this role may follow."""
    resource_type = resource.get("resourceType")
    edges = []
    if role in ("anchored", "related"):
        if resource_type == "Practitioner":
            edges += [(ref, False) for ref in _issuer_refs(resource)]
            edges += [(ref, False) for ref in _endpoint_extension_refs(resource)]
        elif resource_type == "Organization":
            edges += [(ref, True) for ref in _refs_under(resource, ["partOf"])]
            edges += [(ref, False) for ref in _refs_under(resource, ["endpoint"])]
            edges += [(ref, False) for ref in _endpoint_extension_refs(resource)]
        elif resource_type == "Location":
            edges += [
                (ref, False)
                for ref in _refs_under(resource, ["managingOrganization", "partOf", "endpoint"])
            ]
        elif resource_type == "PractitionerRole":
            edges += [
                (ref, False)
                for ref in _refs_under(resource, ["practitioner", "organization", "endpoint"])
            ]
        elif resource_type == "OrganizationAffiliation":
            edges += [
                (ref, False)
                for ref in _refs_under(resource, ["organization", "participatingOrganization"])
            ]
    else:  # boundary: integrity-critical edges only
        if resource_type == "Organization":
            edges += [(ref, True) for ref in _refs_under(resource, ["partOf"])]
        elif resource_type == "Location":
            edges += [
                (ref, False) for ref in _refs_under(resource, ["managingOrganization", "partOf"])
            ]
        elif resource_type == "Endpoint":
            edges += [(ref, False) for ref in _refs_under(resource, ["managingOrganization"])]
        elif resource_type == "Practitioner":
            edges += [(ref, False) for ref in _issuer_refs(resource)]
    return edges


def _counts_by_type(kept):
    counts = {}
    for ref in kept:
        resource_type = ref.split("/", 1)[0]
        counts[resource_type] = counts.get(resource_type, 0) + 1
    return dict(sorted(counts.items()))


def _counts_by_role(roles):
    counts = {}
    for role in roles.values():
        counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items()))

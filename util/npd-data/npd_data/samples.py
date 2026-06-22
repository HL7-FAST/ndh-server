"""Sample resources for the NDH profile types absent from the CMS data:
Network, HealthcareService, InsurancePlan, VerificationResult, and Group,
wired to the kept resources.

Output is deterministic: ids derive from the geography slug, members and
targets are picked in sorted order, and dates are fixed constants.
"""

import base64
import json
import re
from dataclasses import dataclass

from .constants import (
    EXT_LOCATION_REFERENCE,
    EXT_NETWORK_REFERENCE,
    EXT_NEWPATIENTS,
    EXT_VERIFICATION_STATUS,
    ACCEPTING_PATIENTS_SYSTEM,
    VERIFICATION_STATUS_CONTEXT_TYPES,
    VERIFICATION_STATUS_SYSTEM,
    ndh_profile,
)

GEOJSON_BOUNDARY_EXT = "http://hl7.org/fhir/StructureDefinition/location-boundary-geojson"
SERVICE_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/ndh-healthcare-service-category"


@dataclass
class SamplesConfig:
    cities: list
    state: str


# Fixed counts and timestamps; constant so regenerated output is byte-identical.
NETWORKS = 2
SERVICES = 5
PLANS = 2
VERIFICATIONS = 5
GROUP_MEMBERS = 10
LINKED_ROLES = 10
STATUS_DATE = "2026-01-01T00:00:00Z"
ATTESTATION_DATE = "2026-01-01"


def build_samples(kept, roles, config):
    """Return sample resources wired into the kept graph.

    Also mutates kept resources in place: verification-status stamping and
    sample PractitionerRole wiring.
    """
    slug = _slug(config)
    city_label = config.cities[0].title()

    anchored_orgs = _sorted_refs(roles, "Organization/", role="anchored")
    practitioner_refs = _sorted_refs(roles, "Practitioner/")
    new_resources = []

    payer = _payer_organization(slug, city_label)
    new_resources.append(payer)

    coverage = _coverage_location(slug, city_label, config.state, kept)
    new_resources.append(coverage)

    networks = [
        _network(slug, city_label, index, payer, coverage)
        for index in range(1, NETWORKS + 1)
    ]
    new_resources.extend(networks)

    services = []
    for index, org_ref in enumerate(anchored_orgs[:SERVICES], start=1):
        network = networks[(index - 1) % len(networks)]
        services.append(_healthcare_service(slug, index, org_ref, kept, network))
    new_resources.extend(services)

    new_resources.extend(
        _insurance_plan(slug, city_label, index, payer, coverage, networks)
        for index in range(1, PLANS + 1)
    )

    verification_targets = (anchored_orgs + [r for r in practitioner_refs if roles.get(r) == "anchored"])
    new_resources.extend(
        _verification(slug, index, target_ref)
        for index, target_ref in enumerate(verification_targets[:VERIFICATIONS], start=1)
    )

    if anchored_orgs and practitioner_refs:
        new_resources.append(
            _group(slug, city_label, anchored_orgs[0], practitioner_refs[:GROUP_MEMBERS])
        )

    _wire_roles(kept, services, networks, LINKED_ROLES)

    for resource in list(kept.values()) + new_resources:
        _stamp_verification_status(resource)

    return new_resources


def _slug(config):
    return re.sub(r"[^a-z0-9]+", "-", f"{config.cities[0]} {config.state}".lower()).strip("-")


def _sorted_refs(roles, prefix, role=None):
    return sorted(
        ref for ref, r in roles.items() if ref.startswith(prefix) and (role is None or r == role)
    )


def _meta(profile_name):
    """NDH profiles require meta.lastUpdated (min=1)."""
    return {"profile": [ndh_profile(profile_name)], "lastUpdated": STATUS_DATE}


def _stamp_verification_status(resource):
    if resource.get("resourceType") not in VERIFICATION_STATUS_CONTEXT_TYPES:
        return
    extensions = resource.setdefault("extension", [])
    if any(e.get("url") == EXT_VERIFICATION_STATUS for e in extensions):
        return
    extensions.append(
        {
            "url": EXT_VERIFICATION_STATUS,
            "valueCodeableConcept": {
                "coding": [
                    {"system": VERIFICATION_STATUS_SYSTEM, "code": "complete", "display": "Complete"}
                ]
            },
        }
    )


def _payer_organization(slug, city_label):
    return {
        "resourceType": "Organization",
        "id": f"Organization-npd-payer-{slug}",
        "meta": _meta("Organization"),
        "active": True,
        "type": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                        "code": "pay",
                        "display": "Payer",
                    }
                ]
            }
        ],
        "name": f"{city_label} Health Plans (Sample)",
    }


def _coverage_location(slug, city_label, state, kept):
    location = {
        "resourceType": "Location",
        "id": f"Location-npd-coverage-{slug}",
        "meta": _meta("Location"),
        "status": "active",
        "name": f"{city_label}, {state} coverage area",
        "address": {"city": city_label, "state": state},
    }
    boundary = _boundary_polygon(kept)
    if boundary:
        location["extension"] = [
            {
                "url": GEOJSON_BOUNDARY_EXT,
                "valueAttachment": {
                    "contentType": "application/geo+json",
                    "data": base64.b64encode(
                        json.dumps(boundary, sort_keys=True, separators=(",", ":")).encode()
                    ).decode(),
                },
            }
        ]
    return location


def _boundary_polygon(kept):
    """Bounding box around the kept Locations' geocodes, as a GeoJSON Polygon."""
    longitudes = []
    latitudes = []
    for resource in kept.values():
        position = resource.get("position")
        if resource.get("resourceType") != "Location" or not isinstance(position, dict):
            continue
        lon, lat = position.get("longitude"), position.get("latitude")
        if lon is not None and lat is not None:
            longitudes.append(lon)
            latitudes.append(lat)
    if not longitudes:
        return None
    west, east = min(longitudes) - 0.05, max(longitudes) + 0.05
    south, north = min(latitudes) - 0.05, max(latitudes) + 0.05
    ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    return {"type": "Polygon", "coordinates": [ring]}


def _network(slug, city_label, index, payer, coverage):
    return {
        "resourceType": "Organization",
        "id": f"Organization-npd-network-{slug}-{index}",
        "meta": _meta("Network"),
        "extension": [
            {
                "url": EXT_LOCATION_REFERENCE,
                "valueReference": {"reference": f"Location/{coverage['id']}"},
            }
        ],
        "active": True,
        "type": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                        # ntwk only exists in CodeSystem version 2.0.1 and later.
                        "version": "2.0.1",
                        "code": "ntwk",
                        "display": "Network",
                    }
                ]
            }
        ],
        "name": f"{city_label} Provider Network {index} (Sample)",
        "partOf": {"reference": f"Organization/{payer['id']}"},
    }


def _healthcare_service(slug, index, org_ref, kept, network):
    organization = kept[org_ref]
    service = {
        "resourceType": "HealthcareService",
        "id": f"HealthcareService-npd-{slug}-{index}",
        "meta": _meta("HealthcareService"),
        "extension": [
            {
                "url": EXT_NETWORK_REFERENCE,
                "valueReference": {"reference": f"Organization/{network['id']}"},
            },
            {
                "url": EXT_NEWPATIENTS,
                "extension": [
                    {
                        "url": "acceptingPatients",
                        "valueCodeableConcept": {
                            "coding": [{"system": ACCEPTING_PATIENTS_SYSTEM, "code": "newpt"}]
                        },
                    }
                ],
            },
        ],
        "active": True,
        "providedBy": {"reference": org_ref},
        "category": [{"coding": [{"system": SERVICE_CATEGORY_SYSTEM, "code": "prov"}]}],
        "name": f"Provider services - {organization.get('name', org_ref)} (Sample)",
    }
    locations = [
        ref
        for ref, resource in sorted(kept.items())
        if resource.get("resourceType") == "Location"
        and resource.get("managingOrganization", {}).get("reference") == org_ref
    ]
    if locations:
        service["location"] = [{"reference": ref} for ref in locations]
    return service


def _insurance_plan(slug, city_label, index, payer, coverage, networks):
    tiers = ["bronze", "gold", "silver", "platinum"]
    tier = tiers[(index - 1) % len(tiers)]
    network = networks[(index - 1) % len(networks)]
    return {
        "resourceType": "InsurancePlan",
        "id": f"InsurancePlan-npd-{slug}-{index}",
        "meta": _meta("InsurancePlan"),
        "status": "active",
        "type": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/insurance-product-type",
                        "code": "qhp",
                        "display": "Qualified Health Plan",
                    }
                ]
            }
        ],
        "name": f"{city_label} Sample QHP {tier.title()}",
        "ownedBy": {"reference": f"Organization/{payer['id']}"},
        "administeredBy": {"reference": f"Organization/{payer['id']}"},
        "coverageArea": [{"reference": f"Location/{coverage['id']}"}],
        "network": [{"reference": f"Organization/{network['id']}"}],
        "plan": [
            {
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/insuranceplan-plan-type",
                            "code": tier,
                        }
                    ]
                }
            }
        ],
    }


def _verification(slug, index, target_ref):
    return {
        "resourceType": "VerificationResult",
        "id": f"VerificationResult-npd-{slug}-{index}",
        "meta": _meta("Verification"),
        "target": [{"reference": target_ref}],
        "need": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/need", "code": "periodic"}]
        },
        "status": "attested",
        "statusDate": STATUS_DATE,
        "validationType": {
            "coding": [
                {"system": "http://terminology.hl7.org/CodeSystem/validation-type", "code": "primary"}
            ]
        },
        # validationProcess and failureAction are min=1 in ndh-Verification.
        "validationProcess": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/validation-process",
                        "code": "in-context",
                    }
                ]
            }
        ],
        "failureAction": {
            "coding": [
                {"system": "http://terminology.hl7.org/CodeSystem/failure-action", "code": "none"}
            ]
        },
        "attestation": {
            "who": {"reference": target_ref},
            "communicationMethod": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/verificationresult-communication-method",
                        "code": "manual",
                    }
                ]
            },
            "date": ATTESTATION_DATE,
        },
    }


def _group(slug, city_label, org_ref, practitioner_refs):
    return {
        "resourceType": "Group",
        "id": f"Group-npd-{slug}",
        "meta": _meta("Group"),
        "active": True,
        "type": "practitioner",
        "actual": True,
        "code": {"coding": [{"system": SERVICE_CATEGORY_SYSTEM, "code": "prov"}]},
        "name": f"{city_label} Multi-Disciplinary Provider Group (Sample)",
        "managingEntity": {"reference": org_ref},
        "member": [{"entity": {"reference": ref}} for ref in practitioner_refs],
    }


def _wire_roles(kept, services, networks, limit):
    """Point a sample of kept PractitionerRoles at sample services and networks."""
    if not services or not networks:
        return
    service_by_org = {service["providedBy"]["reference"]: service for service in services}
    wired = 0
    for ref in sorted(kept):
        if wired >= limit:
            break
        resource = kept[ref]
        if resource.get("resourceType") != "PractitionerRole":
            continue
        service = service_by_org.get(resource.get("organization", {}).get("reference"))
        if service is None:
            continue
        resource.setdefault("healthcareService", []).append(
            {"reference": f"HealthcareService/{service['id']}"}
        )
        resource.setdefault("extension", []).append(
            {
                "url": EXT_NETWORK_REFERENCE,
                "valueReference": {"reference": f"Organization/{networks[0]['id']}"},
            }
        )
        wired += 1

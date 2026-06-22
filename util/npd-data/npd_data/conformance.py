"""Conformance pre-filter.

NDH fixes active=true on Practitioner, PractitionerRole, Organization, and
OrganizationAffiliation, and status="active" on Location and Endpoint.
Records that violate this can never conform and are dropped.
"""

from .constants import ACTIVE_FLAG_TYPES, STATUS_FLAG_TYPES


def conformance_keep(resource):
    resource_type = resource.get("resourceType")
    if resource_type in ACTIVE_FLAG_TYPES:
        # Absent active is repaired to true during transform.
        return resource.get("active") is not False
    if resource_type in STATUS_FLAG_TYPES:
        return resource.get("status") == "active"
    return True

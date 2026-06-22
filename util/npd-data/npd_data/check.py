"""Referential integrity check: every reference in the output set must
resolve inside the set.
"""

from .references import iter_reference_values


def find_dangling_references(resources):
    kept = {f"{r['resourceType']}/{r['id']}" for r in resources}
    findings = []
    for resource in resources:
        source = f"{resource['resourceType']}/{resource['id']}"
        for ref in iter_reference_values(resource):
            if ref not in kept:
                findings.append((source, ref))
    return findings

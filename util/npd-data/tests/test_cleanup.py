from npd_data.cleanup import strip_unresolved


def test_strip_unresolved_returns_removed_references():
    resource = {
        "resourceType": "PractitionerRole",
        "id": "r1",
        "practitioner": {"reference": "Practitioner/kept"},
        "organization": {"reference": "Organization/gone"},
        "location": [
            {"reference": "Location/gone-1"},
            {"reference": "Location/gone-2"},
        ],
    }
    removed = strip_unresolved(resource, {"Practitioner/kept"})
    assert sorted(removed) == ["Location/gone-1", "Location/gone-2", "Organization/gone"]
    assert resource["practitioner"] == {"reference": "Practitioner/kept"}
    assert "organization" not in resource
    assert "location" not in resource


def test_strip_unresolved_no_unresolved_references():
    resource = {
        "resourceType": "PractitionerRole",
        "id": "r1",
        "practitioner": {"reference": "Practitioner/kept"},
    }
    assert strip_unresolved(resource, {"Practitioner/kept"}) == []

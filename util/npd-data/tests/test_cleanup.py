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


def test_remap_extensions():
    from npd_data.cleanup import remap_extensions
    from npd_data.constants import NDH_SD

    resource = {
        "resourceType": "Practitioner",
        "extension": [
            {"url": NDH_SD + "base-ext-cms-identity-verified", "valueBoolean": True},
            {"url": NDH_SD + "base-ext-cms_aligned_with_data_network", "valueBoolean": True},
            {"url": NDH_SD + "base-ext-cms_medicare_enrollment", "valueBoolean": True},
            {"url": NDH_SD + "base-ext-hhs-in-exclusion-list", "valueBoolean": False},
        ],
    }
    urls = [ext["url"] for ext in remap_extensions(resource)["extension"]]
    assert urls == [
        NDH_SD + "base-ext-cms-identity-verified",
        NDH_SD + "base-ext-cms-aligned-with-data-network",
        NDH_SD + "base-ext-cms-medicare-enrollment-in-good-standing",
        NDH_SD + "base-ext-hhs-exclusion-list",
    ]


def test_fix_coding_systems():
    from npd_data.cleanup import fix_coding_systems

    resource = {
        "resourceType": "Practitioner",
        "identifier": [{"system": "http://terminology.hl7.org/NamingSystem/npi", "value": "1"}],
        "qualification": [
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/us/ndh/ValueSet/HealthcareIndividualTaxonomyVS",
                            "code": "207Q00000X",
                        }
                    ]
                }
            }
        ],
    }
    fix_coding_systems(resource)
    assert resource["identifier"][0]["system"] == "http://hl7.org/fhir/sid/us-npi"
    assert resource["qualification"][0]["code"]["coding"][0]["system"] == "http://nucc.org/provider-taxonomy"


def test_fix_credential_codes():
    from npd_data.cleanup import fix_credential_codes

    system = "http://hl7.org/fhir/us/ndh/CodeSystem/FaCeT-credentialCS"
    codings = [
        {"system": system, "code": "CRN", "display": "Certified Registered Nurse"},
        {"system": system, "code": "MT", "display": "Medical Technician"},
        {"system": system, "code": "BT", "display": "Bachelor of Theology"},
        {"system": system, "code": "CRN", "display": "Certified Radiologic Nurse"},
        {"system": "http://nucc.org/provider-taxonomy", "code": "CRN", "display": "Certified Registered Nurse"},
    ]
    resource = {"resourceType": "Practitioner", "qualification": [{"code": {"coding": codings}}]}
    fix_credential_codes(resource)
    assert [c["code"] for c in codings] == ["CRN_2", "MT_2", "BT_2", "CRN", "CRN"]

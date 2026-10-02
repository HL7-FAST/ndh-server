"""Shared constants: canonical URLs, extension mappings, and resource type metadata."""

RESOURCE_TYPES = [
    "Endpoint",
    "Location",
    "Organization",
    "OrganizationAffiliation",
    "Practitioner",
    "PractitionerRole",
]

NDH_SD = "http://hl7.org/fhir/us/ndh/StructureDefinition/"

def ndh_profile(resource_type: str) -> str:
    return f"{NDH_SD}ndh-{resource_type}"

EXT_VERIFICATION_STATUS = NDH_SD + "base-ext-verification-status"
EXT_ENDPOINT_REFERENCE = NDH_SD + "base-ext-endpoint-reference"
EXT_NEWPATIENTS = NDH_SD + "base-ext-newpatients"
EXT_LOCATION_REFERENCE = NDH_SD + "base-ext-location-reference"
EXT_NETWORK_REFERENCE = NDH_SD + "base-ext-network-reference"

# CMS extension URLs with direct NDH equivalents.
EXTENSION_URL_REMAPS = {
    NDH_SD + "base-ext-cms_aligned_with_data_network": NDH_SD + "base-ext-cms-aligned-with-data-network",
    NDH_SD + "base-ext-cms_medicare_enrollment": NDH_SD + "base-ext-cms-medicare-enrollment-in-good-standing",
    NDH_SD + "base-ext-hhs-in-exclusion-list": NDH_SD + "base-ext-hhs-exclusion-list",
}

# CMS extension URLs with no NDH equivalent.
EXTENSION_URL_DROPS = set()

# CMS system URIs rewritten to the standard code system URIs.
CODING_SYSTEM_REMAPS = {
    "http://terminology.hl7.org/NamingSystem/npi": "http://hl7.org/fhir/sid/us-npi",
    NDH_SD.replace("StructureDefinition", "ValueSet") + "HealthcareIndividualTaxonomyVS": "http://nucc.org/provider-taxonomy",
}

FACET_CREDENTIAL_SYSTEM = "http://hl7.org/fhir/us/ndh/CodeSystem/FaCeT-credentialCS"

# CMS credential codes absent from FaCeT-credentialCS, keyed by (code, display).
# The display selects between the suffixed concepts that share an abbreviation.
CREDENTIAL_CODE_REMAPS = {
    ("CRN", "Certified Registered Nurse"): "CRN_2",
    ("MT", "Medical Technician"): "MT_2",
    ("BT", "Bachelor of Theology"): "BT_2",
}

ACCEPTING_PATIENTS_SYSTEM = "http://terminology.hl7.org/CodeSystem/accepting-patients"
VERIFICATION_STATUS_SYSTEM = "http://hl7.org/fhir/us/ndh/CodeSystem/NdhVerificationStatusCS"

# Resource types where base-ext-verification-status is allowed.
VERIFICATION_STATUS_CONTEXT_TYPES = {
    "Endpoint",
    "HealthcareService",
    "InsurancePlan",
    "Location",
    "Organization",
    "OrganizationAffiliation",
    "Practitioner",
    "PractitionerRole",
}

# Resource types whose NDH profiles pin active = true (patternBoolean).
ACTIVE_FLAG_TYPES = {"Practitioner", "PractitionerRole", "Organization", "OrganizationAffiliation"}
# Resource types whose NDH profiles pin status = "active" (fixedCode).
STATUS_FLAG_TYPES = {"Location", "Endpoint"}

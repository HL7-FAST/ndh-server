import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from npd_data.download import fetch_validator_jar, parse_manifest, resource_type_from_filename


def _fake_response(body):
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [body]
    return response


class FetchArtifactTests(unittest.TestCase):
    def test_local_path_returned_as_is(self):
        self.assertEqual(
            Path("/some/validator.jar"), fetch_validator_jar("/some/validator.jar", "/unused")
        )

    def test_url_downloaded_once_then_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "npd_data.download.requests.get", return_value=_fake_response(b"jar")
            ) as get:
                first = fetch_validator_jar(None, tmp)
                second = fetch_validator_jar(None, tmp)
            self.assertEqual(first, second)
            self.assertEqual(b"jar", first.read_bytes())
            self.assertEqual(1, get.call_count)

    def test_force_redownloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "npd_data.download.requests.get", return_value=_fake_response(b"new")
            ) as get:
                fetch_validator_jar(None, tmp)
                target = fetch_validator_jar(None, tmp, force=True)
            self.assertEqual(2, get.call_count)
            self.assertEqual(b"new", target.read_bytes())


class ManifestTests(unittest.TestCase):
    def test_parse_manifest(self):
        manifest = {
            "generated_at": "2026-09-29",
            "files": {
                "06-Practitioner.ndjson": {"compressed_bytes": 10},
                "07-PractitionerRole.ndjson": {"compressed_bytes": 20},
            },
        }
        release = parse_manifest(manifest)
        self.assertEqual("2026-09-29", release["release_date"])
        self.assertEqual(
            [("Practitioner", "06-Practitioner.ndjson.zst", 10), ("PractitionerRole", "07-PractitionerRole.ndjson.zst", 20)],
            [(f["resource_name"], f["filename"], f["compressed_bytes"]) for f in release["files"]],
        )

    def test_resource_type_from_filename(self):
        self.assertEqual("Organization", resource_type_from_filename("01-Organization.ndjson.zst"))
        self.assertEqual("Organization", resource_type_from_filename("Organization.ndjson"))
        self.assertIsNone(resource_type_from_filename("01-.ndjson"))


if __name__ == "__main__":
    unittest.main()

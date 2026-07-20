import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from npd_data.download import fetch_validator_jar


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


if __name__ == "__main__":
    unittest.main()

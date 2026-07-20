import tempfile
import unittest
from pathlib import Path

from npd_data.ndjson import iter_ndjson, loads


class NdjsonTests(unittest.TestCase):
    def test_loads_allows_source_control_characters(self):
        self.assertEqual({"name": "a\x1fb"}, loads('{"name":"a\x1fb"}'))

    def test_iter_ndjson_allows_source_control_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.ndjson"
            path.write_text('{"name":"a\x1fb"}\n')

            self.assertEqual([{"name": "a\x1fb"}], list(iter_ndjson(path)))


if __name__ == "__main__":
    unittest.main()

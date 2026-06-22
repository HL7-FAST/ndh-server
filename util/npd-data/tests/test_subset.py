import json
import tempfile
import unittest
from pathlib import Path

from npd_data.subset import SubsetConfig, run_subset


def write_ndjson(path, resources):
    path.write_text(
        "\n".join(json.dumps(resource) for resource in resources) + "\n",
        encoding="utf-8",
    )


class SubsetTests(unittest.TestCase):
    def test_boundary_location_partof_chain_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            locations = Path(tmp) / "Location.ndjson"
            write_ndjson(
                locations,
                [
                    {
                        "resourceType": "Location",
                        "id": "child",
                        "status": "active",
                        "address": {"city": "Testville", "state": "TX"},
                        "partOf": {"reference": "Location/parent"},
                    },
                    {
                        "resourceType": "Location",
                        "id": "parent",
                        "status": "active",
                        "partOf": {"reference": "Location/grandparent"},
                    },
                    {
                        "resourceType": "Location",
                        "id": "grandparent",
                        "status": "active",
                    },
                ],
            )

            result = run_subset(
                {"Location": [locations]},
                SubsetConfig(cities=["Testville"], state="TX"),
            )

        self.assertEqual(
            {"Location/child", "Location/parent", "Location/grandparent"},
            set(result.kept),
        )
        self.assertEqual("boundary", result.roles["Location/grandparent"])


if __name__ == "__main__":
    unittest.main()

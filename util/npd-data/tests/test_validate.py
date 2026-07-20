import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from npd_data.validate import run_validator_sharded, shard_count


class ShardCountTests(unittest.TestCase):
    def test_memory_caps_shards(self):
        with patch("npd_data.validate._available_memory", return_value=3 * 1024**3):
            self.assertEqual(1, shard_count(xmx="2g"))

    def test_ample_memory_leaves_core_cap(self):
        with patch("npd_data.validate._available_memory", return_value=64 * 1024**3):
            self.assertEqual(shard_count(), shard_count(xmx="2g"))

    def test_unknown_memory_falls_back_to_cores(self):
        with patch("npd_data.validate._available_memory", return_value=None):
            self.assertEqual(shard_count(), shard_count(xmx="2g"))


class ValidateTests(unittest.TestCase):
    def test_shard_filenames_keep_resource_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            for resource_type in ("Location", "Organization"):
                path = staged / resource_type / "same.json"
                path.parent.mkdir()
                path.write_text("{}")

            def fake_run(command, capture_output, text):
                self.assertIn("-level", command)
                self.assertEqual("error", command[command.index("-level") + 1])
                shard_dir = Path(command[-1])
                self.assertEqual(
                    ["Location__same.json", "Organization__same.json"],
                    sorted(path.name for path in shard_dir.iterdir()),
                )
                out_file = Path(command[command.index("-output") + 1])
                out_file.write_text(
                    f"{shard_dir / 'Location__same.json'}: line 1, Error - bad\n"
                )
                return subprocess.CompletedProcess(command, 1, "", "")

            with patch("npd_data.validate.subprocess.run", fake_run):
                report = run_validator_sharded(
                    staged, "ig.tgz", validator_jar="validator.jar", shards=1
                )

        self.assertEqual("Location/same.json: line 1, Error - bad\n", report)

    def test_shards_interleave_sorted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            for resource_type in ("AType", "BType"):
                for rid in ("1", "2"):
                    path = staged / resource_type / f"{rid}.json"
                    path.parent.mkdir(exist_ok=True)
                    path.write_text("{}")

            seen = {}

            def fake_run(command, capture_output, text):
                shard_dir = Path(command[-1])
                seen[shard_dir.name] = sorted(path.name for path in shard_dir.iterdir())
                out_file = Path(command[command.index("-output") + 1])
                out_file.write_text("")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("npd_data.validate.subprocess.run", fake_run):
                run_validator_sharded(staged, "ig.tgz", validator_jar="validator.jar", shards=2)

        self.assertEqual(
            {
                "shard-0": ["AType__1.json", "BType__1.json"],
                "shard-1": ["AType__2.json", "BType__2.json"],
            },
            seen,
        )

    def test_tx_cache_containing_staged_dir_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "out"
            (staged / "Location").mkdir(parents=True)
            (staged / "Location" / "one.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "must not equal or contain"):
                run_validator_sharded(
                    staged, "ig.tgz", validator_jar="validator.jar", tx_cache_root=tmp
                )

    def test_relative_tx_cache_below_absolute_staged_dir_is_excluded(self):
        with tempfile.TemporaryDirectory(dir=".") as tmp:
            staged = Path(tmp).resolve()
            resource = staged / "Location" / "one.json"
            resource.parent.mkdir()
            resource.write_text("{}")
            tx_cache = Path(tmp) / "tx"
            tx_cache.mkdir()
            (tx_cache / "system-map.json").write_text("{}")

            def fake_run(command, capture_output, text):
                shard_dir = Path(command[-1])
                self.assertEqual(["Location__one.json"], [p.name for p in shard_dir.iterdir()])
                Path(command[command.index("-output") + 1]).write_text("")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("npd_data.validate.subprocess.run", fake_run):
                run_validator_sharded(
                    staged,
                    "ig.tgz",
                    validator_jar="validator.jar",
                    shards=1,
                    tx_cache_root=tx_cache,
                )

    def test_exit_1_without_reported_errors_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            path = staged / "Location" / "one.json"
            path.parent.mkdir()
            path.write_text("{}")

            with patch(
                "npd_data.validate.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "startup failed", ""),
            ):
                with self.assertRaisesRegex(RuntimeError, "validator shard 0 failed"):
                    run_validator_sharded(staged, "ig.tgz", validator_jar="validator.jar", shards=1)


if __name__ == "__main__":
    unittest.main()

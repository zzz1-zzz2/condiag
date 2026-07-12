"""Tests for instance_identity.py.

Covers:
  - resolve_canonical_instance_id with various pool record formats
  - instance_artifact_filename with various ID formats
  - resolve_instance_alias with alias index
  - Edge cases: empty records, short hashes, vnan suffix,
    different dir_name vs instance_id, collision scenarios
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from condiag.instance_identity import (
    instance_artifact_filename,
    resolve_canonical_instance_id,
    resolve_instance_alias,
    resolve_from_manifest,
)


class TestResolveCanonicalInstanceId(unittest.TestCase):

    def test_uses_dir_name_when_present(self):
        """dir_name takes priority over instance_id."""
        record = {
            "instance_id": "instance_NodeBB__shorthash",
            "dir_name": "instance_NodeBB__NodeBB-abc123def456-vnan",
        }
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "instance_NodeBB__NodeBB-abc123def456-vnan")

    def test_falls_back_to_instance_id(self):
        """When dir_name is absent, use instance_id."""
        record = {"instance_id": "django__django-11820"}
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "django__django-11820")

    def test_explicit_canonical_id(self):
        """canonical_id field is used as fallback."""
        record = {
            "instance_id": "short-alias",
            "canonical_id": "full-canonical-id-123456",
        }
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "full-canonical-id-123456")

    def test_raises_on_empty_record(self):
        with self.assertRaises(ValueError):
            resolve_canonical_instance_id({})

    def test_raises_on_all_empty_fields(self):
        record = {"instance_id": "", "dir_name": "", "canonical_id": None}
        with self.assertRaises(ValueError):
            resolve_canonical_instance_id(record)

    def test_vnan_suffix_not_confused_with_short_hash(self):
        """-vnan suffix should not trigger short-hash warning."""
        record = {
            "instance_id": "instance_NodeBB__NodeBB-short",
            "dir_name": "instance_NodeBB__NodeBB-abc123def456-vnan",
        }
        # Should resolve cleanly without ValueError
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "instance_NodeBB__NodeBB-abc123def456-vnan")

    def test_standard_swebench_id(self):
        """Standard SWE-bench ID works."""
        record = {"instance_id": "django__django-11820"}
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "django__django-11820")

    def test_long_hash_id_no_dir_name(self):
        """Long hash ID without explicit dir_name uses instance_id."""
        record = {
            "instance_id": "instance_ansible__ansible-abc123def456-v0f01c69deadbeef",
        }
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "instance_ansible__ansible-abc123def456-v0f01c69deadbeef")

    def test_dir_name_equals_instance_id(self):
        """When dir_name equals instance_id, still returns correctly."""
        record = {
            "instance_id": "sympy__sympy-16597",
            "dir_name": "sympy__sympy-16597",
        }
        result = resolve_canonical_instance_id(record)
        self.assertEqual(result, "sympy__sympy-16597")


class TestInstanceArtifactFilename(unittest.TestCase):

    def test_standard_id(self):
        """django__django-11820 → django__django-11820.json"""
        result = instance_artifact_filename("django__django-11820")
        self.assertEqual(result, "django__django-11820")

    def test_long_hash_id(self):
        """Full hash ID unchanged."""
        cid = "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc550c63509046-vnan"
        result = instance_artifact_filename(cid)
        self.assertEqual(result, cid)

    def test_vnan_suffix(self):
        cid = "instance_ansible__ansible-abc123-v0f01c69deadbeef"
        result = instance_artifact_filename(cid)
        self.assertEqual(result, cid)

    def test_replaces_forward_slash(self):
        """Forward slash replaced with __."""
        result = instance_artifact_filename("repo/name-123")
        self.assertEqual(result, "repo__name-123")

    def test_raises_on_empty(self):
        with self.assertRaises(ValueError):
            instance_artifact_filename("")

    def test_raises_on_none(self):
        with self.assertRaises(ValueError):
            instance_artifact_filename(None)  # type: ignore

    def test_deterministic(self):
        """Same input → same output."""
        r1 = instance_artifact_filename("django__django-11820")
        r2 = instance_artifact_filename("django__django-11820")
        self.assertEqual(r1, r2)

    def test_no_collision_for_different_ids(self):
        """Different inputs → different outputs."""
        r1 = instance_artifact_filename("django__django-11820")
        r2 = instance_artifact_filename("django__django-14349")
        self.assertNotEqual(r1, r2)


class TestResolveInstanceAlias(unittest.TestCase):

    def setUp(self):
        self.index = {
            "instance_NodeBB__NodeBB-4f2b6de5": {
                "instance_id": "instance_NodeBB__NodeBB-4f2b6de5",
                "dir_name": "instance_NodeBB__NodeBB-c63509046-vnan",
            },
            "instance_NodeBB__NodeBB-c63509046-vnan": {
                "instance_id": "instance_NodeBB__NodeBB-4f2b6de5",
                "dir_name": "instance_NodeBB__NodeBB-c63509046-vnan",
            },
            "django__django-11820": {
                "instance_id": "django__django-11820",
            },
        }

    def test_resolve_alias_to_canonical(self):
        """Short hash alias resolves to canonical dir_name."""
        result = resolve_instance_alias("instance_NodeBB__NodeBB-4f2b6de5", self.index)
        self.assertEqual(result, "instance_NodeBB__NodeBB-c63509046-vnan")

    def test_resolve_canonical_to_itself(self):
        """Canonical ID resolves to itself."""
        result = resolve_instance_alias("instance_NodeBB__NodeBB-c63509046-vnan", self.index)
        self.assertEqual(result, "instance_NodeBB__NodeBB-c63509046-vnan")

    def test_simple_id_resolves_to_itself(self):
        result = resolve_instance_alias("django__django-11820", self.index)
        self.assertEqual(result, "django__django-11820")

    def test_unknown_alias_raises_keyerror(self):
        with self.assertRaises(KeyError):
            resolve_instance_alias("nonexistent-id", self.index)


class TestResolveFromManifest(unittest.TestCase):

    def setUp(self):
        self.manifest_data = {
            "pool_version": "v1",
            "instances": [
                {"instance_id": "django__django-11820", "split": "dev"},
                {
                    "instance_id": "instance_NodeBB__NodeBB-4f2b6de5",
                    "dir_name": "instance_NodeBB__NodeBB-c63509046-vnan",
                    "split": "dev",
                },
            ],
        }

    def test_resolve_standard_from_manifest(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(self.manifest_data, f)
            tmp = f.name
        try:
            result = resolve_from_manifest("django__django-11820", tmp)
            self.assertEqual(result, "django__django-11820")
        finally:
            os.unlink(tmp)

    def test_resolve_alias_from_manifest(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(self.manifest_data, f)
            tmp = f.name
        try:
            result = resolve_from_manifest("instance_NodeBB__NodeBB-4f2b6de5", tmp)
            self.assertEqual(result, "instance_NodeBB__NodeBB-c63509046-vnan")
        finally:
            os.unlink(tmp)

    def test_unknown_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(self.manifest_data, f)
            tmp = f.name
        try:
            with self.assertRaises(KeyError):
                resolve_from_manifest("nobody", tmp)
        finally:
            os.unlink(tmp)


class TestRealWorldRecords(unittest.TestCase):
    """Test against actual Dev-10 pool records."""

    def setUp(self):
        pool_path = Path("/mnt/d/condiag-artifacts/condiag/pool/condiag_dev_pool.json")
        if not pool_path.is_file():
            self.skipTest("Dev pool not available")
        with open(pool_path) as f:
            data = json.load(f)
        self.instances = data["instances"]

    def test_all_resolve_uniquely(self):
        """All 10 dev instances resolve to unique canonical IDs."""
        canonicals = set()
        for inst in self.instances:
            cid = resolve_canonical_instance_id(inst)
            canonicals.add(cid)
        self.assertEqual(len(canonicals), len(self.instances),
                         f"Expected {len(self.instances)} unique IDs, got {len(canonicals)}")

    def test_all_resolve_to_dir_name(self):
        """Every canonical ID matches dir_name (not short hash)."""
        for inst in self.instances:
            cid = resolve_canonical_instance_id(inst)
            expected = inst.get("dir_name", inst["instance_id"])
            self.assertEqual(cid, expected,
                             f"Canonical ID {cid} != expected {expected}")

    def test_all_produce_distinct_filenames(self):
        """All 10 produce unique filenames (no collisions)."""
        filenames = set()
        for inst in self.instances:
            cid = resolve_canonical_instance_id(inst)
            fname = instance_artifact_filename(cid)
            self.assertNotIn(fname, filenames,
                             f"Duplicate filename: {fname}")
            filenames.add(fname)
        self.assertEqual(len(filenames), len(self.instances))

    def test_filename_matches_directory(self):
        """Filename should match the on-disk instance directory name."""
        instances_dir = Path("/mnt/d/condiag-artifacts/condiag/instances")
        for inst in self.instances:
            cid = resolve_canonical_instance_id(inst)
            fname = instance_artifact_filename(cid)
            dir_path = instances_dir / cid
            self.assertTrue(dir_path.is_dir(),
                            f"Canonical ID {cid} should have matching directory at {dir_path}")


class TestNodeBBSpecific(unittest.TestCase):
    """NodeBB-specific regression test."""

    def test_nodebb_resolves_correctly(self):
        """NodeBB short hash → canonical long hash."""
        record = {
            "instance_id": "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc5504f2b6de5",
            "dir_name": "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc550c63509046-vnan",
        }
        cid = resolve_canonical_instance_id(record)
        self.assertEqual(cid, "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc550c63509046-vnan")
        fname = instance_artifact_filename(cid)
        self.assertEqual(fname, cid)
        # Expected contract filename
        self.assertEqual(f"{fname}.json",
                         "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc550c63509046-vnan.json")


class TestAnsibleSpecific(unittest.TestCase):
    """Ansible-specific regression test."""

    def test_ansible_resolves_correctly(self):
        """Ansible short hash → canonical long hash."""
        record = {
            "instance_id": "instance_ansible__ansible-949c503f2ef4b2c5d668af0492a5c71e7f136ffa",
            "dir_name": "instance_ansible__ansible-949c503f2ef4b2c5d668af0492a5c0db1ab86140-v0f01c69f1e2528b935359cfe578530722bca2c59",
        }
        cid = resolve_canonical_instance_id(record)
        self.assertTrue(cid.endswith("-v0f01c69f1e2528b935359cfe578530722bca2c59"))
        fname = instance_artifact_filename(cid)
        self.assertEqual(fname, cid)


if __name__ == "__main__":
    unittest.main()

"""
Tests for the data integrity verification module.

These tests use the pre-built BIDS dataset fixture (from conftest.py)
to test manifest generation, verification, and corruption detection.
"""

import json

from neuro_curation.verify import generate_manifest, verify_manifest


class TestGenerateManifest:
    """Verify that manifest generation produces correct structure."""

    def test_generate_manifest(self, bids_copy):
        """Manifest should contain entries for all dataset files with
        required fields (path, sha256, size_bytes)."""
        manifest = generate_manifest(bids_copy)

        # Manifest metadata
        assert manifest["hash_algorithm"] == "SHA-256"
        assert manifest["total_files"] > 0
        assert "generated_at" in manifest

        # Every file in the dataset should be in the manifest
        # (excluding the manifest itself)
        dataset_files = sorted(
            f.relative_to(bids_copy).as_posix()
            for f in bids_copy.rglob("*")
            if f.is_file() and f.name != "transfer_manifest.json"
        )
        manifest_paths = sorted(e["path"] for e in manifest["files"])
        assert manifest_paths == dataset_files

        # Each entry should have required fields
        for entry in manifest["files"]:
            assert len(entry["sha256"]) == 64, "SHA-256 should be 64 hex chars"
            assert entry["size_bytes"] > 0
            assert "last_modified" in entry


class TestVerifyIntact:
    """Verify that an unmodified dataset passes verification."""

    def test_verify_intact_dataset(self, bids_copy):
        """Generate manifest then immediately verify — should pass
        with zero mismatches and zero missing files."""
        generate_manifest(bids_copy)
        results = verify_manifest(bids_copy)

        assert results["passed"] is True
        assert len(results["mismatches"]) == 0
        assert len(results["missing"]) == 0
        assert results["files_checked"] == results["total_files"]


class TestVerifyCorruption:
    """Verify that corruption and missing files are detected."""

    def test_verify_detects_corruption(self, bids_copy):
        """Modifying a file after manifest generation should be detected
        as a hash mismatch. Deleting a file should be detected as missing."""
        generate_manifest(bids_copy)

        # Corrupt a file by appending data
        nifti_files = list(bids_copy.rglob("*.nii.gz"))
        assert len(nifti_files) > 0
        corrupted_file = nifti_files[0]
        with open(corrupted_file, "ab") as f:
            f.write(b"CORRUPTED")

        # Delete another file if available
        deleted_file = None
        json_files = list(bids_copy.rglob("*.json"))
        # Find a JSON file that's not the manifest
        for jf in json_files:
            if jf.name != "transfer_manifest.json":
                deleted_file = jf
                break

        if deleted_file:
            deleted_file.unlink()

        results = verify_manifest(bids_copy)

        assert results["passed"] is False
        assert len(results["mismatches"]) >= 1, "Should detect corrupted file"

        if deleted_file:
            assert len(results["missing"]) >= 1, "Should detect missing file"

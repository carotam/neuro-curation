"""
Tests for the FAIR compliance audit module.

Uses the pre-built BIDS dataset fixture (from conftest.py) with
modifications to test pass/fail scenarios for each FAIR principle.
"""

from neuro_curation.audit import run_audit


class TestAuditComplete:
    """Verify that a complete BIDS dataset passes all FAIR checks."""

    def test_audit_complete_dataset(self, bids_copy):
        """A well-formed BIDS dataset with all metadata files should
        achieve 100% FAIR compliance score."""
        results = run_audit(bids_copy)

        assert results["score"] == 100.0
        assert results["total_passed"] == results["total_checks"]
        assert results["total_checks"] > 0

        # Each principle should have checks
        for principle in ["F", "A", "I", "R"]:
            assert principle in results["principles"]
            p = results["principles"][principle]
            assert p["passed"] == p["total"], (
                f"Principle {principle}: {p['passed']}/{p['total']} passed"
            )


class TestAuditMissingMetadata:
    """Verify that missing metadata files are detected."""

    def test_audit_missing_metadata(self, bids_copy):
        """Removing dataset_description.json and LICENSE should cause
        Findable and Reusable checks to fail."""
        # Remove key metadata files
        (bids_copy / "dataset_description.json").unlink()
        (bids_copy / "LICENSE").unlink()

        results = run_audit(bids_copy)

        assert results["score"] < 100.0

        # Findable should fail (no dataset_description.json)
        f_checks = results["principles"]["F"]
        assert f_checks["passed"] < f_checks["total"]

        # Reusable should fail (no LICENSE)
        r_checks = results["principles"]["R"]
        assert r_checks["passed"] < r_checks["total"]


class TestAuditBadNaming:
    """Verify that non-BIDS filenames are detected."""

    def test_audit_bad_naming(self, bids_copy):
        """Files that don't follow BIDS naming should cause
        Interoperable checks to fail."""
        # Rename a NIfTI file to a non-BIDS name
        anat_dir = bids_copy / "sub-01" / "anat"
        bids_file = anat_dir / "sub-01_T1w.nii.gz"
        bad_file = anat_dir / "brain_scan.nii.gz"

        if bids_file.exists():
            bids_file.rename(bad_file)

        results = run_audit(bids_copy)

        # Interoperable should have at least one failure
        i_checks = results["principles"]["I"]
        assert i_checks["passed"] < i_checks["total"]

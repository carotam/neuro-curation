"""
Tests for the DICOM-to-NIfTI conversion and BIDS organization module.

Tests 1-2 work without dcm2niix installed (they test pure Python logic).
Test 3 requires dcm2niix and is skipped if the binary is absent.
"""

import json
import shutil
import subprocess

import pytest

from neuro_curation.convert import (
    convert_subject,
    create_bids_metadata,
    detect_bids_suffix,
)


class TestDetectBidsSuffix:
    """Verify that SeriesDescription strings map to correct BIDS suffixes."""

    def test_detect_bids_suffix(self):
        """Common scanner protocol names should map to the right BIDS suffix.
        Unknown protocols should return 'unknown' gracefully."""
        # Standard Siemens protocol names (as used in Insight 46)
        assert detect_bids_suffix("T1_MPRAGE") == "T1w"
        assert detect_bids_suffix("t1_mprage") == "T1w"
        assert detect_bids_suffix("FLAIR_3D") == "FLAIR"
        assert detect_bids_suffix("T2_TSE") == "T2w"
        assert detect_bids_suffix("DWI") == "dwi"

        # Variations with extra text (substring matching)
        assert detect_bids_suffix("MPRAGE_GRAPPA2") == "T1w"
        assert detect_bids_suffix("resting_state_bold") == "bold"

        # Unknown protocol should return "unknown" (not crash)
        assert detect_bids_suffix("weird_protocol_xyz") == "unknown"


class TestBidsMetadata:
    """Verify that BIDS root-level metadata files are created correctly."""

    def test_bids_metadata_created(self, tmp_path):
        """create_bids_metadata should generate all required BIDS root files
        with the correct structure and content."""
        create_bids_metadata(tmp_path, dataset_name="Test Dataset")

        # dataset_description.json — required by BIDS
        desc_path = tmp_path / "dataset_description.json"
        assert desc_path.exists(), "dataset_description.json must exist"
        desc = json.loads(desc_path.read_text())
        assert desc["Name"] == "Test Dataset"
        assert desc["BIDSVersion"] == "1.9.0"
        assert desc["DatasetType"] == "raw"
        assert "GeneratedBy" in desc

        # README — human-readable description
        readme_path = tmp_path / "README"
        assert readme_path.exists(), "README must exist"
        assert len(readme_path.read_text()) > 50, "README should have meaningful content"

        # participants.tsv — subject metadata table
        tsv_path = tmp_path / "participants.tsv"
        assert tsv_path.exists(), "participants.tsv must exist"
        header = tsv_path.read_text().strip().split("\n")[0]
        assert "participant_id" in header

        # participants.json — data dictionary
        json_path = tmp_path / "participants.json"
        assert json_path.exists(), "participants.json must exist"
        data_dict = json.loads(json_path.read_text())
        assert "participant_id" in data_dict
        assert "age" in data_dict


# Check if dcm2niix is available for the integration test
def _dcm2niix_available():
    try:
        subprocess.run(["dcm2niix", "--version"], capture_output=True)
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _dcm2niix_available(), reason="dcm2niix not installed")
class TestBidsConversionIntegration:
    """Integration test: full DICOM-to-BIDS conversion (requires dcm2niix)."""

    def test_bids_directory_structure(self, dicom_copy):
        """Full conversion should produce correct BIDS tree with
        .nii.gz files, JSON sidecars, and root metadata."""
        input_dir, output_dir = dicom_copy

        convert_subject(
            input_dir=input_dir,
            output_dir=output_dir,
            subject_id="sub-01",
            dataset_name="Integration Test",
        )

        # Root metadata should exist
        assert (output_dir / "dataset_description.json").exists()
        assert (output_dir / "participants.tsv").exists()
        assert (output_dir / "README").exists()

        # Subject directory should exist with NIfTI files
        sub_dir = output_dir / "sub-01"
        assert sub_dir.exists(), "Subject directory should be created"

        nifti_files = list(sub_dir.rglob("*.nii.gz"))
        assert len(nifti_files) > 0, "Should produce at least one NIfTI file"

        # Each NIfTI should have a JSON sidecar
        for nifti in nifti_files:
            json_sidecar = nifti.with_suffix("").with_suffix(".json")
            assert json_sidecar.exists(), f"Missing JSON sidecar for {nifti.name}"

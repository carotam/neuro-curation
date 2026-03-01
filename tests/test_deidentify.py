"""
Tests for the DICOM de-identification module.

These tests use inline synthetic DICOM files (from conftest.py) with
deliberately planted PII tags. Each test verifies a different aspect
of the de-identification process:

1. PII tags are removed or replaced
2. UIDs are replaced consistently across files
3. Private (vendor-specific) tags are removed
4. De-identification markers are set correctly
"""

import pydicom
import pytest

from neuro_curation.deidentify import deidentify_directory, deidentify_file


class TestDeidentifyPII:
    """Verify that all planted PII tags are properly removed or replaced."""

    def test_pii_tags_removed(self, dicom_copy):
        """After de-identification, direct identifiers must be absent
        and PatientName/PatientID must be replaced with the subject ID."""
        input_dir, output_dir = dicom_copy

        deidentify_directory(input_dir, output_dir, subject_id="sub-01")

        # Read the first de-identified file
        output_files = sorted(output_dir.rglob("*.dcm"))
        assert len(output_files) > 0, "No output files produced"
        ds = pydicom.dcmread(str(output_files[0]))

        # Direct identifiers must be gone
        assert not hasattr(ds, "PatientBirthDate"), "PatientBirthDate should be removed"
        assert not hasattr(ds, "PatientAddress"), "PatientAddress should be removed"
        assert not hasattr(ds, "PatientAge"), "PatientAge should be removed"

        # Hospital system links must be gone
        assert not hasattr(ds, "InstitutionName"), "InstitutionName should be removed"
        assert not hasattr(ds, "AccessionNumber"), "AccessionNumber should be removed"

        # Personnel identifiers must be gone
        assert not hasattr(ds, "ReferringPhysicianName"), "ReferringPhysicianName should be removed"
        assert not hasattr(ds, "PerformingPhysicianName"), "PerformingPhysicianName should be removed"

        # PatientName and PatientID replaced with subject ID
        assert str(ds.PatientName) == "sub-01"
        assert ds.PatientID == "sub-01"


class TestDeidentifyUIDs:
    """Verify that UIDs are replaced consistently across a series."""

    def test_uids_replaced_consistently(self, dicom_copy):
        """All files in the same series must share the same new
        StudyInstanceUID but have unique SOPInstanceUIDs, and all
        new UIDs must differ from the originals."""
        input_dir, output_dir = dicom_copy

        # Read original UIDs before de-identification
        input_files = sorted(input_dir.rglob("*.dcm"))
        original_study_uid = pydicom.dcmread(str(input_files[0])).StudyInstanceUID
        original_sop_uids = {
            pydicom.dcmread(str(f)).SOPInstanceUID for f in input_files
        }

        deidentify_directory(input_dir, output_dir, subject_id="sub-01")

        output_files = sorted(output_dir.rglob("*.dcm"))
        deid_datasets = [pydicom.dcmread(str(f)) for f in output_files]

        # All files should share the same NEW StudyInstanceUID
        study_uids = {ds.StudyInstanceUID for ds in deid_datasets}
        assert len(study_uids) == 1, "All files should share one StudyInstanceUID"

        # The new StudyInstanceUID must differ from the original
        new_study_uid = study_uids.pop()
        assert new_study_uid != original_study_uid, "StudyInstanceUID should change"

        # Each file should have a unique SOPInstanceUID
        sop_uids = {ds.SOPInstanceUID for ds in deid_datasets}
        assert len(sop_uids) == len(deid_datasets), "Each file needs unique SOPInstanceUID"

        # No new SOP UID should match any original
        assert not sop_uids & original_sop_uids, "SOPInstanceUIDs should all change"


class TestDeidentifyPrivateTags:
    """Verify that vendor-specific private tags are removed."""

    def test_private_tags_removed(self, dicom_copy):
        """Private tags (odd group numbers) often contain vendor-specific
        patient info. They must all be stripped."""
        input_dir, output_dir = dicom_copy

        # Verify the synthetic data has private tags before de-identification
        input_files = sorted(input_dir.rglob("*.dcm"))
        ds_before = pydicom.dcmread(str(input_files[0]))
        has_private = any(elem.tag.is_private for elem in ds_before)
        assert has_private, "Test data should contain private tags"

        deidentify_directory(input_dir, output_dir, subject_id="sub-01")

        output_files = sorted(output_dir.rglob("*.dcm"))
        ds_after = pydicom.dcmread(str(output_files[0]))

        # No private tags should remain
        private_tags = [elem for elem in ds_after if elem.tag.is_private]
        assert len(private_tags) == 0, f"Private tags still present: {private_tags}"


class TestDeidentifyMarkers:
    """Verify that de-identification compliance markers are set."""

    def test_deidentification_markers_set(self, dicom_copy):
        """DICOM standard requires setting PatientIdentityRemoved=YES
        and a DeidentificationMethod when de-identification is applied.
        Also, all PersonName VR fields should be scrubbed."""
        input_dir, output_dir = dicom_copy

        deidentify_directory(input_dir, output_dir, subject_id="sub-01")

        output_files = sorted(output_dir.rglob("*.dcm"))
        ds = pydicom.dcmread(str(output_files[0]))

        # De-identification markers per DICOM standard
        assert ds.PatientIdentityRemoved == "YES"
        assert "PS3.15" in ds.DeidentificationMethod

        # All PersonName VR fields should be either "sub-01" or "ANONYMOUS"
        for elem in ds:
            if elem.VR == "PN":
                assert str(elem.value) in ("sub-01", "ANONYMOUS"), (
                    f"PersonName field {elem.tag} has unexpected value: {elem.value}"
                )

"""
Shared pytest fixtures for neuro-curation test suite.

This file creates two types of test data INLINE — no external files needed:

1. Synthetic DICOM series: Minimal DICOM files with planted PII tags.
   Used by deidentify and convert tests. The PII is deliberately planted
   so tests can verify it gets stripped correctly.

2. Pre-built BIDS dataset: A valid BIDS directory structure with tiny NIfTI
   files. Used by verify, audit, and report tests. This avoids needing
   dcm2niix to run those tests.

All fixtures are session-scoped (created once, shared across all tests)
for speed. Tests that need to modify data should copy to their own tmp_path.
"""

import json
import shutil

import nibabel as nib
import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
import pytest


# ---------------------------------------------------------------------------
# Helper: create a minimal NIfTI file
# ---------------------------------------------------------------------------

def create_minimal_nifti(filepath):
    """Create a tiny 4x4x4 NIfTI file for testing.

    We use nibabel to create a minimal image with zeros. This is enough
    for the verify, audit, and report modules to detect and process it
    without needing a real brain scan.
    """
    img = nib.Nifti1Image(np.zeros((4, 4, 4), dtype=np.int16), affine=np.eye(4))
    nib.save(img, str(filepath))


# ---------------------------------------------------------------------------
# Helper: create a single synthetic DICOM file
# ---------------------------------------------------------------------------

def _create_synthetic_dicom(
    filepath,
    patient_name="Doe^Jane",
    patient_id="HOSPITAL-12345",
    series_description="T1_MPRAGE",
    study_instance_uid=None,
    series_instance_uid=None,
    instance_number=1,
):
    """Create a minimal but valid DICOM file with planted PII tags.

    Each tag that contains personally identifiable information is marked
    with a comment explaining WHY it's a risk. These are the exact tags
    our deidentify module must handle.

    Args:
        filepath: Where to save the .dcm file.
        patient_name: Planted PII — direct identifier.
        patient_id: Planted PII — links to hospital records.
        series_description: Used by convert module to detect BIDS suffix.
        study_instance_uid: Shared across all files in the same study.
        series_instance_uid: Shared across all files in the same series.
        instance_number: Slice number within the series.
    """
    # File meta info — required for a valid DICOM Part 10 file
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    # Create the dataset with the standard 128-byte preamble
    ds = FileDataset(str(filepath), {}, file_meta=file_meta, preamble=b"\x00" * 128)

    # --- Planted PII tags (these MUST be stripped by deidentify) ---
    ds.PatientName = patient_name                       # Direct identifier
    ds.PatientID = patient_id                           # Links to hospital RIS/PACS
    ds.PatientBirthDate = "19460315"                    # Direct identifier (1946 = NSHD birth year)
    ds.PatientAge = "079Y"                              # Quasi-identifier for small cohorts
    ds.PatientAddress = "123 Queen Square, London"      # Direct identifier
    ds.InstitutionName = "UCL Queen Square Institute"   # Identifies scanning site
    ds.InstitutionAddress = "Queen Square, London"      # Identifies scanning site
    ds.ReferringPhysicianName = "Dr^Smith"              # Personnel identifier
    ds.PerformingPhysicianName = "Dr^Jones"             # Personnel identifier
    ds.OperatorsName = "TechnicianA"                    # Personnel identifier
    ds.AccessionNumber = "ACC-98765"                    # Links to hospital system
    ds.StudyDescription = "Research Brain MRI"          # May contain patient info

    # --- Imaging metadata (required for valid DICOM) ---
    ds.Modality = "MR"
    ds.SeriesDescription = series_description
    ds.StudyInstanceUID = study_instance_uid or generate_uid()
    ds.SeriesInstanceUID = series_instance_uid or generate_uid()
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.InstanceNumber = instance_number
    ds.StudyDate = "20240115"
    ds.SeriesDate = "20240115"
    ds.StudyTime = "093000"

    # --- Minimal pixel data (64x64 zeros, 16-bit unsigned) ---
    ds.Rows = 64
    ds.Columns = 64
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0  # Unsigned
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = np.zeros((64, 64), dtype=np.uint16).tobytes()

    # --- Add a private tag (should be removed by deidentify) ---
    # Private tags often contain vendor-specific patient information
    ds.add_new(pydicom.tag.Tag(0x0009, 0x0010), "LO", "VENDOR_PRIVATE")
    ds.add_new(pydicom.tag.Tag(0x0009, 0x1001), "LO", "SomePrivateData")

    ds.save_as(str(filepath))
    return ds


# ---------------------------------------------------------------------------
# Fixture 1: Single-subject synthetic DICOM series (10 slices, T1)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def synthetic_dicom_series(tmp_path_factory):
    """Create a synthetic DICOM series: 1 subject, 1 T1 series, 10 slices.

    All files share the same StudyInstanceUID and SeriesInstanceUID
    (as real DICOM series do). PII tags are planted for deidentify tests.

    Returns:
        Path to directory containing 10 .dcm files.
    """
    dicom_dir = tmp_path_factory.mktemp("dicom_single")
    study_uid = generate_uid()
    series_uid = generate_uid()

    for i in range(1, 11):
        filepath = dicom_dir / f"slice_{i:03d}.dcm"
        _create_synthetic_dicom(
            filepath,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            instance_number=i,
        )

    return dicom_dir


# ---------------------------------------------------------------------------
# Fixture 2: Two-subject synthetic DICOM series (T1 + FLAIR each)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def synthetic_dicom_two_subjects(tmp_path_factory):
    """Create synthetic DICOMs for 2 subjects x 2 series (T1 + FLAIR).

    Used by multi-subject conversion tests. Each subject gets their own
    StudyInstanceUID, and each series gets a unique SeriesInstanceUID.

    Returns:
        Path to directory with sub-directories per subject/series.
    """
    base_dir = tmp_path_factory.mktemp("dicom_multi")

    subjects = [
        ("sub-01", "Doe^Jane", "HOSP-001"),
        ("sub-02", "Doe^John", "HOSP-002"),
    ]
    series_types = [
        ("T1_MPRAGE", 5),    # 5 slices per series (enough for testing)
        ("FLAIR_3D", 5),
    ]

    for subject_id, patient_name, patient_id in subjects:
        subject_dir = base_dir / subject_id
        study_uid = generate_uid()

        for series_desc, num_slices in series_types:
            series_dir = subject_dir / series_desc
            series_dir.mkdir(parents=True)
            series_uid = generate_uid()

            for i in range(1, num_slices + 1):
                filepath = series_dir / f"slice_{i:03d}.dcm"
                _create_synthetic_dicom(
                    filepath,
                    patient_name=patient_name,
                    patient_id=patient_id,
                    series_description=series_desc,
                    study_instance_uid=study_uid,
                    series_instance_uid=series_uid,
                    instance_number=i,
                )

    return base_dir


# ---------------------------------------------------------------------------
# Fixture 3: Pre-built valid BIDS dataset
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_bids_dataset(tmp_path_factory):
    """Create a valid BIDS dataset directory for testing verify/audit/report.

    This creates the full BIDS structure directly (no dcm2niix needed):
    - dataset_description.json with all required and recommended fields
    - participants.tsv and participants.json (data dictionary)
    - README and LICENSE files
    - One subject (sub-01) with T1w and FLAIR NIfTI files + JSON sidecars

    Returns:
        Path to the BIDS root directory.
    """
    bids_root = tmp_path_factory.mktemp("bids_dataset")

    # --- Root metadata files ---

    # dataset_description.json — required by BIDS, checked by FAIR audit
    dataset_desc = {
        "Name": "Test Neuroimaging Dataset",
        "BIDSVersion": "1.9.0",
        "DatasetType": "raw",
        "License": "CC0-1.0",
        "Authors": ["Test Author"],
        "GeneratedBy": [
            {
                "Name": "neuro-curation",
                "Version": "0.1.0",
            }
        ],
    }
    (bids_root / "dataset_description.json").write_text(
        json.dumps(dataset_desc, indent=2)
    )

    # participants.tsv — tabular subject metadata (FAIR: Reusable)
    participants_tsv = "participant_id\tage\tsex\nsub-01\t79\tF\n"
    (bids_root / "participants.tsv").write_text(participants_tsv)

    # participants.json — data dictionary describing columns (FAIR: Reusable)
    participants_json = {
        "participant_id": {"Description": "Unique subject identifier"},
        "age": {"Description": "Age in years", "Units": "years"},
        "sex": {"Description": "Biological sex", "Levels": {"M": "Male", "F": "Female"}},
    }
    (bids_root / "participants.json").write_text(
        json.dumps(participants_json, indent=2)
    )

    # README — human-readable dataset description (FAIR: Accessible)
    (bids_root / "README").write_text(
        "Test Neuroimaging Dataset\n\n"
        "This is a synthetic BIDS dataset created for automated testing.\n"
        "It contains minimal NIfTI images with no real brain data.\n"
    )

    # LICENSE — data reuse terms (FAIR: Reusable)
    (bids_root / "LICENSE").write_text(
        "CC0 1.0 Universal\n\n"
        "This dataset is released into the public domain.\n"
    )

    # --- Subject data: sub-01 with T1w and FLAIR ---

    anat_dir = bids_root / "sub-01" / "anat"
    anat_dir.mkdir(parents=True)

    # T1w NIfTI + JSON sidecar
    create_minimal_nifti(anat_dir / "sub-01_T1w.nii.gz")
    (anat_dir / "sub-01_T1w.json").write_text(
        json.dumps({
            "Modality": "MR",
            "MagneticFieldStrength": 3,
            "Manufacturer": "Siemens",
            "ManufacturersModelName": "Prisma",
            "SeriesDescription": "T1_MPRAGE",
        }, indent=2)
    )

    # FLAIR NIfTI + JSON sidecar
    create_minimal_nifti(anat_dir / "sub-01_FLAIR.nii.gz")
    (anat_dir / "sub-01_FLAIR.json").write_text(
        json.dumps({
            "Modality": "MR",
            "MagneticFieldStrength": 3,
            "Manufacturer": "Siemens",
            "ManufacturersModelName": "Prisma",
            "SeriesDescription": "FLAIR_3D",
        }, indent=2)
    )

    return bids_root


# ---------------------------------------------------------------------------
# Utility fixture: copy a session-scoped fixture to a test-local tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture
def dicom_copy(synthetic_dicom_series, tmp_path):
    """Copy the synthetic DICOM series to a test-local directory.

    Use this when the test needs to modify DICOM files (e.g., deidentify
    writes output files). This avoids mutating the shared session fixture.

    Returns:
        Tuple of (input_dir, output_dir) — input contains the DICOM copies,
        output is an empty directory for deidentified results.
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    shutil.copytree(synthetic_dicom_series, input_dir)
    output_dir.mkdir()
    return input_dir, output_dir


@pytest.fixture
def bids_copy(sample_bids_dataset, tmp_path):
    """Copy the BIDS dataset to a test-local directory.

    Use this when the test needs to modify the BIDS dataset (e.g., deleting
    files to test audit failure scenarios).

    Returns:
        Path to the copied BIDS root directory.
    """
    bids_dir = tmp_path / "bids"
    shutil.copytree(sample_bids_dataset, bids_dir)
    return bids_dir

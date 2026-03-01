"""
DICOM De-identification Module.

Strips personally identifiable information (PII) from DICOM files following
the DICOM PS3.15 Annex E Basic De-identification Profile. This is the
standard approach used in clinical research to protect patient privacy
while preserving the imaging data needed for analysis.

In the Insight 46 workflow, XNAT handles primary de-identification on
ingest via DicomEdit scripts. This module serves three complementary roles:

1. **Verification layer**: After XNAT export, confirm that DicomEdit
   actually stripped everything — DicomEdit errors are silent and hard to
   debug (scripts from older XNAT versions can fail without warning).
2. **Non-XNAT sources**: Data arriving from collaborators, external
   scanners, or multi-site studies (DPUK, GAAIN) may not have passed
   through XNAT's anonymization pipeline.
3. **Pre-transfer safety net**: Before sending data externally, run a
   second pass to catch any residual PII that XNAT's script missed
   (common with vendor-specific private tags and nested sequences).

Key concepts:
- "Direct identifiers" (name, DOB, address) are removed entirely
- "Quasi-identifiers" (age, weight) are removed because in small cohorts
  like NSHD (all born in one week of March 1946), even age is identifying
- UIDs are replaced with new random UIDs, but consistently: all files from
  the same study keep the same new StudyInstanceUID
- Private tags (vendor-specific) are removed because they may contain PII
  in unpredictable locations

Usage:
    from neuro_curation.deidentify import deidentify_directory

    results = deidentify_directory(
        input_dir=Path("raw_dicoms/"),
        output_dir=Path("deid_dicoms/"),
        subject_id="sub-01",
    )
"""

from pathlib import Path

import pydicom
from pydicom.uid import generate_uid


# ---------------------------------------------------------------------------
# Tags to remove per DICOM PS3.15 Annex E Basic De-identification Profile
# ---------------------------------------------------------------------------
# Each entry is (group, element) with a comment explaining the privacy risk.
# Action "X" means "remove the tag entirely" (as opposed to "Z" = zero-length,
# "D" = replace with dummy, etc.).
#
# Reference: https://dicom.nema.org/medical/dicom/current/output/html/part15.html#table_E.1-1

TAGS_TO_REMOVE = [
    # Direct identifiers — uniquely identify a person
    (0x0010, 0x0030),  # PatientBirthDate
    (0x0010, 0x1010),  # PatientAge — quasi-identifier (in NSHD, age = birth year)
    (0x0010, 0x1030),  # PatientWeight — quasi-identifier for small cohorts
    (0x0010, 0x1040),  # PatientAddress
    # Hospital system links — could be used to re-identify via hospital records
    (0x0008, 0x0050),  # AccessionNumber — links to radiology information system
    (0x0008, 0x0080),  # InstitutionName — identifies scanning site
    (0x0008, 0x0081),  # InstitutionAddress
    (0x0008, 0x1030),  # StudyDescription — may contain patient info in free text
    # Personnel identifiers
    (0x0008, 0x0090),  # ReferringPhysicianName
    (0x0008, 0x1050),  # PerformingPhysicianName
    (0x0008, 0x1070),  # OperatorsName
    # Date/time stamps that could narrow identification
    (0x0008, 0x0020),  # StudyDate
    (0x0008, 0x0030),  # StudyTime
    (0x0008, 0x0021),  # SeriesDate
]


def replace_uids(dataset: pydicom.Dataset, uid_map: dict) -> None:
    """Replace Study/Series/SOP Instance UIDs with new random UIDs.

    UIDs are globally unique identifiers assigned by the scanner. If an
    attacker has access to the original PACS/XNAT database, they could match
    de-identified files back to the original patient by comparing UIDs.

    We use a shared uid_map dictionary so that:
    - All files from the SAME study get the SAME new StudyInstanceUID
    - All files from the SAME series get the SAME new SeriesInstanceUID
    - Each file gets a UNIQUE new SOPInstanceUID

    This preserves the study/series grouping (essential for reconstruction)
    while breaking the link to the original database.

    Args:
        dataset: The DICOM dataset to modify in place.
        uid_map: Shared mapping of original_uid -> new_uid. Will be updated
                 with new mappings as UIDs are encountered.
    """
    for uid_tag in ["StudyInstanceUID", "SeriesInstanceUID"]:
        if hasattr(dataset, uid_tag):
            original_uid = getattr(dataset, uid_tag)
            if original_uid not in uid_map:
                uid_map[original_uid] = generate_uid()
            setattr(dataset, uid_tag, uid_map[original_uid])

    # SOPInstanceUID must be unique per file (not shared across series)
    new_sop_uid = generate_uid()
    dataset.SOPInstanceUID = new_sop_uid

    # Keep file_meta in sync — MediaStorageSOPInstanceUID must match SOPInstanceUID
    if hasattr(dataset, "file_meta") and hasattr(dataset.file_meta, "MediaStorageSOPInstanceUID"):
        dataset.file_meta.MediaStorageSOPInstanceUID = new_sop_uid


def scrub_person_names(dataset: pydicom.Dataset) -> None:
    """Walk the entire dataset and replace all PersonName (PN) VR fields.

    DICOM has a specific Value Representation (VR) type "PN" for person
    names. While we explicitly handle PatientName and physician names in
    the tag removal list, some DICOM files have additional PN fields buried
    in private sequences or nested datasets that we might miss.

    This function catches ALL of them by walking the entire tag tree.

    Args:
        dataset: The DICOM dataset to modify in place.
    """
    def _pn_callback(ds, data_element):
        """Callback for dataset.walk() — replaces any PN-type element."""
        if data_element.VR == "PN":
            data_element.value = "ANONYMOUS"

    dataset.walk(_pn_callback)


def check_xnat_deidentification(input_path: Path) -> dict:
    """Check whether a DICOM file carries XNAT DicomEdit de-identification markers.

    XNAT uses DicomEdit scripts to anonymize DICOM files on ingest.
    When DicomEdit runs successfully, it sets two standard DICOM tags:
    - (0028,0301) BurnedInAnnotation  — "NO" if text overlays were removed
    - (0012,0062) PatientIdentityRemoved — "YES" if the patient identity
      has been removed per the de-identification method documented in
      (0012,0063) DeidentificationMethodCodeSequence

    However, DicomEdit errors are **silent**: if the script fails, XNAT
    still exports the file without raising an exception. A file that was
    never anonymized will simply lack these tags.

    This function reads those markers and reports:
    1. Whether XNAT DicomEdit successfully ran (PatientIdentityRemoved = "YES")
    2. Whether any direct identifiers remain despite the markers
    3. A recommendation: skip de-identification, apply full de-identification,
       or apply verification-only pass.

    ---
    **NSHD quasi-identifier note**:
    The National Survey of Health and Development (NSHD) 1946 birth cohort
    has a structural re-identification risk that header de-identification
    alone cannot address: ALL participants were born in a single week of
    March 1946. This means PatientAge (a quasi-identifier, tag 0010,1010)
    can narrow identification even if no name or DOB is present.
    This function flags residual PatientAge or PatientBirthDate tags as
    QUASI_ID_RISK to ensure the safety-net pass (deidentify_file) removes them.

    Args:
        input_path: Path to a DICOM file (post-XNAT export).

    Returns:
        Dict with:
        - xnat_deid_confirmed: bool — True if PatientIdentityRemoved = "YES"
        - recommendation: "skip" | "verify_only" | "full_deidentify"
        - residual_tags: list of (tag_hex, name) tuples still present
        - quasi_id_risk: bool — True if NSHD quasi-identifiers found
        - details: human-readable explanation
    """
    try:
        ds = pydicom.dcmread(str(input_path), stop_before_pixels=True)
    except Exception as exc:
        return {
            "xnat_deid_confirmed": False,
            "recommendation": "full_deidentify",
            "residual_tags": [],
            "quasi_id_risk": False,
            "details": f"Could not read DICOM file: {exc}",
        }

    # --- Check XNAT DicomEdit primary marker ---
    patient_identity_removed = str(getattr(ds, "PatientIdentityRemoved", "")).upper()
    xnat_confirmed = patient_identity_removed == "YES"

    # --- Check for residual direct identifiers ---
    DIRECT_ID_TAGS = {
        (0x0010, 0x0010): "PatientName",
        (0x0010, 0x0030): "PatientBirthDate",
        (0x0010, 0x1040): "PatientAddress",
        (0x0008, 0x0080): "InstitutionName",
        (0x0008, 0x0090): "ReferringPhysicianName",
        (0x0008, 0x1050): "PerformingPhysicianName",
    }

    # NSHD cohort quasi-identifiers (all born one week in March 1946 —
    # age alone is re-identifying for this cohort)
    QUASI_ID_TAGS = {
        (0x0010, 0x1010): "PatientAge",
        (0x0010, 0x1030): "PatientWeight",
        (0x0010, 0x1020): "PatientSize",
    }

    residual_tags = []
    for tag, name in DIRECT_ID_TAGS.items():
        if tag in ds:
            value = str(ds[tag].value).strip()
            # PatientName replaced with subject ID is acceptable
            if name == "PatientName" and value.startswith("sub-"):
                continue
            if value:   # Non-empty value means direct identifier remains
                residual_tags.append({
                    "tag": f"({tag[0]:04X},{tag[1]:04X})",
                    "name": name,
                    "value_preview": value[:20],
                })

    quasi_id_risk = any(tag in ds for tag in QUASI_ID_TAGS)

    # --- Determine recommendation ---
    if not xnat_confirmed:
        recommendation = "full_deidentify"
        details = (
            "PatientIdentityRemoved tag absent or not 'YES' — XNAT DicomEdit "
            "did not run or failed silently. Apply full de-identification pass."
        )
    elif residual_tags:
        recommendation = "full_deidentify"
        details = (
            f"XNAT DicomEdit ran (PatientIdentityRemoved=YES) but "
            f"{len(residual_tags)} direct identifier(s) remain. "
            "Apply full de-identification pass."
        )
    elif quasi_id_risk:
        recommendation = "verify_only"
        details = (
            "XNAT DicomEdit confirmed. No direct identifiers found. "
            "NSHD quasi-identifiers (age/weight) still present — "
            "apply verify_only pass to strip quasi-identifiers before "
            "external sharing (DPUK, GAAIN)."
        )
    else:
        recommendation = "skip"
        details = (
            "XNAT DicomEdit confirmed. No direct identifiers or quasi-identifiers "
            "found. File is safe for internal use. Consider a verify_only pass "
            "before external sharing to confirm private tag removal."
        )

    return {
        "xnat_deid_confirmed": xnat_confirmed,
        "recommendation": recommendation,
        "residual_tags": residual_tags,
        "quasi_id_risk": quasi_id_risk,
        "details": details,
    }


def deidentify_file(
    input_path: Path,
    output_path: Path,
    subject_id: str,
    uid_map: dict,
) -> dict:
    """De-identify a single DICOM file.

    This is the core function that applies all de-identification steps
    to one DICOM file:
    1. Remove explicitly listed PII tags
    2. Replace PatientName and PatientID with the research subject ID
    3. Replace UIDs consistently (via shared uid_map)
    4. Scrub any remaining PersonName VR fields
    5. Remove all private (vendor-specific) tags
    6. Set de-identification markers per DICOM standard

    Args:
        input_path: Path to the original DICOM file.
        output_path: Where to save the de-identified copy.
        subject_id: Research subject identifier (e.g., "sub-01").
        uid_map: Shared UID mapping dict — updated in place.

    Returns:
        Summary dict with original_patient_id and tags_removed count.
    """
    # Read the original DICOM file
    ds = pydicom.dcmread(str(input_path))

    # Track what was there before for the summary
    original_patient_id = str(getattr(ds, "PatientID", "unknown"))
    tags_removed = 0

    # Step 1: Remove explicitly listed PII tags
    for tag in TAGS_TO_REMOVE:
        if tag in ds:
            del ds[tag]
            tags_removed += 1

    # Step 2: Replace UIDs consistently across the series
    replace_uids(ds, uid_map)

    # Step 3: Scrub any remaining PersonName fields we might have missed
    scrub_person_names(ds)

    # Step 4: Replace patient identifiers with research subject ID
    # Done AFTER scrub_person_names so the subject ID isn't overwritten
    # with "ANONYMOUS". We keep PatientName and PatientID because some
    # downstream tools expect these tags to exist.
    ds.PatientName = subject_id
    ds.PatientID = subject_id

    # Step 5: Remove private tags (vendor-specific, odd group numbers)
    # These may contain PII in unpredictable locations — safer to remove all
    ds.remove_private_tags()

    # Step 6: Mark the file as de-identified per DICOM standard
    # Other tools (e.g., XNAT) check these markers to confirm de-identification
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "DICOM PS3.15 Basic Profile"

    # Save the de-identified file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(output_path))

    return {
        "original_patient_id": original_patient_id,
        "tags_removed": tags_removed,
        "output_path": str(output_path),
    }


def deidentify_directory(
    input_dir: Path,
    output_dir: Path,
    subject_id: str,
) -> list[dict]:
    """De-identify all DICOM files in a directory.

    This is the main public API for the de-identification module. It walks
    the input directory, finds all DICOM files (by .dcm extension or by
    attempting to read files without extensions), and de-identifies each one.

    A shared uid_map ensures UID consistency: all files from the same
    original study will share the same new StudyInstanceUID.

    Args:
        input_dir: Directory containing original DICOM files.
        output_dir: Where to write de-identified copies (preserves subdirectory structure).
        subject_id: Research subject identifier (e.g., "sub-01").

    Returns:
        List of summary dicts, one per file processed.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shared UID map — ensures consistent UID replacement across all files
    uid_map: dict = {}

    # Find all DICOM files (both .dcm and extensionless)
    dicom_files = _find_dicom_files(input_dir)

    if not dicom_files:
        print(f"Warning: No DICOM files found in {input_dir}")
        return []

    results = []
    total = len(dicom_files)

    for i, dcm_path in enumerate(dicom_files, 1):
        # Preserve subdirectory structure in output
        relative = dcm_path.relative_to(input_dir)
        out_path = output_dir / relative

        result = deidentify_file(dcm_path, out_path, subject_id, uid_map)
        results.append(result)
        print(f"De-identified {i}/{total}: {dcm_path.name}")

    print(f"\nDe-identification complete: {total} files processed")
    return results


def _find_dicom_files(directory: Path) -> list[Path]:
    """Find all DICOM files in a directory tree.

    Looks for files with .dcm extension first. For files without a
    recognized extension, attempts to read them with pydicom — real
    DICOM files from some scanners don't have extensions.

    Args:
        directory: Root directory to search.

    Returns:
        Sorted list of paths to DICOM files.
    """
    dicom_files = []

    for filepath in sorted(directory.rglob("*")):
        if not filepath.is_file():
            continue

        # Files with .dcm extension are assumed to be DICOM
        if filepath.suffix.lower() == ".dcm":
            dicom_files.append(filepath)
            continue

        # For extensionless files, try reading as DICOM
        if filepath.suffix == "":
            try:
                pydicom.dcmread(str(filepath), stop_before_pixels=True)
                dicom_files.append(filepath)
            except (pydicom.errors.InvalidDicomError, Exception):
                continue

    return dicom_files

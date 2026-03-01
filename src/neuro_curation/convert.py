"""
DICOM to NIfTI Conversion + BIDS Organization Module.

Converts de-identified DICOM files to NIfTI format using dcm2niix and
organizes the output into a Brain Imaging Data Structure (BIDS) compliant
directory tree.

BIDS is the standard format for sharing neuroimaging data. It defines:
- A directory hierarchy: sub-XX/[ses-XX/]anat/ (or func/, dwi/, etc.)
- Filename conventions: sub-01_ses-01_T1w.nii.gz
- Required metadata files: dataset_description.json, participants.tsv
- JSON sidecars alongside each NIfTI with acquisition parameters

In the Insight 46 context, BIDS compliance is essential because:
- DPUK and GAAIN require BIDS-formatted submissions
- BIDS apps (fmriprep, freesurfer) expect this structure
- XNAT can export to BIDS, but the mapping often needs manual correction

Usage:
    from neuro_curation.convert import convert_subject

    convert_subject(
        input_dir=Path("deid_dicoms/"),
        output_dir=Path("bids_dataset/"),
        subject_id="sub-01",
        session_id="ses-01",
    )
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pydicom


# ---------------------------------------------------------------------------
# Series description to BIDS suffix mapping
# ---------------------------------------------------------------------------
# BIDS requires specific suffixes for each imaging modality. We map common
# SeriesDescription values (as used on Siemens scanners in Insight 46) to
# BIDS-compliant suffixes.
#
# Reference: https://bids-specification.readthedocs.io/en/stable/appendices/suffixes.html

SERIES_TO_BIDS_SUFFIX = {
    # Structural MRI
    "t1": "T1w",
    "mprage": "T1w",
    "t1_mprage": "T1w",
    "t1w": "T1w",
    "flair": "FLAIR",
    "flair_3d": "FLAIR",
    "t2": "T2w",
    "t2_tse": "T2w",
    "t2w": "T2w",
    # Diffusion
    "dwi": "dwi",
    "diffusion": "dwi",
    "dti": "dwi",
    # Functional
    "bold": "bold",
    "resting": "bold",
    "resting_state": "bold",
    "fmri": "bold",
}


def detect_bids_suffix(series_description: str) -> str:
    """Map a DICOM SeriesDescription to a BIDS filename suffix.

    Uses case-insensitive substring matching against known patterns.
    This handles the fact that scanner protocols have inconsistent naming
    across sites and vendors (e.g., "T1_MPRAGE", "t1_mprage_sag",
    "MPRAGE_GRAPPA2").

    Args:
        series_description: The SeriesDescription tag from the DICOM header.

    Returns:
        BIDS suffix string (e.g., "T1w", "FLAIR", "dwi"). Returns "unknown"
        if no pattern matches, with a warning printed.
    """
    normalized = series_description.lower().strip()

    # Try exact match first, then substring match
    if normalized in SERIES_TO_BIDS_SUFFIX:
        return SERIES_TO_BIDS_SUFFIX[normalized]

    for pattern, suffix in SERIES_TO_BIDS_SUFFIX.items():
        if pattern in normalized:
            return suffix

    print(f"Warning: Unknown series description '{series_description}', using 'unknown'")
    return "unknown"


def run_dcm2niix(input_dir: Path, output_dir: Path) -> list[Path]:
    """Convert DICOM files to NIfTI using dcm2niix.

    dcm2niix is the gold-standard tool for DICOM-to-NIfTI conversion,
    written by Chris Rorden. It handles vendor-specific quirks, creates
    BIDS-compatible JSON sidecars, and compresses output to .nii.gz.

    Args:
        input_dir: Directory containing DICOM files.
        output_dir: Where to write NIfTI + JSON output.

    Returns:
        List of paths to generated .nii.gz files.

    Raises:
        FileNotFoundError: If dcm2niix is not installed.
        subprocess.CalledProcessError: If dcm2niix fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "dcm2niix",
        "-z", "y",       # Compress output to .nii.gz (saves ~70% disk space)
        "-b", "y",       # Generate BIDS JSON sidecar with acquisition parameters
        "-ba", "y",      # Anonymize the JSON sidecar (strip patient info)
        "-f", "%p_%s",   # Filename pattern: protocol_seriesNumber
        "-o", str(output_dir),
        str(input_dir),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        print(f"dcm2niix output:\n{result.stdout}")
    except FileNotFoundError:
        raise FileNotFoundError(
            "dcm2niix not found. Install it with:\n"
            "  macOS:  brew install dcm2niix\n"
            "  Ubuntu: apt install dcm2niix\n"
            "  conda:  conda install -c conda-forge dcm2niix"
        )

    # Return all generated NIfTI files
    return sorted(output_dir.glob("*.nii.gz"))


def organize_bids(
    nifti_dir: Path,
    bids_root: Path,
    subject_id: str,
    session_id: str | None = None,
) -> list[Path]:
    """Move flat dcm2niix output into BIDS directory structure.

    dcm2niix outputs flat files like "T1_MPRAGE_4.nii.gz". This function
    renames and moves them into the BIDS hierarchy:
        sub-01/[ses-01/]anat/sub-01[_ses-01]_T1w.nii.gz

    It reads the JSON sidecar to determine the SeriesDescription, then
    maps that to the correct BIDS suffix and datatype directory.

    Args:
        nifti_dir: Directory containing dcm2niix output (.nii.gz + .json).
        bids_root: Root of the BIDS dataset.
        subject_id: Subject identifier (e.g., "sub-01").
        session_id: Optional session identifier (e.g., "ses-01").

    Returns:
        List of paths to the organized NIfTI files in BIDS structure.
    """
    organized_files = []

    # Track suffixes to handle duplicates (add _run-XX entity)
    suffix_counts: dict[str, int] = {}

    for nifti_path in sorted(nifti_dir.glob("*.nii.gz")):
        json_path = nifti_path.with_suffix("").with_suffix(".json")

        # Determine BIDS suffix from JSON sidecar or filename
        series_desc = _get_series_description(json_path, nifti_path)
        bids_suffix = detect_bids_suffix(series_desc)

        # Determine the BIDS datatype directory (anat, func, dwi, etc.)
        datatype = _suffix_to_datatype(bids_suffix)

        # Build the BIDS path
        # Format: sub-01/[ses-01/]anat/sub-01[_ses-01][_run-01]_T1w.nii.gz
        parts = [subject_id]
        if session_id:
            parts.append(session_id)

        # Handle duplicate modalities with run entity
        suffix_counts[bids_suffix] = suffix_counts.get(bids_suffix, 0) + 1
        if suffix_counts[bids_suffix] > 1:
            parts.append(f"run-{suffix_counts[bids_suffix]:02d}")

        # Build directory and filename
        subject_dir = bids_root / subject_id
        if session_id:
            subject_dir = subject_dir / session_id
        dest_dir = subject_dir / datatype
        dest_dir.mkdir(parents=True, exist_ok=True)

        bids_stem = "_".join(parts) + f"_{bids_suffix}"
        dest_nifti = dest_dir / f"{bids_stem}.nii.gz"
        dest_json = dest_dir / f"{bids_stem}.json"

        # Move files to BIDS location
        shutil.move(str(nifti_path), str(dest_nifti))
        if json_path.exists():
            shutil.move(str(json_path), str(dest_json))

        organized_files.append(dest_nifti)
        print(f"  {nifti_path.name} -> {dest_nifti.relative_to(bids_root)}")

    return organized_files


def create_bids_metadata(
    bids_root: Path,
    dataset_name: str = "Neuroimaging Dataset",
) -> None:
    """Create required BIDS root-level metadata files.

    BIDS mandates certain files at the dataset root. Each file maps to
    a FAIR principle:
    - dataset_description.json -> Findable (persistent metadata)
    - README -> Accessible (human-readable description)
    - participants.tsv -> Reusable (structured subject metadata)
    - participants.json -> Reusable (data dictionary for participants.tsv)

    Args:
        bids_root: Root directory of the BIDS dataset.
        dataset_name: Human-readable name for the dataset.
    """
    bids_root.mkdir(parents=True, exist_ok=True)

    # dataset_description.json — required by BIDS spec
    # This is the "identity card" of the dataset
    desc_path = bids_root / "dataset_description.json"
    if not desc_path.exists():
        dataset_desc = {
            "Name": dataset_name,
            "BIDSVersion": "1.9.0",
            "DatasetType": "raw",
            "License": "CC0-1.0",
            "GeneratedBy": [
                {
                    "Name": "neuro-curation",
                    "Version": "0.1.0",
                    "Description": "Reproducible Neuroimaging Curation & Transfer Pipeline",
                }
            ],
        }
        desc_path.write_text(json.dumps(dataset_desc, indent=2) + "\n")
        print(f"  Created {desc_path.name}")

    # README — human-readable dataset description
    readme_path = bids_root / "README"
    if not readme_path.exists():
        readme_path.write_text(
            f"{dataset_name}\n\n"
            f"This dataset was processed by the neuro-curation pipeline.\n"
            f"It contains de-identified neuroimaging data in BIDS format.\n\n"
            f"For questions about this dataset, contact the data steward.\n"
        )
        print(f"  Created {readme_path.name}")

    # participants.tsv — tabular subject metadata (created empty with headers)
    tsv_path = bids_root / "participants.tsv"
    if not tsv_path.exists():
        tsv_path.write_text("participant_id\tage\tsex\n")
        print(f"  Created {tsv_path.name}")

    # participants.json — data dictionary describing the TSV columns
    # This is a BIDS recommendation (not requirement) but essential for FAIR
    json_path = bids_root / "participants.json"
    if not json_path.exists():
        data_dict = {
            "participant_id": {
                "Description": "Unique subject identifier",
            },
            "age": {
                "Description": "Age of participant at time of scan",
                "Units": "years",
            },
            "sex": {
                "Description": "Biological sex of participant",
                "Levels": {"M": "Male", "F": "Female"},
            },
        }
        json_path.write_text(json.dumps(data_dict, indent=2) + "\n")
        print(f"  Created {json_path.name}")


def update_participants_tsv(
    bids_root: Path,
    subject_id: str,
    age: str = "n/a",
    sex: str = "n/a",
) -> None:
    """Add or update a subject entry in participants.tsv.

    Args:
        bids_root: Root directory of the BIDS dataset.
        subject_id: Subject identifier (e.g., "sub-01").
        age: Age value (or "n/a" if not available).
        sex: Sex value (or "n/a" if not available).
    """
    tsv_path = bids_root / "participants.tsv"

    # Read existing entries
    existing_lines = []
    if tsv_path.exists():
        existing_lines = tsv_path.read_text().strip().split("\n")

    # Check if subject already exists
    for line in existing_lines[1:]:  # Skip header
        if line.startswith(subject_id):
            return  # Already present

    # Add the new subject
    if not existing_lines:
        existing_lines = ["participant_id\tage\tsex"]
    existing_lines.append(f"{subject_id}\t{age}\t{sex}")
    tsv_path.write_text("\n".join(existing_lines) + "\n")


def convert_subject(
    input_dir: Path,
    output_dir: Path,
    subject_id: str,
    session_id: str | None = None,
    dataset_name: str = "Neuroimaging Dataset",
) -> Path:
    """Convert DICOM files for one subject to BIDS format.

    This is the main public API. It chains:
    1. dcm2niix: DICOM -> NIfTI + JSON sidecars (in temp dir)
    2. organize_bids: move into BIDS directory structure
    3. create_bids_metadata: generate root-level BIDS files
    4. update_participants_tsv: add subject entry

    Args:
        input_dir: Directory containing de-identified DICOM files.
        output_dir: Root of the BIDS dataset to create/update.
        subject_id: Subject identifier (e.g., "sub-01").
        session_id: Optional session identifier (e.g., "ses-01").
        dataset_name: Human-readable name for the dataset.

    Returns:
        Path to the BIDS root directory.
    """
    output_dir = Path(output_dir)

    print(f"Converting DICOMs for {subject_id}...")

    # Step 1: Run dcm2niix to a temporary directory
    # We use a temp dir because dcm2niix outputs flat files that need
    # to be reorganized into the BIDS hierarchy
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        print("  Running dcm2niix...")
        nifti_files = run_dcm2niix(input_dir, tmp_path)

        if not nifti_files:
            print("  Warning: dcm2niix produced no NIfTI files")
            return output_dir

        # Step 2: Organize into BIDS directory structure
        print("  Organizing into BIDS structure...")
        organize_bids(tmp_path, output_dir, subject_id, session_id)

    # Step 3: Create root-level BIDS metadata (if not already present)
    print("  Creating BIDS metadata...")
    create_bids_metadata(output_dir, dataset_name)

    # Step 4: Add subject to participants.tsv
    update_participants_tsv(output_dir, subject_id)

    print(f"Conversion complete for {subject_id}")
    return output_dir


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_series_description(json_path: Path, nifti_path: Path) -> str:
    """Extract SeriesDescription from JSON sidecar or infer from filename.

    Args:
        json_path: Path to the dcm2niix JSON sidecar.
        nifti_path: Path to the NIfTI file (used as fallback).

    Returns:
        SeriesDescription string.
    """
    if json_path.exists():
        try:
            with open(json_path) as f:
                metadata = json.load(f)
            if "SeriesDescription" in metadata:
                return metadata["SeriesDescription"]
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: extract from filename (dcm2niix uses protocol name)
    # e.g., "T1_MPRAGE_4.nii.gz" -> "T1_MPRAGE"
    stem = nifti_path.name.replace(".nii.gz", "")
    # Remove trailing _number (series number added by dcm2niix)
    stem = re.sub(r"_\d+$", "", stem)
    return stem


def _suffix_to_datatype(bids_suffix: str) -> str:
    """Map a BIDS suffix to its datatype directory.

    BIDS organizes files into datatype directories:
    - anat/ for structural images (T1w, T2w, FLAIR)
    - func/ for functional images (bold)
    - dwi/ for diffusion images
    - unknown/ as fallback

    Args:
        bids_suffix: BIDS filename suffix (e.g., "T1w", "bold").

    Returns:
        Datatype directory name.
    """
    datatype_map = {
        "T1w": "anat",
        "T2w": "anat",
        "FLAIR": "anat",
        "bold": "func",
        "dwi": "dwi",
    }
    return datatype_map.get(bids_suffix, "unknown")

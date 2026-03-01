"""
Data Integrity Verification Module.

Generates and verifies SHA-256 checksums for all files in a dataset,
producing a transfer manifest that can be used to confirm data integrity
after moving files between systems.

In the Insight 46 context, data is regularly transferred between:
- The MRI scanner → XNAT (internal archive)
- XNAT → DPUK (Dementias Platform UK, for sharing)
- XNAT → GAAIN (Global Alzheimer's Association Interactive Network)
- Local workstations ← XNAT (for analysis)

Each transfer risks silent data corruption (network errors, disk faults,
incomplete copies). A transfer manifest with cryptographic checksums lets
the receiving end verify that every file arrived intact.

Why SHA-256 (not MD5)?
- MD5 has known collision vulnerabilities (two different files can produce
  the same hash). While unlikely in practice for accidental corruption,
  SHA-256 is the modern standard for research data integrity.
- NIH, Wellcome Trust, and UKRI data management plans recommend SHA-256.

Usage:
    from neuro_curation.verify import generate_manifest, verify_manifest

    # After creating/transferring a dataset:
    manifest = generate_manifest(Path("bids_dataset/"))

    # On the receiving end, verify integrity:
    report = verify_manifest(Path("bids_dataset/"))
    print(format_verification_report(report))
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from neuro_curation import __version__


def compute_sha256(filepath: Path) -> str:
    """Compute the SHA-256 hash of a file using chunked reads.

    Reads the file in 8192-byte chunks to avoid loading entire large
    NIfTI files (~200MB uncompressed) into memory at once.

    Args:
        filepath: Path to the file to hash.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    sha256 = hashlib.sha256()

    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)

    return sha256.hexdigest()


def generate_manifest(dataset_dir: Path) -> dict:
    """Generate a transfer manifest with checksums for all files.

    Walks the dataset directory, computes SHA-256 for every file, and
    saves the result as transfer_manifest.json in the dataset root.

    The manifest includes:
    - Metadata: when it was generated, by which tool, which algorithm
    - Per-file: relative path, hash, size in bytes, last modified time

    Args:
        dataset_dir: Root directory of the dataset.

    Returns:
        The manifest dict (also saved to transfer_manifest.json).
    """
    dataset_dir = Path(dataset_dir)
    files = []

    # Walk all files in the dataset (excluding the manifest itself)
    for filepath in sorted(dataset_dir.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.name == "transfer_manifest.json":
            continue

        relative_path = filepath.relative_to(dataset_dir).as_posix()
        stat = filepath.stat()

        files.append({
            "path": relative_path,
            "sha256": compute_sha256(filepath),
            "size_bytes": stat.st_size,
            "last_modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        })

    manifest = {
        "dataset_root": dataset_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": "neuro-curation",
        "tool_version": __version__,
        "hash_algorithm": "SHA-256",
        "total_files": len(files),
        "files": files,
    }

    # Save the manifest alongside the dataset
    manifest_path = dataset_dir / "transfer_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Manifest generated: {manifest_path}")
    print(f"  {len(files)} files checksummed")

    return manifest


def verify_manifest(dataset_dir: Path) -> dict:
    """Verify dataset integrity by re-computing checksums against the manifest.

    Reads the transfer_manifest.json, re-computes SHA-256 for every listed
    file, and compares. Reports any mismatches (corruption) or missing files
    (incomplete transfer).

    Args:
        dataset_dir: Root directory of the dataset (must contain
                     transfer_manifest.json).

    Returns:
        Verification report dict with:
        - files_checked: number of files verified
        - mismatches: list of files with hash mismatches
        - missing: list of files in manifest but not on disk
        - passed: bool, True if all files match
    """
    dataset_dir = Path(dataset_dir)
    manifest_path = dataset_dir / "transfer_manifest.json"

    if not manifest_path.exists():
        return {
            "files_checked": 0,
            "mismatches": [],
            "missing": [],
            "passed": False,
            "error": "transfer_manifest.json not found",
        }

    manifest = json.loads(manifest_path.read_text())
    mismatches = []
    missing = []

    for entry in manifest["files"]:
        filepath = dataset_dir / entry["path"]

        if not filepath.exists():
            missing.append(entry["path"])
            continue

        actual_hash = compute_sha256(filepath)
        if actual_hash != entry["sha256"]:
            mismatches.append({
                "path": entry["path"],
                "expected": entry["sha256"],
                "actual": actual_hash,
            })

    files_checked = len(manifest["files"]) - len(missing)
    passed = len(mismatches) == 0 and len(missing) == 0

    return {
        "files_checked": files_checked,
        "total_files": len(manifest["files"]),
        "mismatches": mismatches,
        "missing": missing,
        "passed": passed,
    }


def format_verification_report(results: dict) -> str:
    """Format a verification report as a human-readable string.

    Args:
        results: Output from verify_manifest().

    Returns:
        Formatted multi-line string with pass/fail status.
    """
    lines = ["=== Data Integrity Verification ===", ""]

    if "error" in results:
        lines.append(f"ERROR: {results['error']}")
        return "\n".join(lines)

    total = results["total_files"]
    checked = results["files_checked"]

    if results["missing"]:
        lines.append(f"MISSING FILES ({len(results['missing'])}):")
        for path in results["missing"]:
            lines.append(f"  [MISSING] {path}")
        lines.append("")

    if results["mismatches"]:
        lines.append(f"HASH MISMATCHES ({len(results['mismatches'])}):")
        for m in results["mismatches"]:
            lines.append(f"  [FAIL] {m['path']}")
            lines.append(f"         Expected: {m['expected'][:16]}...")
            lines.append(f"         Actual:   {m['actual'][:16]}...")
        lines.append("")

    if results["passed"]:
        lines.append(f"RESULT: PASS — {checked}/{total} files verified successfully")
    else:
        failed = len(results["mismatches"]) + len(results["missing"])
        lines.append(f"RESULT: FAIL — {failed} issue(s) found in {total} files")

    return "\n".join(lines)

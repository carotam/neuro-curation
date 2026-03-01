"""
FAIR Compliance Audit Module.

Checks a BIDS dataset against the FAIR data principles:
- Findable: persistent metadata (dataset_description.json)
- Accessible: human-readable documentation (README)
- Interoperable: standard format (BIDS naming, JSON sidecars, .nii.gz)
- Reusable: licensing and data dictionaries (LICENSE, participants.tsv/json)

The FAIR principles (Wilkinson et al., 2016) are the gold standard for
research data management. Funders like Wellcome Trust and UKRI require
FAIR compliance in data management plans. At UCL's DRC, FAIR compliance
ensures that Insight 46 data can be:
- Found by other researchers via metadata catalogues
- Accessed through clear documentation and protocols
- Combined with data from other studies (DPUK, GAAIN)
- Reused under well-defined licensing terms

Each check produces a FairCheck result with:
- The FAIR principle it tests (F, A, I, R)
- Whether it passed or failed
- A human-readable explanation
- Severity (required vs recommended)

Usage:
    from neuro_curation.audit import run_audit, format_audit_report

    results = run_audit(Path("bids_dataset/"))
    print(format_audit_report(results))
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FairCheck:
    """Result of a single FAIR compliance check.

    Attributes:
        principle: Which FAIR principle this tests ("F", "A", "I", or "R").
        criterion: Short description of what was checked.
        passed: Whether the check passed.
        message: Human-readable explanation of the result.
        severity: "required" (must-have) or "recommended" (nice-to-have).
    """

    principle: str
    criterion: str
    passed: bool
    message: str
    severity: str = "required"


# ---------------------------------------------------------------------------
# FAIR principle checks
# ---------------------------------------------------------------------------


def check_findable(dataset_dir: Path) -> list[FairCheck]:
    """Check Findable (F) criteria: persistent, structured metadata.

    FAIR F requires that data has rich metadata that can be indexed by
    search engines and data catalogues. In BIDS, this is primarily
    dataset_description.json.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        List of FairCheck results for Findable criteria.
    """
    checks = []
    desc_path = dataset_dir / "dataset_description.json"

    # F1: dataset_description.json exists
    if desc_path.exists():
        checks.append(FairCheck(
            principle="F",
            criterion="dataset_description.json exists",
            passed=True,
            message="Dataset metadata file found",
        ))

        # Parse and check required fields
        try:
            desc = json.loads(desc_path.read_text())
        except json.JSONDecodeError:
            checks.append(FairCheck(
                principle="F",
                criterion="dataset_description.json is valid JSON",
                passed=False,
                message="File exists but contains invalid JSON",
            ))
            return checks

        # F2: Required field "Name"
        has_name = "Name" in desc and len(str(desc["Name"])) > 0
        checks.append(FairCheck(
            principle="F",
            criterion="Required field 'Name' present",
            passed=has_name,
            message="Dataset name: " + str(desc.get("Name", "MISSING")),
        ))

        # F3: Required field "BIDSVersion"
        has_version = "BIDSVersion" in desc
        checks.append(FairCheck(
            principle="F",
            criterion="Required field 'BIDSVersion' present",
            passed=has_version,
            message=f"BIDS version: {desc.get('BIDSVersion', 'MISSING')}",
        ))

        # F4: Recommended field "Authors"
        has_authors = "Authors" in desc and len(desc["Authors"]) > 0
        checks.append(FairCheck(
            principle="F",
            criterion="Recommended field 'Authors' present",
            passed=has_authors,
            message="Authors listed" if has_authors else "No authors listed",
            severity="recommended",
        ))

    else:
        checks.append(FairCheck(
            principle="F",
            criterion="dataset_description.json exists",
            passed=False,
            message="Missing dataset_description.json — required by BIDS",
        ))

    return checks


def check_accessible(dataset_dir: Path) -> list[FairCheck]:
    """Check Accessible (A) criteria: human-readable documentation.

    FAIR A requires that data can be understood by humans, not just
    machines. A README file explains what the dataset is, how it was
    collected, and how to use it.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        List of FairCheck results for Accessible criteria.
    """
    checks = []
    readme_path = dataset_dir / "README"

    if readme_path.exists():
        content = readme_path.read_text()
        checks.append(FairCheck(
            principle="A",
            criterion="README exists",
            passed=True,
            message="README file found",
        ))

        # Check that README has meaningful content (not just a placeholder)
        has_content = len(content.strip()) > 50
        checks.append(FairCheck(
            principle="A",
            criterion="README has meaningful content",
            passed=has_content,
            message=f"README is {len(content)} characters"
            if has_content
            else "README is too short — add a dataset description",
            severity="recommended",
        ))
    else:
        checks.append(FairCheck(
            principle="A",
            criterion="README exists",
            passed=False,
            message="Missing README — required by BIDS",
        ))

    return checks


def check_interoperable(dataset_dir: Path) -> list[FairCheck]:
    """Check Interoperable (I) criteria: standard format compliance.

    FAIR I requires that data uses standard formats that other tools
    can read. For neuroimaging, this means BIDS naming conventions,
    NIfTI format (.nii.gz), and JSON sidecars with acquisition parameters.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        List of FairCheck results for Interoperable criteria.
    """
    checks = []

    # I1: Check for NIfTI files in compressed format
    nifti_gz = list(dataset_dir.rglob("*.nii.gz"))
    nifti_raw = list(dataset_dir.rglob("*.nii"))
    # Filter out .nii that are actually .nii.gz
    nifti_raw = [f for f in nifti_raw if not str(f).endswith(".nii.gz")]

    has_nifti = len(nifti_gz) > 0
    checks.append(FairCheck(
        principle="I",
        criterion="NIfTI files present in compressed format",
        passed=has_nifti,
        message=f"Found {len(nifti_gz)} .nii.gz files"
        if has_nifti
        else "No .nii.gz files found",
    ))

    if nifti_raw:
        checks.append(FairCheck(
            principle="I",
            criterion="All NIfTI files are compressed",
            passed=False,
            message=f"Found {len(nifti_raw)} uncompressed .nii files — use .nii.gz",
            severity="recommended",
        ))

    # I2: Check BIDS naming convention (sub-XX in path)
    bids_named = [f for f in nifti_gz if "sub-" in str(f)]
    if nifti_gz:
        all_bids = len(bids_named) == len(nifti_gz)
        checks.append(FairCheck(
            principle="I",
            criterion="Files follow BIDS naming convention",
            passed=all_bids,
            message=f"{len(bids_named)}/{len(nifti_gz)} files follow BIDS naming"
            if all_bids
            else f"Only {len(bids_named)}/{len(nifti_gz)} files follow BIDS naming",
        ))

    # I3: JSON sidecars alongside NIfTI files
    missing_sidecars = []
    for nifti in nifti_gz:
        json_sidecar = nifti.with_suffix("").with_suffix(".json")
        if not json_sidecar.exists():
            missing_sidecars.append(nifti.name)

    if nifti_gz:
        has_all_sidecars = len(missing_sidecars) == 0
        checks.append(FairCheck(
            principle="I",
            criterion="JSON sidecars present for NIfTI files",
            passed=has_all_sidecars,
            message="All NIfTI files have JSON sidecars"
            if has_all_sidecars
            else f"Missing sidecars for: {', '.join(missing_sidecars)}",
        ))

    return checks


def check_reusable(dataset_dir: Path) -> list[FairCheck]:
    """Check Reusable (R) criteria: licensing and data dictionaries.

    FAIR R requires clear licensing (can others reuse this data?) and
    rich metadata about participants and acquisition. Without a LICENSE,
    data is legally unusable even if technically accessible.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        List of FairCheck results for Reusable criteria.
    """
    checks = []

    # R1: LICENSE file
    license_path = dataset_dir / "LICENSE"
    checks.append(FairCheck(
        principle="R",
        criterion="LICENSE file exists",
        passed=license_path.exists(),
        message="LICENSE file found"
        if license_path.exists()
        else "Missing LICENSE — data cannot be legally reused without one",
    ))

    # R2: participants.tsv (structured subject metadata)
    tsv_path = dataset_dir / "participants.tsv"
    checks.append(FairCheck(
        principle="R",
        criterion="participants.tsv exists",
        passed=tsv_path.exists(),
        message="Participant metadata found"
        if tsv_path.exists()
        else "Missing participants.tsv — no structured subject metadata",
    ))

    # R3: participants.json (data dictionary for the TSV)
    json_path = dataset_dir / "participants.json"
    checks.append(FairCheck(
        principle="R",
        criterion="participants.json (data dictionary) exists",
        passed=json_path.exists(),
        message="Data dictionary found"
        if json_path.exists()
        else "Missing participants.json — columns in participants.tsv are undocumented",
        severity="recommended",
    ))

    return checks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_audit(dataset_dir: Path) -> dict:
    """Run all FAIR compliance checks on a BIDS dataset.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        Dict with per-principle results, overall score, and check details.
    """
    dataset_dir = Path(dataset_dir)

    all_checks = (
        check_findable(dataset_dir)
        + check_accessible(dataset_dir)
        + check_interoperable(dataset_dir)
        + check_reusable(dataset_dir)
    )

    # Compute scores per principle and overall
    principles = {}
    for check in all_checks:
        p = check.principle
        if p not in principles:
            principles[p] = {"passed": 0, "total": 0, "checks": []}
        principles[p]["total"] += 1
        if check.passed:
            principles[p]["passed"] += 1
        principles[p]["checks"].append({
            "criterion": check.criterion,
            "passed": check.passed,
            "message": check.message,
            "severity": check.severity,
        })

    total_passed = sum(p["passed"] for p in principles.values())
    total_checks = sum(p["total"] for p in principles.values())
    score = (total_passed / total_checks * 100) if total_checks > 0 else 0

    return {
        "dataset": str(dataset_dir),
        "score": round(score, 1),
        "total_passed": total_passed,
        "total_checks": total_checks,
        "principles": principles,
    }


def format_audit_report(results: dict) -> str:
    """Format audit results as a human-readable report.

    Args:
        results: Output from run_audit().

    Returns:
        Multi-line formatted string with per-principle breakdown.
    """
    principle_names = {
        "F": "FINDABLE",
        "A": "ACCESSIBLE",
        "I": "INTEROPERABLE",
        "R": "REUSABLE",
    }

    lines = [
        "=== FAIR Compliance Audit ===",
        f"Dataset: {results['dataset']}",
        "",
    ]

    for principle_code in ["F", "A", "I", "R"]:
        if principle_code not in results["principles"]:
            continue

        p = results["principles"][principle_code]
        name = principle_names[principle_code]
        lines.append(f"[{principle_code}] {name} ({p['passed']}/{p['total']} passed)")

        for check in p["checks"]:
            status = "PASS" if check["passed"] else "FAIL"
            severity = "" if check["severity"] == "required" else " (recommended)"
            lines.append(f"  [{status}] {check['criterion']}{severity}")

        lines.append("")

    score = results["score"]
    passed = results["total_passed"]
    total = results["total_checks"]
    lines.append(f"Overall score: {score}% ({passed}/{total} checks passed)")

    return "\n".join(lines)

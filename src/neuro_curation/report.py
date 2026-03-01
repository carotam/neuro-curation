"""
HTML Summary Report Module.

Generates a self-contained HTML report summarizing the entire pipeline run:
- Pipeline overview (5 stages with status)
- Raw data provenance (input path, subject ID, scanner characteristics)
- De-identification summary (files processed, tags stripped)
- Dataset overview (subjects, modalities, completeness matrix)
- FAIR compliance score with per-principle breakdown and fix hints
- Integrity verification status
- File listing with sizes

The report uses an inline Jinja2 template with embedded CSS, so the
output is a single .html file that can be opened in any browser without
external dependencies. This makes it easy to email to collaborators
or include in data transfer packages.

In the Insight 46 context, this report would accompany each data
transfer to DPUK/GAAIN, giving the receiving team an immediate
overview of what was done, what's in the dataset, and whether it
meets FAIR compliance standards.

Usage:
    from neuro_curation.report import generate_report

    # Basic usage (dataset scan only):
    generate_report(dataset_dir=Path("bids/"), output_path=Path("report.html"))

    # Full pipeline report (with all stage results and provenance):
    generate_report(
        dataset_dir=Path("bids/"),
        output_path=Path("report.html"),
        pipeline_results={
            "deidentify": {"files_processed": 608, "tags_removed": 5472},
            "audit": audit_results_dict,
            "verify": verify_results_dict,
        },
        provenance={
            "input_path": "/data/raw/sub-01",
            "subject_id": "sub-01",
        },
    )
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Template


def scan_bids_dataset(dataset_dir: Path) -> dict:
    """Scan a BIDS dataset and extract summary information.

    Walks the directory tree to discover subjects, sessions, and
    modalities. Builds a completeness matrix showing which subjects
    have which imaging modalities available.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        Dict with dataset metadata, subjects, modalities, completeness
        matrix, file list, and total size.
    """
    dataset_dir = Path(dataset_dir)

    # Read dataset metadata from dataset_description.json
    metadata = {}
    desc_path = dataset_dir / "dataset_description.json"
    if desc_path.exists():
        try:
            metadata = json.loads(desc_path.read_text())
        except json.JSONDecodeError:
            pass

    # Discover subjects (directories matching sub-*)
    subjects = sorted([
        d.name for d in dataset_dir.iterdir()
        if d.is_dir() and d.name.startswith("sub-")
    ])

    # Discover modalities and build completeness matrix
    # completeness[subject] = {"T1w": True, "FLAIR": False, ...}
    all_modalities = set()
    completeness = {}

    for subject in subjects:
        subject_dir = dataset_dir / subject
        subject_modalities = set()

        # Find all NIfTI files for this subject
        for nifti in subject_dir.rglob("*.nii.gz"):
            # Extract BIDS suffix from filename (last part before extension)
            # e.g., "sub-01_ses-01_T1w.nii.gz" -> "T1w"
            stem = nifti.name.replace(".nii.gz", "")
            parts = stem.split("_")
            if parts:
                suffix = parts[-1]
                subject_modalities.add(suffix)
                all_modalities.add(suffix)

        completeness[subject] = {mod: mod in subject_modalities for mod in all_modalities}

    # After discovering all modalities, fill in any missing entries
    for subject in completeness:
        for mod in all_modalities:
            if mod not in completeness[subject]:
                completeness[subject][mod] = False

    # Collect file information
    files = []
    total_size = 0
    for filepath in sorted(dataset_dir.rglob("*")):
        if not filepath.is_file():
            continue
        size = filepath.stat().st_size
        total_size += size
        files.append({
            "path": filepath.relative_to(dataset_dir).as_posix(),
            "size": size,
            "size_human": format_file_size(size),
        })

    has_unknown_modality = "unknown" in all_modalities

    # Collect the series descriptions that landed in unknown/
    unknown_series: list[str] = []
    if has_unknown_modality:
        for subject in subjects:
            unknown_dir = dataset_dir / subject
            for json_path in unknown_dir.rglob("unknown/*.json"):
                try:
                    data = json.loads(json_path.read_text())
                    sd = data.get("SeriesDescription")
                    if sd and sd not in unknown_series:
                        unknown_series.append(sd)
                except Exception:
                    pass

    acquisition = _extract_acquisition_summary(dataset_dir)

    return {
        "metadata": metadata,
        "subjects": subjects,
        "modalities": sorted(all_modalities),
        "completeness": completeness,
        "has_unknown_modality": has_unknown_modality,
        "unknown_series": sorted(unknown_series),
        "acquisition": acquisition,
        "files": files,
        "total_files": len(files),
        "total_size": total_size,
        "total_size_human": format_file_size(total_size),
    }


def format_file_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable file size string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Formatted string like "1.23 MB" or "456 KB".
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _extract_acquisition_summary(dataset_dir: Path) -> dict:
    """Extract scanner and acquisition characteristics from BIDS JSON sidecars.

    Reads all NIfTI JSON sidecars (excluding root metadata files) and
    aggregates scanner characteristics: manufacturer, model, field strength,
    and unique series descriptions. This provides the "raw data provenance"
    section of the report — the receiving team can see exactly what scanner
    produced the data without needing to open individual files.

    Args:
        dataset_dir: Root of the BIDS dataset.

    Returns:
        Dict with manufacturers, models, field_strengths_T, series_descriptions.
        Empty dict if no sidecars found.
    """
    SKIP_FILES = {
        "dataset_description.json",
        "participants.json",
        "transfer_manifest.json",
    }

    sidecar_data = []
    for path in sorted(dataset_dir.rglob("*.json")):
        if path.name in SKIP_FILES:
            continue
        try:
            meta = json.loads(path.read_text())
            # Only acquisition sidecars have imaging fields
            if any(k in meta for k in ("Manufacturer", "MagneticFieldStrength", "Modality")):
                sidecar_data.append(meta)
        except (json.JSONDecodeError, OSError):
            continue

    if not sidecar_data:
        return {}

    # Group sidecars by scanner (manufacturer + model + field strength).
    # This preserves the link between scanner identity, sequences, and protocol params.
    scanner_data: dict[tuple, dict] = {}
    for m in sidecar_data:
        manufacturer = m.get("Manufacturer", "Unknown")
        model = m.get("ManufacturersModelName", "")
        fs = m.get("MagneticFieldStrength")
        field_str = f"{fs:.1f} T" if fs is not None else "n/a"
        key = (manufacturer, model, field_str)
        if key not in scanner_data:
            scanner_data[key] = {"series": set(), "sidecars": []}
        if m.get("SeriesDescription"):
            scanner_data[key]["series"].add(m["SeriesDescription"])
        scanner_data[key]["sidecars"].append(m)

    # Harmonisation-relevant parameters to extract per scanner.
    # For each param, collect unique formatted values across all series.
    # len(values) > 1 means the parameter varies within the scanner — flagged in the report.
    HARM_PARAMS = [
        ("RepetitionTime",   "TR",  lambda v: f"{v:.2f} s"),
        ("EchoTime",         "TE",  lambda v: f"{v * 1000:.0f} ms"),
        ("FlipAngle",        "FA",  lambda v: f"{v:.0f}\u00b0"),
        ("SoftwareVersions", "SW",  lambda v: str(v)),
    ]

    scanners = []
    for key, data in sorted(scanner_data.items()):
        sidecars = data["sidecars"]
        params = {}
        for field, label, fmt in HARM_PARAMS:
            values = sorted({fmt(m[field]) for m in sidecars if field in m}, key=str)
            if values:
                params[label] = {"vals": values, "consistent": len(values) == 1}
        scanners.append({
            "manufacturer": key[0],
            "model": key[1],
            "field_strength": key[2],
            "series_descriptions": sorted(data["series"]),
            "params": params,
        })

    return {"scanners": scanners}


def generate_report(
    dataset_dir: Path,
    output_path: Path,
    pipeline_results: dict | None = None,
    provenance: dict | None = None,
) -> Path:
    """Generate an HTML summary report for a BIDS dataset.

    When called with pipeline_results, the report includes the full
    pipeline story: de-identification summary, FAIR audit score,
    and integrity verification status. Without it, the report shows
    only the dataset scan (backwards compatible).

    Args:
        dataset_dir: Root of the BIDS dataset.
        output_path: Where to write the HTML report.
        pipeline_results: Optional dict with keys:
            - "deidentify": {"files_processed": int, "tags_removed": int}
            - "audit": output from audit.run_audit()
            - "verify": output from verify.verify_manifest()
        provenance: Optional dict with pipeline input context:
            - "input_path": str path to raw DICOM input directory
            - "subject_id": str subject identifier used in the run

    Returns:
        Path to the generated report file.
    """
    dataset_dir = Path(dataset_dir)
    output_path = Path(output_path)

    # Scan the dataset
    data = scan_bids_dataset(dataset_dir)
    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Include pipeline results and provenance if provided
    data["pipeline"] = pipeline_results or {}
    data["has_pipeline"] = pipeline_results is not None
    data["provenance"] = provenance or {}

    # Render the HTML template
    template = Template(HTML_TEMPLATE)
    html = template.render(**data)

    # Write the report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    print(f"Report generated: {output_path}")

    return output_path


# ---------------------------------------------------------------------------
# Inline HTML template (self-contained with embedded CSS)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ metadata.get('Name', 'BIDS Dataset') }} &mdash; Pipeline Report</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #333;
            background: #f5f7fa;
            line-height: 1.6;
        }
        .container { max-width: 1000px; margin: 0 auto; padding: 2rem; }

        /* Header */
        header {
            background: linear-gradient(135deg, #1a365d, #2b6cb0);
            color: white;
            padding: 2rem 2.5rem;
            border-radius: 8px 8px 0 0;
        }
        header h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
        header .subtitle { opacity: 0.85; font-size: 0.9rem; }

        /* Main content */
        .content {
            background: white;
            padding: 2rem 2.5rem;
            border-radius: 0 0 8px 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }

        /* Summary cards row */
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .card {
            border-radius: 8px;
            padding: 1.2rem 1rem;
            text-align: center;
        }
        .card-blue { background: #ebf4ff; }
        .card-green { background: #f0fff4; }
        .card-amber { background: #fffbeb; }
        .card .value { font-size: 1.8rem; font-weight: 700; }
        .card-blue .value { color: #2b6cb0; }
        .card-green .value { color: #276749; }
        .card-amber .value { color: #92400e; }
        .card .label {
            font-size: 0.75rem;
            color: #4a5568;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 0.2rem;
        }

        /* Section headings */
        h2 {
            font-size: 1.1rem;
            color: #1a365d;
            margin: 2rem 0 0.75rem;
            padding-bottom: 0.4rem;
            border-bottom: 2px solid #e2e8f0;
        }
        h2:first-of-type { margin-top: 0; }

        /* Pipeline stages */
        .pipeline-stages {
            display: flex;
            align-items: center;
            gap: 0;
            margin-bottom: 2rem;
            flex-wrap: wrap;
        }
        .stage {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.6rem 1rem;
            background: #f7fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            font-size: 0.8rem;
            white-space: nowrap;
        }
        .stage-num {
            background: #2b6cb0;
            color: white;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.7rem;
            font-weight: 700;
            flex-shrink: 0;
        }
        .stage-arrow {
            color: #a0aec0;
            font-size: 1.2rem;
            padding: 0 0.3rem;
            flex-shrink: 0;
        }

        /* FAIR score gauge */
        .fair-section {
            display: grid;
            grid-template-columns: 200px 1fr;
            gap: 2rem;
            align-items: start;
            margin-bottom: 1.5rem;
        }
        .fair-gauge {
            text-align: center;
        }
        .fair-score-circle {
            width: 140px;
            height: 140px;
            border-radius: 50%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            margin: 0 auto 0.5rem;
            font-weight: 700;
        }
        .fair-score-circle .score-value { font-size: 2.2rem; line-height: 1; }
        .fair-score-circle .score-label { font-size: 0.75rem; opacity: 0.8; }
        .score-high { background: #c6f6d5; color: #276749; }
        .score-mid { background: #fefcbf; color: #92400e; }
        .score-low { background: #fed7d7; color: #9b2c2c; }
        .fair-details { font-size: 0.85rem; }
        .fair-principle {
            margin-bottom: 0.75rem;
        }
        .fair-principle-header {
            font-weight: 600;
            color: #1a365d;
            margin-bottom: 0.25rem;
        }
        .fair-check {
            padding: 0.15rem 0;
            padding-left: 1.2rem;
            color: #4a5568;
        }
        .fair-check-pass::before { content: "\\2713 "; color: #38a169; font-weight: bold; }
        .fair-check-fail::before { content: "\\2717 "; color: #e53e3e; font-weight: bold; }

        /* De-id summary */
        .deid-summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }
        .deid-stat {
            background: #f7fafc;
            border-radius: 6px;
            padding: 1rem;
        }
        .deid-stat .stat-value { font-size: 1.4rem; font-weight: 700; color: #2b6cb0; }
        .deid-stat .stat-label { font-size: 0.8rem; color: #4a5568; }

        /* Verification badge */
        .verify-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.9rem;
        }
        .verify-pass { background: #c6f6d5; color: #276749; }
        .verify-fail { background: #fed7d7; color: #9b2c2c; }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
            margin-bottom: 1.5rem;
        }
        th, td {
            padding: 0.5rem 0.75rem;
            text-align: left;
            border-bottom: 1px solid #e2e8f0;
        }
        th { background: #f7fafc; font-weight: 600; color: #4a5568; }
        tr:hover { background: #f7fafc; }
        .check { color: #38a169; font-weight: bold; }
        .cross { color: #e53e3e; font-weight: bold; }

        /* Collapsible file list */
        details { margin-bottom: 1rem; }
        summary {
            cursor: pointer;
            font-size: 1.1rem;
            font-weight: 600;
            color: #1a365d;
            padding: 0.4rem 0;
            border-bottom: 2px solid #e2e8f0;
            margin-bottom: 0.75rem;
        }
        summary:hover { color: #2b6cb0; }

        /* Footer */
        footer {
            text-align: center;
            font-size: 0.75rem;
            color: #a0aec0;
            margin-top: 2rem;
            padding-top: 1rem;
        }

        /* Raw data provenance */
        .provenance-bar {
            font-size: 0.8rem;
            color: rgba(255,255,255,0.75);
            margin-top: 0.5rem;
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem 1.5rem;
        }
        .provenance-bar span { white-space: nowrap; }
        .provenance-bar code {
            background: rgba(255,255,255,0.15);
            padding: 0.1rem 0.35rem;
            border-radius: 3px;
            font-size: 0.78rem;
        }

        /* Raw data section */
        .raw-data-table {
            width: 100%;
            font-size: 0.85rem;
            margin-bottom: 1rem;
        }
        .raw-data-table td {
            padding: 0.35rem 0.75rem;
            border-bottom: 1px solid #e2e8f0;
        }
        .raw-data-table td:first-child {
            width: 180px;
            font-weight: 600;
            color: #4a5568;
            white-space: nowrap;
        }
        .series-badge {
            display: inline-block;
            background: #edf2f7;
            border-radius: 4px;
            padding: 0.1rem 0.5rem;
            margin: 0.15rem 0.2rem 0.15rem 0;
            font-size: 0.78rem;
            color: #2d3748;
        }
        .series-badge-unknown {
            background: #fefcbf;
            color: #744210;
        }

        /* Unknown modality warning */
        .unknown-th { color: #92400e; }
        .note {
            font-size: 0.8rem;
            color: #744210;
            background: #fffbeb;
            border-left: 3px solid #f6e05e;
            padding: 0.5rem 0.75rem;
            border-radius: 0 4px 4px 0;
            margin-bottom: 1rem;
        }

        /* FAIR fix hints */
        .fair-hint {
            padding: 0.2rem 0 0.1rem 1.2rem;
            font-size: 0.78rem;
            color: #744210;
            font-style: italic;
        }
        .fair-hint::before { content: "Fix: "; font-weight: 600; font-style: normal; }

        /* Responsive */
        @media (max-width: 700px) {
            .fair-section { grid-template-columns: 1fr; }
            .pipeline-stages { flex-direction: column; align-items: stretch; }
            .stage-arrow { display: none; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <h1>{{ metadata.get('Name', 'BIDS Dataset') }}</h1>
            <p class="subtitle">
                {% if has_pipeline %}Pipeline Report{% else %}Dataset Summary{% endif %}
                | BIDS {{ metadata.get('BIDSVersion', 'unknown') }}
                | {{ generated_at }}
            </p>
            {% if provenance %}
            <div class="provenance-bar">
                {% if provenance.get('subject_id') %}
                <span>Subject: <code>{{ provenance.subject_id }}</code></span>
                {% endif %}
                {% if provenance.get('input_path') %}
                <span>Input: <code>{{ provenance.input_path }}</code></span>
                {% endif %}
            </div>
            {% endif %}
        </header>

        <div class="content">

            {% if has_pipeline %}
            <!-- ============================================= -->
            <!-- Pipeline Overview (process status, decoupled from sections below) -->
            <!-- ============================================= -->
            <h2>Pipeline Overview</h2>
            <p class="note" style="margin-bottom:0.75rem;">Dataset readiness by stage. ✓ = verified from artefacts, ✗ = failed, – = not verifiable from the BIDS dataset alone (prior step). See sections below for details.</p>
            <div class="pipeline-stages">
                <div class="stage">
                    <span class="stage-num">1</span> De-identify
                    <span style="color:#a0aec0; font-size:0.85rem; margin-left:0.3rem;" title="Prior step — not verifiable from BIDS dataset alone">–</span>
                </div>
                <span class="stage-arrow">&rarr;</span>
                <div class="stage">
                    <span class="stage-num">2</span> Convert to BIDS
                    {% if subjects | length > 0 %}<span style="color:#38a169; font-size:0.85rem; margin-left:0.3rem;">&#10003;</span>{% else %}<span style="color:#e53e3e; font-size:0.85rem; margin-left:0.3rem;">&#10007;</span>{% endif %}
                </div>
                <span class="stage-arrow">&rarr;</span>
                <div class="stage">
                    <span class="stage-num">3</span> Verify Integrity
                    {% if pipeline.get('verify') %}
                        {% if pipeline.verify.passed %}<span style="color:#38a169; font-size:0.85rem; margin-left:0.3rem;">&#10003;</span>{% else %}<span style="color:#e53e3e; font-size:0.85rem; margin-left:0.3rem;">&#10007;</span>{% endif %}
                    {% else %}<span style="color:#a0aec0; font-size:0.85rem; margin-left:0.3rem;" title="Not run">–</span>{% endif %}
                </div>
                <span class="stage-arrow">&rarr;</span>
                <div class="stage">
                    <span class="stage-num">4</span> FAIR Audit
                    {% if pipeline.get('audit') %}
                        {% if pipeline.audit.score >= 80 %}<span style="color:#38a169; font-size:0.85rem; margin-left:0.3rem;">&#10003;</span>{% elif pipeline.audit.score >= 50 %}<span style="color:#b7791f; font-size:0.85rem; margin-left:0.3rem;">&#9651;</span>{% else %}<span style="color:#e53e3e; font-size:0.85rem; margin-left:0.3rem;">&#10007;</span>{% endif %}
                    {% else %}<span style="color:#a0aec0; font-size:0.85rem; margin-left:0.3rem;" title="Not run">–</span>{% endif %}
                </div>
                <span class="stage-arrow">&rarr;</span>
                <div class="stage">
                    <span class="stage-num">5</span> Report
                    <span style="color:#38a169; font-size:0.85rem; margin-left:0.3rem;">&#10003;</span>
                </div>
            </div>
            {% endif %}

            <!-- ============================================= -->
            <!-- Summary Cards -->
            <!-- ============================================= -->
            <div class="cards">
                <div class="card card-blue">
                    <div class="value">{{ subjects | length }}</div>
                    <div class="label">Subjects</div>
                </div>
                <div class="card card-blue">
                    <div class="value">{{ modalities | length }}</div>
                    <div class="label">BIDS modalities</div>
                </div>
                <div class="card card-blue">
                    <div class="value">{{ total_files }}</div>
                    <div class="label">Total files (BIDS)</div>
                </div>
                <div class="card card-blue">
                    <div class="value">{{ total_size_human }}</div>
                    <div class="label">Total size</div>
                </div>
                {% if pipeline.get('audit') %}
                <div class="card {% if pipeline.audit.score >= 80 %}card-green{% elif pipeline.audit.score >= 50 %}card-amber{% else %}card-amber{% endif %}">
                    <div class="value">{{ pipeline.audit.score }}%</div>
                    <div class="label">FAIR Score</div>
                </div>
                {% endif %}
            </div>
            <p class="note"><strong>Subjects</strong>: participant directories named <code>sub-*</code> in the BIDS dataset, one per individual. The ID is assigned at conversion time via <code>--subject-id</code>. &nbsp;|&nbsp; <strong>BIDS modalities</strong>: imaging data types inferred from NIfTI filenames (e.g. <code>bold</code> for fMRI, <code>T1w</code> for structural MRI). Each series description is mapped to a BIDS suffix in <code>convert.py</code>.</p>

            {% if provenance or acquisition %}
            <!-- ============================================= -->
            <!-- Source Data -->
            <!-- ============================================= -->
            <h2>Source Data</h2>
            <table class="raw-data-table">
                {% if provenance.get('input_path') %}
                <tr>
                    <td>Input directory</td>
                    <td><code>{{ provenance.input_path }}</code></td>
                </tr>
                {% endif %}
                {% if provenance.get('subject_id') %}
                <tr>
                    <td>Subject ID</td>
                    <td>{{ provenance.subject_id }}</td>
                </tr>
                {% endif %}
                {% if pipeline.get('deidentify') %}
                <tr>
                    <td>Raw DICOM files</td>
                    <td>{{ pipeline.deidentify.files_processed }}</td>
                </tr>
                {% endif %}
                {% if acquisition.get('scanners') %}
                {% for scanner in acquisition.scanners %}
                <tr>
                    <td>Scanner{% if acquisition.scanners | length > 1 %} {{ loop.index }}{% endif %}</td>
                    <td>
                        <strong>{{ scanner.manufacturer }}{% if scanner.model %} {{ scanner.model }}{% endif %}</strong>
                        &mdash; {{ scanner.field_strength }}
                    </td>
                </tr>
                <tr>
                    <td style="padding-left:1.5rem; color:#718096;">Series</td>
                    <td>
                        {% for sd in scanner.series_descriptions %}
                        <span class="series-badge">{{ sd }}</span>
                        {% endfor %}
                    </td>
                </tr>
                {% if scanner.params %}
                <tr>
                    <td style="padding-left:1.5rem; color:#718096;">Protocol</td>
                    <td style="font-size:0.85rem;">
                        {% for label, info in scanner.params.items() %}
                        <span style="margin-right:1rem;">
                            <strong>{{ label }}:</strong>
                            {% if info.consistent %}
                                {{ info.vals[0] }}
                            {% else %}
                                <span style="color:#b7791f;" title="Varies across series">{{ info.vals | join(' / ') }} ⚠</span>
                            {% endif %}
                        </span>
                        {% endfor %}
                    </td>
                </tr>
                {% endif %}
                {% endfor %}
                {% endif %}
            </table>
            {% if acquisition.get('scanners') %}
            <p class="note" style="margin-top:0.4rem;">Protocol parameters sourced from DICOM headers, extracted by dcm2niix into BIDS JSON sidecars during conversion. <span style="color:#b7791f;">⚠ amber</span> = value varies across series within the same scanner — potential protocol inconsistency to investigate before harmonisation.</p>
            {% endif %}
            {% endif %}

            {% if pipeline.get('deidentify') %}
            <!-- ============================================= -->
            <!-- De-identification Summary -->
            <!-- ============================================= -->
            <h2>De-identification</h2>
            <p style="margin-bottom:1rem; color:#4a5568; font-size:0.9rem;">
                PII verification and removal per
                <strong>DICOM PS3.15 Annex E Basic Profile</strong>.
                Complements XNAT DicomEdit anonymization by catching residual tags:
                patient names, dates of birth, addresses, hospital IDs,
                physician names, institution details, and vendor-specific private tags.
                UIDs replaced consistently across each study.
            </p>
            <div class="deid-summary">
                <div class="deid-stat">
                    <div class="stat-value">{{ pipeline.deidentify.files_processed }}</div>
                    <div class="stat-label">DICOM files processed</div>
                </div>
                <div class="deid-stat">
                    <div class="stat-value">{{ pipeline.deidentify.tags_removed }}</div>
                    <div class="stat-label">PII tags removed</div>
                </div>
            </div>
            {% endif %}

            {% if modalities and subjects %}
            <!-- ============================================= -->
            <!-- Completeness Matrix -->
            <!-- ============================================= -->
            <h2>BIDS Dataset Completeness</h2>
            <p class="note">One row per subject found in the BIDS dataset (directories named <code>sub-*</code>). Subject IDs are assigned at conversion time via <code>--subject-id</code>. Each column is a BIDS modality suffix derived from the NIfTI filenames.</p>
            <table>
                <thead>
                    <tr>
                        <th>Subject</th>
                        {% for mod in modalities %}
                        <th {% if mod == 'unknown' %}class="unknown-th"{% endif %}>
                            {{ mod }}{% if mod == 'unknown' %} ⚠{% endif %}
                        </th>
                        {% endfor %}
                    </tr>
                </thead>
                <tbody>
                    {% for subject in subjects %}
                    <tr>
                        <td><strong>{{ subject }}</strong></td>
                        {% for mod in modalities %}
                        <td>
                            {% if completeness[subject][mod] %}
                            <span class="check">&#10003;</span>
                            {% else %}
                            <span class="cross">&#10007;</span>
                            {% endif %}
                        </td>
                        {% endfor %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% if has_unknown_modality %}
            <p class="note">
                ⚠ <strong>unknown</strong> modality: the following series descriptions were not recognised by the BIDS suffix mapper
                and were placed in <code>unknown/</code>:
                {% for sd in unknown_series %}<code>{{ sd }}</code>{% if not loop.last %}, {% endif %}{% endfor %}.
                Add a matching entry to <code>SERIES_TO_BIDS_SUFFIX</code> in <code>convert.py</code> to assign the
                correct BIDS suffix (e.g., <code>"EPI PE=AP": "epi"</code> for a fieldmap).
            </p>
            {% endif %}
            {% endif %}

            {% if pipeline.get('verify') %}
            <!-- ============================================= -->
            <!-- Integrity Verification -->
            <!-- ============================================= -->
            <h2>Integrity Verification</h2>
            {% if pipeline.verify.passed %}
            <div class="verify-badge verify-pass">
                &#10003; All {{ pipeline.verify.files_checked }} files verified &mdash; SHA-256 checksums match
            </div>
            {% else %}
            <div class="verify-badge verify-fail">
                &#10007; Verification issues found:
                {{ pipeline.verify.mismatches | length }} mismatches,
                {{ pipeline.verify.missing | length }} missing
            </div>
            {% endif %}
            <p style="margin-top:0.75rem; color:#4a5568; font-size:0.85rem;">
                Transfer manifest saved as <code>transfer_manifest.json</code>.
                Re-verify after transfer with: <code>neuro-curation verify --dataset DIR --check</code>
            </p>
            {% endif %}

            {% if pipeline.get('audit') %}
            <!-- ============================================= -->
            <!-- FAIR Compliance Audit -->
            <!-- ============================================= -->
            <h2>FAIR Compliance</h2>
            <div class="fair-section">
                <div class="fair-gauge">
                    <div class="fair-score-circle {% if pipeline.audit.score >= 80 %}score-high{% elif pipeline.audit.score >= 50 %}score-mid{% else %}score-low{% endif %}">
                        <span class="score-value">{{ pipeline.audit.score }}%</span>
                        <span class="score-label">{{ pipeline.audit.total_passed }}/{{ pipeline.audit.total_checks }} passed</span>
                    </div>
                </div>
                <div class="fair-details">
                    {% set principle_names = {"F": "Findable", "A": "Accessible", "I": "Interoperable", "R": "Reusable"} %}
                    {% set fair_hints = {
                        "LICENSE file exists": "Add a LICENSE file to the dataset root (e.g., CC0-1.0 text from choosealicense.com). Required for DPUK submission.",
                        "Recommended field 'Authors' present": "Add an 'Authors' list to dataset_description.json, e.g. ['Surname, Firstname'].",
                        "Required field 'Name' present": "Add a 'Name' string field to dataset_description.json.",
                        "Required field 'BIDSVersion' present": "Add a 'BIDSVersion' field (e.g. '1.9.0') to dataset_description.json.",
                        "JSON sidecars present for NIfTI files": "Re-run dcm2niix with -b y to generate JSON sidecars alongside each NIfTI.",
                        "NIfTI files present in compressed format": "Re-run conversion with dcm2niix -z y to produce .nii.gz files.",
                        "Files follow BIDS naming convention": "Rename files to follow BIDS convention: sub-XX[_ses-XX]_suffix.nii.gz",
                        "participants.tsv exists": "Create participants.tsv with at least a participant_id column.",
                        "participants.json (data dictionary) exists": "Create participants.json with a description for each column in participants.tsv.",
                        "README exists": "Create a README file at the dataset root describing its contents.",
                        "README has meaningful content": "Expand the README to include dataset description, acquisition protocol, and contact info."
                    } %}
                    {% for code in ["F", "A", "I", "R"] %}
                    {% if code in pipeline.audit.principles %}
                    {% set p = pipeline.audit.principles[code] %}
                    <div class="fair-principle">
                        <div class="fair-principle-header">
                            [{{ code }}] {{ principle_names[code] }}
                            ({{ p.passed }}/{{ p.total }})
                        </div>
                        {% for check in p.checks %}
                        <div class="fair-check {% if check.passed %}fair-check-pass{% else %}fair-check-fail{% endif %}">
                            {{ check.criterion }}
                            {% if check.severity == 'recommended' %}<em>(recommended)</em>{% endif %}
                        </div>
                        {% if not check.passed and fair_hints.get(check.criterion) %}
                        <div class="fair-hint">{{ fair_hints[check.criterion] }}</div>
                        {% endif %}
                        {% endfor %}
                    </div>
                    {% endif %}
                    {% endfor %}
                </div>
            </div>
            {% endif %}

            <!-- ============================================= -->
            <!-- File Listing (collapsible) -->
            <!-- ============================================= -->
            <details>
                <summary>Files ({{ total_files }})</summary>
                <table>
                    <thead>
                        <tr>
                            <th>Path</th>
                            <th style="text-align:right">Size</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for file in files %}
                        <tr>
                            <td>{{ file.path }}</td>
                            <td style="text-align:right">{{ file.size_human }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </details>
        </div>

        <footer>
            Generated by <strong>neuro-curation</strong> v0.1.0 | {{ generated_at }}
        </footer>
    </div>
</body>
</html>"""

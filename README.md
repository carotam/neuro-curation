# neuro-curation

Reproducible Neuroimaging Curation & Transfer Pipeline — a modular toolkit for converting DICOM exports to BIDS format, verifying data integrity for external transfers, auditing FAIR compliance, and generating summary reports.

Built as a code sample for the UCL Dementia Research Centre Research Data Steward role (Insight 46 study).

## Where this fits

XNAT handles primary de-identification on ingest via **DicomEdit** scripts, but it **has no native BIDS export** and no built-in FAIR auditing. This pipeline sits downstream of XNAT, filling those gaps:

```
Scanner ──> XNAT (de-id via DicomEdit) ──> Export DICOMs
                                                │
                              neuro-curation pipeline:
                                                │
                    [deidentify] ──> [convert] ──> BIDS Dataset
                    (DicomEdit           │
                     verification +      ├── [verify]  ── SHA-256 manifest
                     safety net layer)   ├── [audit]   ── FAIR compliance score
                                         ├── [metrics] ── KPI dashboard / DMP JSON
                                         └── [report]  ── HTML summary
                                                │
                                    Ready for DPUK / GAAIN / analysis
```

## Quick Start

```bash
# Create and activate virtual environment
uv venv .venv
source .venv/bin/activate

# Install in development mode
make install

# Run tests (no external tools required)
make test

# Download sample DICOM data for demo (requires internet)
make download

# Run the full pipeline
PYTHONPATH=src .venv/bin/python -m neuro_curation.cli run \
    --input sample_data/raw --output output --subject-id sub-01
```

## Modules

### 1. De-identification (`deidentify.py`)

Strips PII from DICOM files per the DICOM PS3.15 Annex E Basic De-identification Profile. In the Insight 46 workflow, this serves as a **DicomEdit verification and safety net layer** — XNAT's DicomEdit handles primary anonymization on ingest, but DicomEdit errors are silent and scripts from older XNAT versions can fail without warning. This module also handles data from non-XNAT sources (collaborators, multi-site transfers) and provides a second pass before external sharing.

Removes direct identifiers (name, DOB, address), quasi-identifiers (age, weight — particularly sensitive in the NSHD cohort where all participants were born in a single week of March 1946, making age alone re-identifying), hospital system links, and vendor-specific private tags. UIDs are replaced consistently across a study.

`check_xnat_deidentification(path)` reads the `PatientIdentityRemoved` DICOM tag set by XNAT DicomEdit and returns a recommendation: `"skip"` (DicomEdit confirmed, clean), `"verify_only"` (DicomEdit confirmed but NSHD quasi-identifiers remain), or `"full_deidentify"` (DicomEdit failed or direct identifiers found). This lets the pipeline apply the minimum necessary processing rather than blindly re-anonymizing clean files.

### 2. DICOM to BIDS Conversion (`convert.py`)

**Fills the main gap in the XNAT workflow**: XNAT has no native BIDS export (the xnat2bids tooling is incomplete and often fails silently). This module converts DICOM exports to NIfTI using dcm2niix and organizes the output into a BIDS-compliant directory structure. Maps scanner SeriesDescription values to BIDS suffixes (T1_MPRAGE → T1w, FLAIR_3D → FLAIR). Generates required root-level BIDS metadata: `dataset_description.json`, `participants.tsv`, `README`.

### 3. Integrity Verification (`verify.py`)

Generates SHA-256 checksums for every file in the dataset and saves them in a `transfer_manifest.json`. Essential for transfers to DPUK, GAAIN, or collaborator workstations — the manifest lets the receiving end verify that every file arrived intact. Uses chunked reads for memory efficiency with large NIfTI files.

### 4. FAIR Compliance Audit (`audit.py`)

Checks the dataset against the FAIR data principles (Wilkinson et al., 2016):
- **Findable**: `dataset_description.json` with required fields (Name, BIDSVersion, Authors)
- **Accessible**: README with meaningful content
- **Interoperable**: BIDS naming convention, compressed NIfTI (.nii.gz), JSON sidecars
- **Reusable**: LICENSE file, `participants.tsv` with data dictionary

Produces a per-principle score and an overall compliance percentage. Directly relevant to Wellcome Trust and UKRI data management plan requirements.

### 5. Pipeline KPI Metrics (`metrics.py`)

Computes the four key performance indicators used to assess pipeline health and dataset quality. Output is a structured dict that can be printed as a text dashboard or saved as JSON for DMP reporting or Alzheimer's Association funder submissions.

| KPI | Measurement | Target |
|-----|-------------|--------|
| BIDS Validation Rate | % NIfTI files passing compression, naming, and sidecar checks | 100% |
| Checksum Match Rate | % files matching SHA-256 hashes in transfer_manifest.json | 100% |
| FAIR Compliance Score | % FAIR checks passed (F / A / I / R) via audit.py | ≥ 80% |
| Metadata Completeness | % NIfTI sessions with complete JSON sidecars | 100% |

The BIDS Validation Rate is a **gating metric**: no session should enter fMRIPrep, FreeSurfer, or any BIDS app until it passes.

### 6. HTML Summary Report (`report.py`)

Generates a self-contained HTML report showing the full pipeline output. Uses inline CSS and Jinja2 templating — a single .html file with no external dependencies, suitable for emailing with any data transfer.

Sections:
- **Pipeline Overview**: stage-by-stage status (De-identify → Convert → Verify → Audit → Report) with ✓/✗/– badges.
- **Summary cards**: subjects, BIDS modalities, total files, total size, and FAIR score at a glance.
- **Source Data**: input directory, subject ID, raw DICOM count, scanner make/model/field strength, series descriptions, and protocol parameters (TR, TE, flip angle, software version) — with ⚠ flagging when a parameter varies across series. Extracted automatically from the BIDS JSON sidecars.
- **De-identification summary**: DICOM files processed and PII tags removed.
- **Dataset completeness matrix**: subjects × modalities. Files that could not be mapped to a BIDS suffix are flagged as `unknown ⚠` with an actionable note.
- **Integrity verification**: SHA-256 pass/fail badge with re-verify command.
- **FAIR compliance**: per-principle breakdown (F/A/I/R) with an actionable fix hint below each failing check.
- **File listing** (collapsible): all files with sizes.

### CLI (`cli.py`)

```
neuro-curation deidentify  --input DIR --output DIR --subject-id ID
neuro-curation convert     --input DIR --output DIR --subject-id ID [--session-id ID]
neuro-curation verify      --dataset DIR [--check]
neuro-curation audit       --dataset DIR
neuro-curation metrics     --dataset DIR [--output FILE]
neuro-curation report      --dataset DIR --output FILE
neuro-curation run         --input DIR --output DIR --subject-id ID
```

The `run` command chains all stages: deidentify → convert → verify → audit → metrics → report.

## Testing

```bash
make test     # 16 unit tests, all run offline with synthetic data
make lint     # ruff check + format check
```

Tests use **inline synthetic DICOM files** (created in `tests/conftest.py`) with deliberately planted PII tags. No external data, no internet, no dcm2niix required for unit tests. One integration test (full DICOM → BIDS conversion) requires dcm2niix.

The `check_xnat_deidentification()` function is tested with synthetic DICOM files that simulate three scenarios: clean DicomEdit output, DicomEdit output with residual NSHD quasi-identifiers, and files with no DicomEdit markers at all.

## Dependencies

| Package | Purpose |
|---------|---------|
| `pydicom` | Read, modify, and write DICOM files |
| `nibabel` | Read/create NIfTI images |
| `jinja2` | HTML report templating |
| `bids-validator` | Validate BIDS filename conventions |
| `dcm2niix` | DICOM → NIfTI conversion (external binary, `brew install dcm2niix`) |

## Relevance to Insight 46

This pipeline addresses gaps in the current XNAT-based workflow at the DRC:

1. **XNAT → BIDS**: XNAT has no native BIDS export. The conversion module produces BIDS-compliant datasets ready for analysis with fmriprep, FreeSurfer, and other BIDS apps. BIDS adoption reduces per-cohort preparation time from ~6 hours to ~30 minutes for federated networks (Fang et al., 2023, GAAIN/DPUK/ADDI).
2. **Transfer to DPUK/GAAIN**: The integrity verification module generates SHA-256 manifests to confirm data survives transit. The FAIR audit confirms the dataset meets funder requirements before sending.
3. **DicomEdit verification**: XNAT's DicomEdit handles primary anonymization on ingest, but this module reads the `PatientIdentityRemoved` tag to confirm DicomEdit ran — and applies a full safety-net pass if it failed or missed residual tags. Particularly important for the NSHD cohort where even age is quasi-identifying (all participants born in one week in March 1946).
4. **KPI monitoring**: The metrics module produces a JSON-serialisable dashboard of four pipeline KPIs (BIDS validation rate, checksum match rate, FAIR score, metadata completeness). These provide evidence for Alzheimer's Association data-sharing obligations and UKRI data management plan reporting.
5. **Documentation**: The HTML report accompanies each transfer, giving the receiving team (DPUK, GAAIN, Skylark) an instant overview without specialised tools.

**Version control**: For longitudinal studies like Insight 46 with multiple waves (phases 1–3), data + code versioning via DataLad (Halchenko et al., 2021) is recommended alongside this pipeline. DataLad provides DOI-tagged derivative releases and reproducible re-runs across waves — complementing the JSONL audit logs that neuro-curation generates.

Every module is independently usable, heavily commented for educational value, and tested with zero external setup.

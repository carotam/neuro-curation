"""
Command-line interface for neuro-curation.

Provides subcommands for each pipeline stage, plus a `run` command that
chains them all together. Uses argparse (stdlib) to avoid extra dependencies.

Usage:
    neuro-curation deidentify --input raw/ --output deid/ --subject-id sub-01
    neuro-curation convert --input deid/ --output bids/ --subject-id sub-01
    neuro-curation verify --dataset bids/
    neuro-curation verify --dataset bids/ --check
    neuro-curation audit --dataset bids/
    neuro-curation metrics --dataset bids/
    neuro-curation metrics --dataset bids/ --output metrics.json
    neuro-curation report --dataset bids/ --output report.html
    neuro-curation run --input raw/ --output output/ --subject-id sub-01
"""

import argparse
import sys
from pathlib import Path

from neuro_curation import __version__


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the neuro-curation CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    parser = argparse.ArgumentParser(
        prog="neuro-curation",
        description="Reproducible Neuroimaging Curation & Transfer Pipeline",
    )
    parser.add_argument(
        "--version", action="version", version=f"neuro-curation {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Pipeline commands")

    # --- deidentify subcommand ---
    p_deid = subparsers.add_parser(
        "deidentify", help="Strip PII from DICOM files"
    )
    p_deid.add_argument("--input", required=True, type=Path, help="Input DICOM directory")
    p_deid.add_argument("--output", required=True, type=Path, help="Output directory for de-identified DICOMs")
    p_deid.add_argument("--subject-id", required=True, help="Research subject ID (e.g., sub-01)")

    # --- convert subcommand ---
    p_conv = subparsers.add_parser(
        "convert", help="Convert DICOM to NIfTI + BIDS"
    )
    p_conv.add_argument("--input", required=True, type=Path, help="Input DICOM directory")
    p_conv.add_argument("--output", required=True, type=Path, help="Output BIDS directory")
    p_conv.add_argument("--subject-id", required=True, help="Subject ID (e.g., sub-01)")
    p_conv.add_argument("--session-id", default=None, help="Session ID (e.g., ses-01)")
    p_conv.add_argument("--dataset-name", default="Neuroimaging Dataset", help="Dataset name for BIDS metadata")

    # --- verify subcommand ---
    p_verify = subparsers.add_parser(
        "verify", help="Generate or verify SHA-256 transfer manifest"
    )
    p_verify.add_argument("--dataset", required=True, type=Path, help="BIDS dataset directory")
    p_verify.add_argument("--check", action="store_true", help="Verify existing manifest (default: generate new)")

    # --- audit subcommand ---
    p_audit = subparsers.add_parser(
        "audit", help="Check FAIR compliance of a BIDS dataset"
    )
    p_audit.add_argument("--dataset", required=True, type=Path, help="BIDS dataset directory")

    # --- metrics subcommand ---
    p_metrics = subparsers.add_parser(
        "metrics", help="Compute pipeline KPI metrics"
    )
    p_metrics.add_argument("--dataset", required=True, type=Path, help="BIDS dataset directory")
    p_metrics.add_argument("--output", default=None, type=Path, help="Optional JSON output file")

    # --- report subcommand ---
    p_report = subparsers.add_parser(
        "report", help="Generate HTML summary report"
    )
    p_report.add_argument("--dataset", required=True, type=Path, help="BIDS dataset directory")
    p_report.add_argument("--output", required=True, type=Path, help="Output HTML file path")

    # --- run subcommand (full pipeline) ---
    p_run = subparsers.add_parser(
        "run", help="Run the full pipeline: deidentify -> convert -> verify -> audit -> report"
    )
    p_run.add_argument("--input", required=True, type=Path, help="Input raw DICOM directory")
    p_run.add_argument("--output", required=True, type=Path, help="Output directory")
    p_run.add_argument("--subject-id", required=True, help="Subject ID (e.g., sub-01)")
    p_run.add_argument("--session-id", default=None, help="Session ID (e.g., ses-01)")
    p_run.add_argument("--dataset-name", default="Neuroimaging Dataset", help="Dataset name")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    # Dispatch to the appropriate handler
    handlers = {
        "deidentify": _handle_deidentify,
        "convert": _handle_convert,
        "verify": _handle_verify,
        "audit": _handle_audit,
        "metrics": _handle_metrics,
        "report": _handle_report,
        "run": _handle_run,
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_deidentify(args) -> int:
    """Handle the 'deidentify' subcommand."""
    from neuro_curation.deidentify import deidentify_directory

    if not args.input.exists():
        print(f"Error: Input directory not found: {args.input}", file=sys.stderr)
        return 1

    results = deidentify_directory(args.input, args.output, args.subject_id)
    print(f"\nProcessed {len(results)} files")
    return 0


def _handle_convert(args) -> int:
    """Handle the 'convert' subcommand."""
    from neuro_curation.convert import convert_subject

    if not args.input.exists():
        print(f"Error: Input directory not found: {args.input}", file=sys.stderr)
        return 1

    convert_subject(
        args.input, args.output, args.subject_id,
        session_id=args.session_id, dataset_name=args.dataset_name,
    )
    return 0


def _handle_verify(args) -> int:
    """Handle the 'verify' subcommand."""
    from neuro_curation.verify import (
        format_verification_report,
        generate_manifest,
        verify_manifest,
    )

    if not args.dataset.exists():
        print(f"Error: Dataset directory not found: {args.dataset}", file=sys.stderr)
        return 1

    if args.check:
        # Verify existing manifest
        results = verify_manifest(args.dataset)
        print(format_verification_report(results))
        return 0 if results["passed"] else 1
    else:
        # Generate new manifest
        generate_manifest(args.dataset)
        return 0


def _handle_audit(args) -> int:
    """Handle the 'audit' subcommand."""
    from neuro_curation.audit import format_audit_report, run_audit

    if not args.dataset.exists():
        print(f"Error: Dataset directory not found: {args.dataset}", file=sys.stderr)
        return 1

    results = run_audit(args.dataset)
    print(format_audit_report(results))
    return 0


def _handle_metrics(args) -> int:
    """Handle the 'metrics' subcommand."""
    import json
    from neuro_curation.metrics import compute_metrics, format_metrics_report

    if not args.dataset.exists():
        print(f"Error: Dataset directory not found: {args.dataset}", file=sys.stderr)
        return 1

    metrics = compute_metrics(args.dataset)
    print(format_metrics_report(metrics))

    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2) + "\n")
        print(f"\nMetrics saved to {args.output}")

    return 0


def _handle_report(args) -> int:
    """Handle the 'report' subcommand."""
    from neuro_curation.audit import run_audit
    from neuro_curation.report import generate_report
    from neuro_curation.verify import generate_manifest, verify_manifest

    if not args.dataset.exists():
        print(f"Error: Dataset directory not found: {args.dataset}", file=sys.stderr)
        return 1

    # Run audit and verify so the report is fully populated
    generate_manifest(args.dataset)
    verify_results = verify_manifest(args.dataset)
    audit_results = run_audit(args.dataset)

    pipeline_results = {
        "audit": audit_results,
        "verify": verify_results,
    }
    generate_report(args.dataset, args.output, pipeline_results=pipeline_results)
    return 0


def _handle_run(args) -> int:
    """Handle the 'run' subcommand — full pipeline.

    Chains all stages in order:
    1. De-identify DICOM files
    2. Convert to NIfTI + BIDS format
    3. Generate integrity manifest
    4. Run FAIR compliance audit
    5. Generate HTML summary report
    """
    from neuro_curation.audit import format_audit_report, run_audit
    from neuro_curation.convert import convert_subject
    from neuro_curation.deidentify import deidentify_directory
    from neuro_curation.report import generate_report
    from neuro_curation.verify import generate_manifest, verify_manifest

    if not args.input.exists():
        print(f"Error: Input directory not found: {args.input}", file=sys.stderr)
        return 1

    output = Path(args.output)
    deid_dir = output / "deidentified"
    bids_dir = output / "bids"

    # Stage 1: De-identification
    print("=" * 60)
    print("STAGE 1: De-identification")
    print("=" * 60)
    deid_results = deidentify_directory(args.input, deid_dir, args.subject_id)
    total_tags_removed = sum(r.get("tags_removed", 0) for r in deid_results)
    print(f"  {len(deid_results)} files de-identified\n")

    # Stage 2: DICOM -> NIfTI + BIDS
    print("=" * 60)
    print("STAGE 2: DICOM to NIfTI + BIDS Conversion")
    print("=" * 60)
    convert_subject(
        deid_dir, bids_dir, args.subject_id,
        session_id=args.session_id, dataset_name=args.dataset_name,
    )
    print()

    # Stage 3: Integrity verification
    print("=" * 60)
    print("STAGE 3: Integrity Verification")
    print("=" * 60)
    generate_manifest(bids_dir)
    verify_results = verify_manifest(bids_dir)
    print()

    # Stage 4: FAIR audit
    print("=" * 60)
    print("STAGE 4: FAIR Compliance Audit")
    print("=" * 60)
    audit_results = run_audit(bids_dir)
    print(format_audit_report(audit_results))
    print()

    # Stage 5: Summary report — pass all pipeline results for a rich report
    print("=" * 60)
    print("STAGE 5: Summary Report")
    print("=" * 60)
    report_path = output / "report.html"
    pipeline_results = {
        "deidentify": {
            "files_processed": len(deid_results),
            "tags_removed": total_tags_removed,
        },
        "audit": audit_results,
        "verify": verify_results,
    }
    provenance = {
        "input_path": str(args.input.resolve()),
        "subject_id": args.subject_id,
    }
    generate_report(bids_dir, report_path, pipeline_results=pipeline_results, provenance=provenance)
    print()

    print("=" * 60)
    print("PIPELINE COMPLETE")
    print(f"  De-identified DICOMs: {deid_dir}")
    print(f"  BIDS dataset:        {bids_dir}")
    print(f"  HTML report:         {report_path}")
    print(f"  FAIR score:          {audit_results['score']}%")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())

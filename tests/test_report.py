"""
Tests for the HTML summary report module.

Uses the pre-built BIDS dataset fixture (from conftest.py) to test
dataset scanning, report generation, and file size formatting.
"""

from neuro_curation.report import format_file_size, generate_report, scan_bids_dataset


class TestScanBidsDataset:
    """Verify that BIDS dataset scanning returns correct information."""

    def test_scan_bids_dataset(self, sample_bids_dataset):
        """Scanning a BIDS dataset should return correct subjects,
        modalities, and completeness matrix."""
        data = scan_bids_dataset(sample_bids_dataset)

        # Should find sub-01
        assert "sub-01" in data["subjects"]
        assert len(data["subjects"]) == 1

        # Should find T1w and FLAIR modalities
        assert "T1w" in data["modalities"]
        assert "FLAIR" in data["modalities"]

        # Completeness matrix should show both as present
        assert data["completeness"]["sub-01"]["T1w"] is True
        assert data["completeness"]["sub-01"]["FLAIR"] is True

        # Should have files and non-zero size
        assert data["total_files"] > 0
        assert data["total_size"] > 0

        # Metadata should be parsed from dataset_description.json
        assert data["metadata"].get("Name") == "Test Neuroimaging Dataset"


class TestGenerateReport:
    """Verify that HTML report generation works correctly."""

    def test_report_html_generated(self, bids_copy, tmp_path):
        """Generated HTML report should exist, be non-empty, and
        contain the dataset name and subject count."""
        output_path = tmp_path / "report.html"
        generate_report(bids_copy, output_path)

        assert output_path.exists()
        html = output_path.read_text()
        assert len(html) > 100, "Report HTML should be non-trivial"

        # Should contain dataset info
        assert "Test Neuroimaging Dataset" in html
        assert "sub-01" in html

        # Should contain structural elements
        assert "<table>" in html
        assert "Completeness Matrix" in html


class TestFormatFileSize:
    """Verify file size formatting helper."""

    def test_format_file_size(self):
        """File sizes should be formatted as human-readable strings."""
        assert format_file_size(500) == "500 B"
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(1048576) == "1.00 MB"
        assert format_file_size(1073741824) == "1.00 GB"

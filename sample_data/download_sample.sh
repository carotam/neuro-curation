#!/usr/bin/env bash
# download_sample.sh — Fetch a small public DICOM dataset for pipeline demo.
#
# This downloads MRI DICOM test data from the dcm2niix GitHub repository.
# The data is already publicly available and commonly used for testing
# neuroimaging tools. It contains a small T1-weighted MRI series.
#
# Usage:
#   bash sample_data/download_sample.sh
#   # or: make download

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="${SCRIPT_DIR}/raw"
TEMP_DIR="${SCRIPT_DIR}/.download_tmp"

# Source: Chris Rorden's dcm2niix test dataset (public, no auth required)
# Contains small DICOM series suitable for conversion testing
DATASET_URL="https://github.com/neurolabusc/dcm_qa_nih/archive/refs/heads/master.zip"
DATASET_NAME="dcm_qa_nih-master"

echo "=== neuro-curation: Download Sample DICOM Data ==="
echo ""

# Check if data already exists
if [ -d "${RAW_DIR}" ] && [ "$(ls -A "${RAW_DIR}" 2>/dev/null)" ]; then
    echo "Sample data already exists in ${RAW_DIR}"
    echo "To re-download, run: rm -rf ${RAW_DIR} && bash $0"
    exit 0
fi

# Check for curl or wget
if command -v curl &>/dev/null; then
    DOWNLOADER="curl"
elif command -v wget &>/dev/null; then
    DOWNLOADER="wget"
else
    echo "ERROR: Neither curl nor wget found."
    echo ""
    echo "Manual download instructions:"
    echo "  1. Download: ${DATASET_URL}"
    echo "  2. Unzip into: ${RAW_DIR}/"
    echo "  3. Ensure DICOM files are directly under ${RAW_DIR}/"
    exit 1
fi

# Create directories
mkdir -p "${RAW_DIR}" "${TEMP_DIR}"

echo "Downloading test DICOM data from dcm_qa_nih..."
echo "Source: ${DATASET_URL}"
echo ""

# Download the zip archive
ZIP_FILE="${TEMP_DIR}/dcm_qa_nih.zip"
if [ "${DOWNLOADER}" = "curl" ]; then
    curl -L -o "${ZIP_FILE}" "${DATASET_URL}" --progress-bar
else
    wget -O "${ZIP_FILE}" "${DATASET_URL}" --show-progress
fi

echo ""
echo "Extracting DICOM files..."

# Unzip and move DICOM directories into raw/
unzip -q "${ZIP_FILE}" -d "${TEMP_DIR}"

# Copy the In/ directory which contains actual DICOM series
# dcm_qa_nih organizes data as: In/20180918GE/  In/20180918Ph/  In/20180918Si/
if [ -d "${TEMP_DIR}/${DATASET_NAME}/In" ]; then
    cp -r "${TEMP_DIR}/${DATASET_NAME}/In/"* "${RAW_DIR}/"
    echo "Extracted DICOM series to ${RAW_DIR}/"
else
    # Fallback: copy everything
    cp -r "${TEMP_DIR}/${DATASET_NAME}/"* "${RAW_DIR}/"
    echo "Extracted dataset to ${RAW_DIR}/"
fi

# Clean up temp files
rm -rf "${TEMP_DIR}"

# Show what was downloaded
echo ""
echo "Download complete. Contents:"
echo "---"
find "${RAW_DIR}" -type d | head -20
DICOM_COUNT=$(find "${RAW_DIR}" -type f | wc -l | tr -d ' ')
echo "---"
echo "Total files: ${DICOM_COUNT}"
echo ""
echo "You can now run the full pipeline:"
echo "  neuro-curation run --input sample_data/raw --output output --subject-id sub-01"

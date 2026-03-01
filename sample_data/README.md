# Sample Data

## Source

This directory contains public DICOM MRI data downloaded from the
[dcm_qa_nih](https://github.com/neurolabusc/dcm_qa_nih) repository,
maintained by Chris Rorden (creator of dcm2niix).

The dataset includes small MRI DICOM series from multiple scanner vendors
(GE, Philips, Siemens), commonly used for testing neuroimaging conversion
tools.

## License

The dcm_qa_nih test data is released under the
[BSD 2-Clause License](https://github.com/neurolabusc/dcm_qa_nih/blob/master/LICENSE).
All DICOM files are already de-identified for public distribution.

## Download

```bash
# Automatic (from project root):
make download

# Or directly:
bash sample_data/download_sample.sh
```

## Contents

After downloading, `raw/` will contain DICOM series organized by scanner:

```
raw/
├── 20180918GE/     # GE scanner series
├── 20180918Ph/     # Philips scanner series
└── 20180918Si/     # Siemens scanner series
```

## Note

The `raw/` directory is gitignored (DICOM files are too large for version
control). Each user must run the download script to fetch the data locally.
This mirrors real-world practice where raw imaging data is stored on
dedicated servers (e.g., XNAT) rather than in code repositories.

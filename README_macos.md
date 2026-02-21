# PDAL Filter CLI - macOS Distribution

This document describes how to build and run the standalone macOS executable for the PDAL filter CLI.

## Prerequisites

To build the executable, you need:

- macOS (Intel or Apple Silicon).
- Python 3.9 or newer.
- PDAL installed via Homebrew or Conda.

### Installing PDAL

**Via Homebrew (Recommended):**

```bash
brew install pdal
```

**Via Conda:**

```bash
conda create -n pdal_build -c conda-forge pdal python=3.10
conda activate pdal_build
```

## Building the Executable

Run the provided build script:

```bash
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
```

The script will:

1. Create a temporary virtual environment.
2. Install necessary Python dependencies (`pyinstaller`, `pdal`).
3. Build a standalone executable using `packaging/pdal_filter_macos.spec`.
4. Perform a sanity check.
5. Create a `dist/pdal_filter_macos.zip` archive.

The resulting binary will be located at `dist/pdal_filter/pdal_filter`.

## Architecture Support

By default, the executable is built for the architecture of the machine running the build script:

- **Apple Silicon (arm64):** Runs natively on M1/M2/M3 chips.
- **Intel (x86_64):** Runs on Intel Macs.

To create a universal binary, you would need to build on both and use `lipo`, or use a cross-compilation setup, which is
currently outside the scope of this script.

## Gatekeeper and Security

Because the resulting binary is unsigned, macOS Gatekeeper may block it from running.

### Running for the first time

1. Locate `pdal_filter` in Finder.
2. Right-click (or Control-click) the application and choose **Open**.
3. In the dialog that appears, click **Open**.

Alternatively, you can remove the quarantine attribute via terminal (this is only needed if you downloaded the binary):

```bash
# Check if the attribute exists first
xattr dist/pdal_filter/pdal_filter

# Remove it if com.apple.quarantine is listed
xattr -d com.apple.quarantine dist/pdal_filter/pdal_filter
```

*Note: If you built the binary locally using the build script, it will not have the quarantine attribute.*

## Usage

```bash
./dist/pdal_filter/pdal_filter --input input.las --output output.las --deduplicate
```

For more options, run:

```bash
./dist/pdal_filter/pdal_filter --help
```

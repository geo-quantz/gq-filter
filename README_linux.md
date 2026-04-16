# PDAL Filter CLI - Linux Distribution

This document describes how to build and run the standalone Linux executable for the PDAL filter CLI.

## Prerequisites

To build the executable, you need:

- A Linux distribution with `glibc` (e.g., Ubuntu, Debian, CentOS, Fedora).
- Python 3.9 or newer.
- PDAL installed on the system or in a Conda environment.

### Installing PDAL

**Via Conda (Recommended):**

```bash
conda create -n pdal_build -c conda-forge pdal python=3.10
conda activate pdal_build
```

**Via System Package Manager (Ubuntu/Debian):**

```bash
sudo apt-get update
sudo apt-get install pdal libpdal-dev
```

## Building the Executable

Run the provided build script:

```bash
chmod +x scripts/build_linux.sh
./scripts/build_linux.sh
```

The script will:

1. Create a temporary virtual environment.
2. Install necessary Python dependencies (`pyinstaller`, `pdal`).
3. Build a standalone executable using `packaging/gqfilter_linux.spec`.
4. Perform a sanity check.
5. Create a `dist/gqfilter_linux.tar.gz` archive.

The resulting binary will be located at `dist/gqfilter/gqfilter`.

## Usage

You can run the executable directly:

```bash
./dist/gqfilter/gqfilter --input input.las --output output.las --deduplicate
```

For more options, run:

```bash
./dist/gqfilter/gqfilter --help
```

## Troubleshooting

### Missing Shared Libraries

If you encounter errors about missing `.so` files (e.g., `libpdalcpp.so`), ensure that PDAL is correctly installed. The
build spec attempts to bundle PDAL libraries from Conda or common system paths. If you are using a non-standard path,
you may need to update `packaging/gqfilter_linux.spec`.

### GLIBC Version Errors

If you build the executable on a newer Linux distribution (e.g., Ubuntu 24.04) and try to run it on an older one (e.g.,
Ubuntu 20.04), you might see `GLIBC_X.Y not found` errors. For maximum compatibility, build the executable on the oldest
distribution you intend to support.

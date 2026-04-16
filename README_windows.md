# Windows Distribution Guide - PDAL Filter CLI

This document describes how to build and run the `gqfilter` tool as a standalone Windows executable.

## Prerequisites

To build the executable, you need a Windows machine with the following installed:

- **Python 3.9+**: [python.org](https://www.python.org/) (ensure "Add Python to PATH" is checked during installation).
- **PDAL**: It is highly recommended to use a Conda environment if you need specific PDAL drivers or plugins.
  Alternatively, you can install the `pdal` Python package via pip, which usually includes the basic DLLs.
- **Git** (optional): To clone the repository.

## Build Instructions

1. **Open PowerShell or Command Prompt** in the project root directory.
2. **Run the build script**:
    - Via PowerShell:
      ```powershell
      .\scripts\build_windows.ps1
      ```
    - Via Batch (double-click or run):
      ```cmd
      scripts\build_windows.bat
      ```

The script will:

- Create a temporary virtual environment (`.venv-build`).
- Install `pyinstaller`, `pdal`, and the current package.
- Generate a standalone executable using the configuration in `packaging/gqfilter.spec`.
- Perform a sanity check by running `gqfilter.exe --help`.

## Optional: Create MSI Installer

If you have the **WiX Toolset v3** installed, you can generate a Windows Installer (MSI) that adds the tool to the
system PATH:

1. **Run the MSI build script**:
   ```powershell
   .\scripts\build_msi.ps1
   ```
2. The installer will be created at `dist\gqfilter.msi`.

## Output Artifacts

After a successful build, the directory containing the executable and its dependencies will be located at:
`dist\gqfilter\`

The executable is `dist\gqfilter\gqfilter.exe`.

## Usage

You can run the executable from any terminal:

```cmd
dist\gqfilter\gqfilter.exe --input input.las --output output.las --intensity-min 100 --deduplicate
```

## Troubleshooting PDAL DLLs

PDAL is a complex C++ library with many runtime dependencies (DLLs) and plugins.

### Missing DLLs / "Entry Point Not Found"

If the EXE fails to launch due to missing DLLs:

1. **Conda Users**: Ensure you are running the build script from within an active Conda environment where PDAL is
   installed. The spec file is designed to automatically detect and bundle DLLs from `$env:CONDA_PREFIX\Library\bin`.
2. **Pip Users**: The `pdal` wheel from PyPI usually bundles core DLLs. However, some drivers (like E57 or certain
   database drivers) might require manual bundling if they are not part of the standard wheel.
3. **One-File vs One-Dir**: We use `--onedir` (directory distribution). This significantly improves startup
   performance compared to `--onefile`, as the application doesn't need to decompress itself to a temporary
   folder on every run.

### Filter/Writer Not Found

If the tool runs but reports that a specific filter or writer is missing (e.g., `writers.copc`):

- This usually means the required PDAL plugin (DLL) was not bundled or is not found in the search path.
- Check if the DLL exists in your environment and ensure it's being picked up by the `binaries` list in
  `packaging/gqfilter.spec`.

#!/bin/bash
set -e

# macOS Build Script for gqfilter (conda-forge based)

echo "Starting macOS build process..."

# 1. Conda check
if ! command -v conda >/dev/null 2>&1; then
    echo "ERROR: conda not found."
    echo "Install Miniforge with:"
    echo "    brew install miniforge"
    echo "Then open a new terminal (or 'conda init' your shell) and re-run this script."
    exit 1
fi

# Enable 'conda activate' inside this script
eval "$(conda shell.bash hook)"

ENV_NAME="${GQFILTER_ENV:-gqfilter_build_macos}"

if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "Creating conda env '$ENV_NAME' with pdal + python-pdal + pyinstaller..."
    conda create -y -n "$ENV_NAME" -c conda-forge \
        python=3.12 pdal python-pdal pyinstaller
fi

conda activate "$ENV_NAME"

# 2. Run PyInstaller
echo "Running PyInstaller..."
pyinstaller --clean --noconfirm packaging/gqfilter_macos.spec

# 3. Sanity check
if [ ! -d "dist/gqfilter" ]; then
    echo "Build failed: dist/gqfilter directory not found."
    exit 1
fi
echo "Build successful: dist/gqfilter/"
echo "Running sanity check (--help)..."
./dist/gqfilter/gqfilter --help > /dev/null
echo "Sanity check passed."

# 4. Ad-hoc code signing so the bundle launches on Apple Silicon after download
echo "Applying ad-hoc code signature..."
xattr -cr dist/gqfilter
codesign --force --deep --sign - dist/gqfilter
codesign --verify --deep --strict dist/gqfilter

# 5. Create archive
echo "Creating zip archive..."
rm -f dist/gqfilter_macos.zip
cd dist
zip -r gqfilter_macos.zip gqfilter
cd ..
echo "Archive created: dist/gqfilter_macos.zip"

echo "macOS build complete."
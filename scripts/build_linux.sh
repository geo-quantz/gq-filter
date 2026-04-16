#!/bin/bash
set -e

# Linux Build Script for PDAL Filter CLI

echo "Starting Linux build process..."

# 1. Setup environment
PYTHON_EXE=${PYTHON_EXE:-python3}
VENV_DIR="build_venv_linux"

if [ ! -x "$VENV_DIR/bin/python3" ]; then
    echo "Creating fresh virtual environment (removing any stale one)..."
    rm -rf "$VENV_DIR"
    $PYTHON_EXE -m venv "$VENV_DIR"
fi

source $VENV_DIR/bin/activate

VENV_PY="$VENV_DIR/bin/python3"

if [ ! -x "$VENV_PY" ]; then
    echo "ERROR: venv creation failed — $VENV_PY missing."
    echo "Your python3 ($PYTHON_EXE) may not support 'python -m venv'."
    exit 1
fi

echo "Ensuring pip is available in venv..."
"$VENV_PY" -m ensurepip --upgrade 2>/dev/null || true

echo "Installing dependencies..."
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install pyinstaller pdal

# 2. Run PyInstaller
echo "Running PyInstaller..."
pyinstaller --clean --noconfirm packaging/gqfilter_linux.spec

# 3. Sanity check
if [ -d "dist/gqfilter" ]; then
    echo "Build successful: dist/gqfilter/"
    echo "Running sanity check (--help)..."
    ./dist/gqfilter/gqfilter --help > /dev/null
    echo "Sanity check passed."
else
    echo "Build failed: dist/gqfilter directory not found."
    exit 1
fi

# 4. Create archive
echo "Creating tar.gz archive..."
tar -czf dist/gqfilter_linux.tar.gz -C dist gqfilter
echo "Archive created: dist/gqfilter_linux.tar.gz"

echo "Linux build complete."

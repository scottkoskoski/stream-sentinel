#!/bin/bash

# Simple build script for minimal XGBoost C++ wrapper.
# Locates the XGBoost shared library from the Python package automatically.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building Simple XGBoost C++ Wrapper..."

# Locate the XGBoost shared library from the active Python environment.
XGBOOST_LIB="$(python3 -c "import xgboost; from pathlib import Path; print(Path(xgboost.__file__).parent / 'lib' / 'libxgboost.so')" 2>/dev/null || true)"

if [ -z "$XGBOOST_LIB" ] || [ ! -f "$XGBOOST_LIB" ]; then
    echo "Error: XGBoost shared library not found."
    echo "Ensure xgboost is installed: pip install xgboost"
    exit 1
fi

echo "Using XGBoost library: $XGBOOST_LIB"

# Build directory
mkdir -p build_simple
cd build_simple

# Compile the wrapper
echo "Compiling C++ wrapper..."
g++ -std=c++17 -fPIC -O3 \
    -I../xgboost_headers \
    -I.. \
    -c ../simple_xgboost_wrapper.cpp \
    -o simple_xgboost_wrapper.o

echo "C++ wrapper compiled successfully"
echo ""
echo "Build completed successfully!"
echo "To build the Python extension, run: ./build_python_extension.sh"
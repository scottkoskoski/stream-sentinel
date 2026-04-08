#!/bin/bash

# Build script for Python extension module.
# Produces simple_xgboost_cpp.*.so that can be imported from Python.
# All paths are derived from the active Python environment -- no hardcoded paths.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building Python extension module for C++ XGBoost wrapper..."

# Derive paths from the active Python
PYTHON_EXECUTABLE="$(command -v python3)"
PYBIND11_INCLUDES="$($PYTHON_EXECUTABLE -m pybind11 --includes)"
PYTHON_EXTENSION_SUFFIX="$($PYTHON_EXECUTABLE -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"

echo "Python executable: $PYTHON_EXECUTABLE"
echo "Pybind11 includes: $PYBIND11_INCLUDES"
echo "Extension suffix: $PYTHON_EXTENSION_SUFFIX"

# Locate XGBoost shared library from the Python package
XGBOOST_LIB="$($PYTHON_EXECUTABLE -c "import xgboost; from pathlib import Path; print(Path(xgboost.__file__).parent / 'lib' / 'libxgboost.so')")"

if [ ! -f "$XGBOOST_LIB" ]; then
    echo "Error: XGBoost shared library not found at $XGBOOST_LIB"
    echo "Ensure xgboost is installed: pip install xgboost"
    exit 1
fi

echo "Using XGBoost library: $XGBOOST_LIB"

# Build directory
mkdir -p build_simple
cd build_simple

# Compile the wrapper object if not already compiled
if [ ! -f "simple_xgboost_wrapper.o" ]; then
    echo "Compiling C++ wrapper object..."
    g++ -std=c++17 -fPIC -O3 \
        -I../xgboost_headers \
        -I.. \
        -c ../simple_xgboost_wrapper.cpp \
        -o simple_xgboost_wrapper.o
    echo "C++ wrapper object compiled"
fi

# Compile Python extension module
echo "Compiling Python extension module..."
g++ -std=c++17 -fPIC -O3 -shared \
    $PYBIND11_INCLUDES \
    -I../xgboost_headers \
    -I.. \
    simple_xgboost_wrapper.o \
    ../simple_python_bindings.cpp \
    "$XGBOOST_LIB" \
    -o "simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}"

echo "Python extension module created: simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}"

# Install into the cpp/ directory so fast_inference.py can find it
INSTALL_DIR="$SCRIPT_DIR"
cp "simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}" "$INSTALL_DIR/"

echo "Extension module installed to: $INSTALL_DIR/simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}"

echo ""
echo "Build completed successfully!"
echo "You can now import the module with: import simple_xgboost_cpp"
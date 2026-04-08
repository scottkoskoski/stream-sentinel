#!/bin/bash

# Build script for Python extension module
# Compiles simple_xgboost_cpp.so for Python import

set -e

echo "Building Python extension module for C++ XGBoost wrapper..."

# Get Python and pybind11 configuration -- auto-detect or accept override via env var
PYTHON_EXECUTABLE="${PYTHON_EXECUTABLE:-$(command -v python3)}"
PYBIND11_INCLUDES="$($PYTHON_EXECUTABLE -m pybind11 --includes)"
PYTHON_EXTENSION_SUFFIX="$($PYTHON_EXECUTABLE -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"

echo "Python executable: $PYTHON_EXECUTABLE"
echo "Pybind11 includes: $PYBIND11_INCLUDES"
echo "Extension suffix: $PYTHON_EXTENSION_SUFFIX"

# Build directory  
mkdir -p build_simple
cd build_simple

# XGBoost library path -- auto-detect from Python or accept override via env var
if [ -z "$XGBOOST_LIB" ]; then
    XGBOOST_LIB="$($PYTHON_EXECUTABLE -c "import xgboost, pathlib; print(pathlib.Path(xgboost.__file__).parent / 'lib' / 'libxgboost.so')" 2>/dev/null || true)"
fi

if [ -z "$XGBOOST_LIB" ] || [ ! -f "$XGBOOST_LIB" ]; then
    echo "Error: XGBoost shared library not found."
    echo "Set XGBOOST_LIB to the path of libxgboost.so, or install xgboost in the active Python environment."
    exit 1
fi

echo "Using XGBoost library: $XGBOOST_LIB"

# Compile the wrapper object if not already compiled
if [ ! -f "simple_xgboost_wrapper.o" ]; then
    echo "Compiling C++ wrapper object..."
    g++ -std=c++17 -fPIC -O3 \
        -I../xgboost_headers \
        -c ../simple_xgboost_wrapper.cpp \
        -o simple_xgboost_wrapper.o
    echo "C++ wrapper object compiled"
fi

# Compile Python extension module
echo "Compiling Python extension module..."
g++ -std=c++17 -fPIC -O3 -shared \
    $PYBIND11_INCLUDES \
    -I../xgboost_headers \
    simple_xgboost_wrapper.o \
    ../simple_python_bindings.cpp \
    "$XGBOOST_LIB" \
    -o "simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}"

echo "Python extension module created: simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}"

# Copy to a location where Python can import it
# Derive install dir relative to the script's own location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR}"
cp "simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}" "$INSTALL_DIR/"

echo "Extension module installed to: $INSTALL_DIR/simple_xgboost_cpp${PYTHON_EXTENSION_SUFFIX}"

echo ""
echo "Build completed successfully!"
echo "You can now import the module with: import simple_xgboost_cpp"
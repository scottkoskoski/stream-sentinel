#!/bin/bash

# Simple build script for minimal XGBoost C++ wrapper
# Uses downloaded headers and existing shared library

set -e

echo "Building Simple XGBoost C++ Wrapper..."

# Build directory  
mkdir -p build_simple
cd build_simple

# XGBoost library path -- auto-detect from Python or accept override via env var
if [ -z "$XGBOOST_LIB" ]; then
    XGBOOST_LIB="$(python3 -c "import xgboost, pathlib; print(pathlib.Path(xgboost.__file__).parent / 'lib' / 'libxgboost.so')" 2>/dev/null || true)"
fi

if [ -z "$XGBOOST_LIB" ] || [ ! -f "$XGBOOST_LIB" ]; then
    echo "Error: XGBoost shared library not found."
    echo "Set XGBOOST_LIB to the path of libxgboost.so, or install xgboost in the active Python environment."
    exit 1
fi

echo "Using XGBoost library: $XGBOOST_LIB"

# Compile the simple wrapper
echo "Compiling C++ wrapper..."
g++ -std=c++17 -fPIC -O3 \
    -I../xgboost_headers \
    -I. \
    -c ../simple_xgboost_wrapper.cpp \
    -o simple_xgboost_wrapper.o

echo "C++ wrapper compiled successfully"

# Create simple test executable
echo "Creating test executable..."
cat > test_wrapper.cpp << 'EOF'
#include "../simple_xgboost_wrapper.hpp"
#include <iostream>
#include <vector>

int main() {
    std::cout << "Testing Simple XGBoost Wrapper..." << std::endl;
    
    stream_sentinel::SimpleXGBoostWrapper wrapper;
    
    // Test model loading
    std::string model_path = "../../models/ieee_fraud_model_cpp.json";
    
    if (wrapper.load_model(model_path)) {
        std::cout << "Model loaded successfully" << std::endl;
        
        // Test prediction with dummy features (200 features)
        std::vector<float> test_features(200, 1.0f);
        
        double prediction = wrapper.predict(test_features);
        
        if (prediction >= 0.0) {
            std::cout << "Prediction successful: " << prediction << std::endl;
        } else {
            std::cout << "Prediction failed: " << wrapper.get_last_error() << std::endl;
            return 1;
        }
    } else {
        std::cout << "Model loading failed: " << wrapper.get_last_error() << std::endl;
        return 1;
    }
    
    std::cout << "Simple XGBoost wrapper test PASSED!" << std::endl;
    return 0;
}
EOF

# Compile test executable
g++ -std=c++17 -O3 \
    -I../xgboost_headers \
    simple_xgboost_wrapper.o \
    test_wrapper.cpp \
    "$XGBOOST_LIB" \
    -o test_simple_wrapper

echo "Test executable created: test_simple_wrapper"

echo ""
echo "Build completed successfully!"
echo "To test: cd build_simple && ./test_simple_wrapper"
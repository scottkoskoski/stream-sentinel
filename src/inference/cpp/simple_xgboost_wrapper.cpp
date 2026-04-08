/**
 * @file simple_xgboost_wrapper.cpp
 * @brief Implementation of minimal XGBoost C++ wrapper
 * 
 * Simple implementation focused on drop-in replacement performance.
 * Total target: ~50-100 lines for maximum simplicity and maintainability.
 */

#include "simple_xgboost_wrapper.hpp"
#include <iostream>
#include <fstream>
#include <cmath>  // For NAN

namespace stream_sentinel {

SimpleXGBoostWrapper::SimpleXGBoostWrapper() = default;

SimpleXGBoostWrapper::~SimpleXGBoostWrapper() {
    cleanup();
}

bool SimpleXGBoostWrapper::load_model(const std::string& model_path) {
    cleanup(); // Clean up any existing model

    // Accept the model path as-is.  The caller (fast_inference.py) is
    // responsible for computing the correct _cpp.json path.  If the
    // caller passes a .pkl path by mistake, try the _cpp.json variant
    // as a convenience fallback.
    std::string native_model_path = model_path;
    size_t pkl_pos = native_model_path.find(".pkl");
    if (pkl_pos != std::string::npos) {
        native_model_path.replace(pkl_pos, 4, "_cpp.json");
    }

    // Check if the model file exists
    std::ifstream file(native_model_path);
    if (!file.good()) {
        return set_error("Native model file not found: " + native_model_path +
                        " (Run: python src/inference/export_model.py)");
    }
    file.close();
    
    // Load XGBoost model using C API
    const char* model_path_cstr = native_model_path.c_str();
    if (XGBoosterCreate(nullptr, 0, &booster_) != 0) {
        return set_error("Failed to create XGBoost booster");
    }
    
    if (XGBoosterLoadModel(booster_, model_path_cstr) != 0) {
        cleanup();
        return set_error("Failed to load model from: " + native_model_path);
    }
    
    return true;
}

double SimpleXGBoostWrapper::predict(const std::vector<float>& features) {
    if (!booster_) {
        set_error("Model not loaded");
        return -1.0;
    }
    
    if (features.empty()) {
        set_error("Empty features vector");
        return -1.0;
    }
    
    // Create DMatrix from features
    const float* data = features.data();
    bst_ulong nrow = 1;
    bst_ulong ncol = features.size();
    
    if (XGDMatrixCreateFromMat(data, nrow, ncol, NAN, &dmatrix_) != 0) {
        set_error("Failed to create DMatrix from features");
        return -1.0;
    }
    
    // Perform prediction
    bst_ulong out_len = 0;
    const float* out_result = nullptr;
    
    int ret = XGBoosterPredict(booster_, dmatrix_, 0, 0, 0, &out_len, &out_result);
    
    // Clean up DMatrix immediately after prediction (only if valid)
    if (dmatrix_) {
        XGDMatrixFree(dmatrix_);
        dmatrix_ = nullptr;
    }
    
    if (ret != 0 || out_len == 0 || !out_result) {
        set_error("XGBoost prediction failed");
        return -1.0;
    }
    
    // Our exported model returns probabilities directly (verified in export)
    // No sigmoid transformation needed - just return the probability
    double probability = static_cast<double>(out_result[0]);
    
    // Sanity check: ensure probability is in valid range
    if (probability < 0.0 || probability > 1.0) {
        set_error("Invalid probability returned: " + std::to_string(probability));
        return -1.0;
    }
    
    return probability;
}

void SimpleXGBoostWrapper::cleanup() {
    if (dmatrix_) {
        XGDMatrixFree(dmatrix_);
        dmatrix_ = nullptr;
    }
    if (booster_) {
        XGBoosterFree(booster_);
        booster_ = nullptr;
    }
    last_error_.clear();
}

bool SimpleXGBoostWrapper::set_error(const std::string& error) {
    last_error_ = error;
    return false;
}

} // namespace stream_sentinel
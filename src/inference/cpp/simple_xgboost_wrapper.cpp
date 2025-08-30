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

namespace stream_sentinel {

SimpleXGBoostWrapper::SimpleXGBoostWrapper() = default;

SimpleXGBoostWrapper::~SimpleXGBoostWrapper() {
    cleanup();
}

bool SimpleXGBoostWrapper::load_model(const std::string& model_path) {
    cleanup(); // Clean up any existing model
    
    // Check if file exists
    std::ifstream file(model_path);
    if (!file.good()) {
        return set_error("Model file not found: " + model_path);
    }
    file.close();
    
    // Load XGBoost model using C API
    const char* model_path_cstr = model_path.c_str();
    if (XGBoosterCreate(nullptr, 0, &booster_) != 0) {
        return set_error("Failed to create XGBoost booster");
    }
    
    if (XGBoosterLoadModel(booster_, model_path_cstr) != 0) {
        cleanup();
        return set_error("Failed to load model from: " + model_path);
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
    
    // Clean up DMatrix immediately after prediction
    XGDMatrixFree(dmatrix_);
    dmatrix_ = nullptr;
    
    if (ret != 0 || out_len == 0 || !out_result) {
        set_error("XGBoost prediction failed");
        return -1.0;
    }
    
    // For binary classification, XGBoost returns log-odds by default
    // We need the probability, which is typically the second class probability
    // or we can use sigmoid transformation: 1 / (1 + exp(-logit))
    double probability = static_cast<double>(out_result[0]);
    
    // If output is already a probability (0-1), return directly
    // If it's log-odds, apply sigmoid transformation
    if (probability < 0.0 || probability > 1.0) {
        // Apply sigmoid: 1 / (1 + exp(-x))
        probability = 1.0 / (1.0 + std::exp(-probability));
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
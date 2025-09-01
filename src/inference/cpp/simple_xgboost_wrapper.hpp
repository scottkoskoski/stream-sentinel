/**
 * @file simple_xgboost_wrapper.hpp
 * @brief Minimal XGBoost C++ wrapper for fraud detection performance optimization
 * 
 * Simple, focused wrapper designed for drop-in replacement of Python inference.
 * Target: 50-100 lines total implementation for maximum simplicity.
 */

#pragma once

#include <vector>
#include <string>
#include <memory>
#include "xgboost_headers/c_api.h"

namespace stream_sentinel {

/**
 * @brief Simple XGBoost wrapper for fraud detection
 * 
 * Minimal interface focused on single inference performance.
 * Designed as drop-in replacement for Python XGBoost.
 */
class SimpleXGBoostWrapper {
public:
    /**
     * @brief Constructor
     */
    SimpleXGBoostWrapper();
    
    /**
     * @brief Destructor with resource cleanup
     */
    ~SimpleXGBoostWrapper();
    
    /**
     * @brief Load XGBoost model from file
     * @param model_path Path to XGBoost model (.pkl or .model)
     * @return true if successful
     */
    bool load_model(const std::string& model_path);
    
    /**
     * @brief Predict fraud probability for feature vector
     * @param features Input features (same format as Python version)
     * @return Fraud probability [0.0, 1.0] or -1.0 on error
     */
    double predict(const std::vector<float>& features);
    
    /**
     * @brief Get last error message
     */
    const std::string& get_last_error() const { return last_error_; }
    
    /**
     * @brief Check if model is loaded
     */
    bool is_loaded() const { return booster_ != nullptr; }

private:
    BoosterHandle booster_ = nullptr;
    DMatrixHandle dmatrix_ = nullptr;
    std::string last_error_;
    
    void cleanup();
    bool set_error(const std::string& error);
};

} // namespace stream_sentinel
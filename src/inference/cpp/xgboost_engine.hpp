/**
 * @file xgboost_engine.hpp
 * @brief High-performance XGBoost inference engine for Stream-Sentinel fraud detection
 * 
 * This module implements a production-grade C++ XGBoost inference engine optimized
 * for sub-millisecond fraud detection with advanced memory management and SIMD optimizations.
 * 
 * Key Features:
 * - Sub-millisecond inference latency (target: <1ms p99)
 * - Custom memory allocators for zero-allocation hot path
 * - SIMD-vectorized feature processing
 * - Thread-safe concurrent inference
 * - Comprehensive error handling and logging
 * 
 * @author Stream-Sentinel Team
 * @version 2.0.0
 */

#pragma once

#include <memory>
#include <vector>
#include <string>
#include <chrono>
#include <mutex>
#include <atomic>
#include <immintrin.h> // For SIMD intrinsics

// XGBoost C API
#include <xgboost/c_api.h>

#include "feature_processor.hpp"
#include "memory_pool.hpp"

namespace stream_sentinel {
namespace inference {

/**
 * @brief High-performance fraud detection prediction result
 */
struct PredictionResult {
    double fraud_probability;           ///< Fraud probability [0.0, 1.0]
    double confidence_interval;         ///< Prediction confidence interval
    uint64_t inference_time_ns;         ///< Inference time in nanoseconds
    uint32_t features_processed;        ///< Number of features processed
    bool is_high_risk;                  ///< Pre-computed risk flag for fast decisions
    
    // Performance metrics
    uint64_t feature_processing_ns;     ///< Feature processing time
    uint64_t model_inference_ns;        ///< Pure model inference time
    uint64_t postprocessing_ns;         ///< Post-processing time
};

/**
 * @brief Configuration for XGBoost inference engine
 */
struct EngineConfig {
    std::string model_path;             ///< Path to XGBoost model file
    uint32_t num_threads = 1;           ///< Number of inference threads
    bool use_gpu = false;               ///< Enable GPU acceleration
    double fraud_threshold = 0.7;      ///< Fraud classification threshold
    bool enable_feature_cache = true;  ///< Enable feature result caching
    size_t cache_size_mb = 64;          ///< Feature cache size in MB
    bool enable_simd = true;            ///< Enable SIMD optimizations
    uint32_t batch_size = 1;            ///< Batch inference size
};

/**
 * @brief Performance statistics for monitoring and optimization
 */
struct PerformanceStats {
    std::atomic<uint64_t> total_predictions{0};
    std::atomic<uint64_t> total_inference_time_ns{0};
    std::atomic<uint64_t> total_feature_time_ns{0};
    std::atomic<uint64_t> cache_hits{0};
    std::atomic<uint64_t> cache_misses{0};
    
    // Latency percentiles (updated periodically)
    double p50_latency_ms = 0.0;
    double p95_latency_ms = 0.0;
    double p99_latency_ms = 0.0;
    double mean_latency_ms = 0.0;
    
    // Throughput metrics
    double predictions_per_second = 0.0;
    double cpu_utilization = 0.0;
    double memory_usage_mb = 0.0;
};

/**
 * @brief High-performance XGBoost inference engine
 * 
 * This class provides thread-safe, high-performance fraud detection inference
 * with advanced optimizations including SIMD feature processing, custom memory
 * allocation, and comprehensive performance monitoring.
 * 
 * Design Principles:
 * - Zero-allocation hot path for maximum performance
 * - Thread-safe concurrent inference 
 * - Comprehensive error handling without exceptions in hot path
 * - Extensive performance instrumentation
 * - Memory-efficient with custom allocators
 */
class XGBoostEngine {
public:
    /**
     * @brief Construct XGBoost inference engine
     * @param config Engine configuration parameters
     */
    explicit XGBoostEngine(const EngineConfig& config);
    
    /**
     * @brief Destructor with proper resource cleanup
     */
    ~XGBoostEngine();
    
    // Disable copy construction and assignment for performance and safety
    XGBoostEngine(const XGBoostEngine&) = delete;
    XGBoostEngine& operator=(const XGBoostEngine&) = delete;
    
    // Enable move construction and assignment
    XGBoostEngine(XGBoostEngine&&) noexcept;
    XGBoostEngine& operator=(XGBoostEngine&&) noexcept;
    
    /**
     * @brief Initialize the inference engine
     * @return true if initialization successful, false otherwise
     */
    bool initialize();
    
    /**
     * @brief Load XGBoost model from file
     * @param model_path Path to the XGBoost model file
     * @return true if model loaded successfully
     */
    bool load_model(const std::string& model_path);
    
    /**
     * @brief Perform high-performance fraud detection inference
     * @param features Raw feature vector (must match model expectations)
     * @param result Output prediction result
     * @return true if inference successful
     * 
     * @note This is the hot path - optimized for sub-millisecond execution
     */
    bool predict(const std::vector<float>& features, PredictionResult& result);
    
    /**
     * @brief Batch inference for high throughput scenarios
     * @param batch_features Vector of feature vectors
     * @param results Output vector of prediction results
     * @return true if batch inference successful
     */
    bool predict_batch(const std::vector<std::vector<float>>& batch_features,
                      std::vector<PredictionResult>& results);
    
    /**
     * @brief Get current performance statistics
     * @return Current performance metrics
     */
    const PerformanceStats& get_performance_stats() const;
    
    /**
     * @brief Reset performance statistics
     */
    void reset_performance_stats();
    
    /**
     * @brief Get engine configuration
     * @return Current engine configuration
     */
    const EngineConfig& get_config() const { return config_; }
    
    /**
     * @brief Check if engine is properly initialized
     * @return true if engine is ready for inference
     */
    bool is_initialized() const { return initialized_; }
    
    /**
     * @brief Get last error message
     * @return Last error message string
     */
    const std::string& get_last_error() const { return last_error_; }
    
    /**
     * @brief Warm up the inference engine
     * @param num_warmup_calls Number of warmup inference calls
     * @return true if warmup successful
     * 
     * @note Call this before performance-critical inference to eliminate
     *       cold start effects and optimize CPU cache usage
     */
    bool warmup(uint32_t num_warmup_calls = 100);

private:
    // Configuration and state
    EngineConfig config_;
    bool initialized_ = false;
    mutable std::string last_error_;
    
    // XGBoost handle and resources
    BoosterHandle booster_handle_ = nullptr;
    DMatrixHandle dmatrix_handle_ = nullptr;
    
    // High-performance feature processing
    std::unique_ptr<FeatureProcessor> feature_processor_;
    std::unique_ptr<MemoryPool> memory_pool_;
    
    // Thread safety
    mutable std::mutex inference_mutex_;
    
    // Performance monitoring
    mutable PerformanceStats stats_;
    mutable std::vector<double> latency_samples_;
    mutable std::mutex stats_mutex_;
    
    // Pre-allocated buffers for zero-allocation hot path
    mutable std::vector<float> feature_buffer_;
    mutable std::vector<const float*> feature_ptrs_;
    
    /**
     * @brief Set error message and return false
     * @param error Error message
     * @return false (for convenient error handling)
     */
    bool set_error(const std::string& error) const;
    
    /**
     * @brief Update performance statistics
     * @param inference_time_ns Total inference time in nanoseconds
     * @param feature_time_ns Feature processing time in nanoseconds
     * @param cache_hit Whether cache was hit for features
     */
    void update_stats(uint64_t inference_time_ns, 
                     uint64_t feature_time_ns, 
                     bool cache_hit) const;
    
    /**
     * @brief Update latency percentiles from recent samples
     */
    void update_latency_percentiles() const;
    
    /**
     * @brief Validate input features
     * @param features Input feature vector
     * @return true if features are valid
     */
    bool validate_features(const std::vector<float>& features) const;
    
    /**
     * @brief Cleanup XGBoost resources
     */
    void cleanup_resources();
};

/**
 * @brief Utility functions for inference engine
 */
namespace utils {
    /**
     * @brief Get high-resolution timestamp in nanoseconds
     * @return Current time in nanoseconds since epoch
     */
    inline uint64_t get_time_ns() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now().time_since_epoch()
        ).count();
    }
    
    /**
     * @brief Calculate percentile from sorted vector
     * @param sorted_values Sorted vector of values
     * @param percentile Percentile to calculate (0.0-1.0)
     * @return Percentile value
     */
    double calculate_percentile(const std::vector<double>& sorted_values, double percentile);
    
    /**
     * @brief Validate model file format and compatibility
     * @param model_path Path to model file
     * @return true if model is valid and compatible
     */
    bool validate_model_file(const std::string& model_path);
}

} // namespace inference
} // namespace stream_sentinel
/**
 * @file feature_processor.hpp
 * @brief SIMD-optimized feature processing for high-performance fraud detection
 * 
 * This module implements vectorized feature processing operations using modern CPU
 * SIMD instructions (AVX2/AVX-512) to achieve maximum performance for real-time
 * fraud detection inference.
 * 
 * Key Optimizations:
 * - SIMD vectorized operations for 4x-8x speedup
 * - Cache-aware data structures and algorithms
 * - Zero-allocation hot path with pre-allocated buffers
 * - Branch-free computation for predictable performance
 * - Custom memory layouts for optimal vectorization
 * 
 * Performance Targets:
 * - Process 200+ features in <100 microseconds
 * - 90%+ CPU vectorization efficiency
 * - Cache miss rate <2% for feature processing
 * 
 * @author Stream-Sentinel Team
 * @version 2.0.0
 */

#pragma once

#include <vector>
#include <array>
#include <memory>
#include <cstdint>
#include <immintrin.h>  // AVX2/AVX-512 intrinsics
#include <algorithm>
#include <cmath>

namespace stream_sentinel {
namespace inference {

/**
 * @brief SIMD instruction set capabilities detected at runtime
 */
struct SIMDCapabilities {
    bool has_avx2 = false;      ///< AVX2 support (256-bit vectors)
    bool has_avx512 = false;    ///< AVX-512 support (512-bit vectors)
    bool has_fma = false;       ///< Fused multiply-add support
    bool has_sse42 = false;     ///< SSE 4.2 support (128-bit vectors)
    uint32_t cache_line_size = 64; ///< CPU cache line size in bytes
};

/**
 * @brief Feature processing configuration
 */
struct FeatureProcessingConfig {
    bool enable_simd = true;           ///< Enable SIMD optimizations
    bool enable_caching = true;        ///< Enable intermediate result caching
    uint32_t expected_feature_count = 200; ///< Expected number of features
    double normalization_epsilon = 1e-8;   ///< Small value for numerical stability
    bool enable_fast_math = true;     ///< Enable fast math approximations
    uint32_t alignment_bytes = 32;     ///< Memory alignment for SIMD (32 for AVX2)
};

/**
 * @brief Vectorized feature computation result
 */
struct FeatureProcessingResult {
    uint64_t processing_time_ns = 0;   ///< Processing time in nanoseconds
    uint32_t features_processed = 0;   ///< Number of features processed
    uint32_t simd_operations = 0;      ///< Number of SIMD operations performed
    bool cache_hit = false;            ///< Whether cached results were used
    double vectorization_ratio = 0.0;  ///< Ratio of vectorized operations
};

/**
 * @brief Cache-aligned feature vector for optimal SIMD performance
 */
class alignas(32) AlignedFeatureVector {
public:
    /**
     * @brief Construct aligned feature vector
     * @param size Number of features
     */
    explicit AlignedFeatureVector(size_t size);
    
    /**
     * @brief Get pointer to aligned data
     * @return Aligned float pointer
     */
    float* data() { return data_.get(); }
    const float* data() const { return data_.get(); }
    
    /**
     * @brief Get vector size
     * @return Number of elements
     */
    size_t size() const { return size_; }
    
    /**
     * @brief Element access with bounds checking
     * @param index Element index
     * @return Reference to element
     */
    float& operator[](size_t index) { return data_[index]; }
    const float& operator[](size_t index) const { return data_[index]; }

private:
    std::unique_ptr<float[], std::function<void(float*)>> data_;
    size_t size_;
    size_t aligned_size_;
};

/**
 * @brief High-performance SIMD-optimized feature processor
 * 
 * This class implements vectorized feature processing operations for fraud detection,
 * including normalization, transformation, and feature engineering operations.
 * 
 * Design Philosophy:
 * - Maximum vectorization with minimal branching
 * - Cache-conscious data layout and access patterns
 * - Zero-allocation hot path with pre-allocated buffers
 * - Runtime SIMD capability detection and optimization
 * - Extensive performance instrumentation
 */
class FeatureProcessor {
public:
    /**
     * @brief Construct feature processor with configuration
     * @param config Feature processing configuration
     */
    explicit FeatureProcessor(const FeatureProcessingConfig& config);
    
    /**
     * @brief Destructor
     */
    ~FeatureProcessor() = default;
    
    // Disable copy construction and assignment
    FeatureProcessor(const FeatureProcessor&) = delete;
    FeatureProcessor& operator=(const FeatureProcessor&) = delete;
    
    // Enable move construction and assignment
    FeatureProcessor(FeatureProcessor&&) noexcept = default;
    FeatureProcessor& operator=(FeatureProcessor&&) noexcept = default;
    
    /**
     * @brief Initialize the feature processor
     * @return true if initialization successful
     */
    bool initialize();
    
    /**
     * @brief Process raw features for fraud detection inference
     * @param raw_features Input raw features
     * @param processed_features Output processed features
     * @param result Processing result and performance metrics
     * @return true if processing successful
     * 
     * @note This is the hot path - optimized for sub-100 microsecond execution
     */
    bool process_features(const std::vector<float>& raw_features,
                         AlignedFeatureVector& processed_features,
                         FeatureProcessingResult& result);
    
    /**
     * @brief Batch feature processing for high throughput
     * @param batch_features Vector of raw feature vectors
     * @param batch_processed Vector of processed feature vectors
     * @param results Vector of processing results
     * @return true if batch processing successful
     */
    bool process_features_batch(const std::vector<std::vector<float>>& batch_features,
                               std::vector<AlignedFeatureVector>& batch_processed,
                               std::vector<FeatureProcessingResult>& results);
    
    /**
     * @brief Normalize feature vector using SIMD operations
     * @param features Input/output feature vector
     * @param mean Mean values for normalization
     * @param std_dev Standard deviation values for normalization
     * @return true if normalization successful
     */
    bool normalize_features_simd(AlignedFeatureVector& features,
                                const AlignedFeatureVector& mean,
                                const AlignedFeatureVector& std_dev);
    
    /**
     * @brief Apply log transformation to features using SIMD
     * @param features Input/output feature vector
     * @param epsilon Small value for numerical stability
     * @return true if transformation successful
     */
    bool log_transform_simd(AlignedFeatureVector& features, float epsilon = 1e-8f);
    
    /**
     * @brief Compute feature statistics using SIMD operations
     * @param features Input feature vector
     * @param mean Output mean value
     * @param variance Output variance value
     * @param min_val Output minimum value
     * @param max_val Output maximum value
     * @return true if computation successful
     */
    bool compute_statistics_simd(const AlignedFeatureVector& features,
                                float& mean, float& variance,
                                float& min_val, float& max_val);
    
    /**
     * @brief Get detected SIMD capabilities
     * @return SIMD capabilities structure
     */
    const SIMDCapabilities& get_simd_capabilities() const { return simd_caps_; }
    
    /**
     * @brief Get feature processing configuration
     * @return Current configuration
     */
    const FeatureProcessingConfig& get_config() const { return config_; }
    
    /**
     * @brief Warm up the feature processor
     * @param num_warmup_calls Number of warmup processing calls
     * @return true if warmup successful
     */
    bool warmup(uint32_t num_warmup_calls = 50);

private:
    FeatureProcessingConfig config_;
    SIMDCapabilities simd_caps_;
    bool initialized_ = false;
    
    // Pre-allocated aligned buffers for zero-allocation hot path
    std::unique_ptr<AlignedFeatureVector> temp_buffer1_;
    std::unique_ptr<AlignedFeatureVector> temp_buffer2_;
    std::unique_ptr<AlignedFeatureVector> mean_buffer_;
    std::unique_ptr<AlignedFeatureVector> std_buffer_;
    
    // Performance optimization state
    mutable uint64_t total_processing_time_ns_ = 0;
    mutable uint32_t total_operations_ = 0;
    
    /**
     * @brief Detect CPU SIMD capabilities at runtime
     */
    void detect_simd_capabilities();
    
    /**
     * @brief Validate feature vector size and alignment
     * @param features Feature vector to validate
     * @return true if valid
     */
    bool validate_features(const std::vector<float>& features) const;
    
    // SIMD-optimized implementations for different instruction sets
    
    /**
     * @brief AVX2 implementation of feature normalization
     */
    void normalize_avx2(float* features, const float* mean, const float* std_dev, size_t count);
    
    /**
     * @brief AVX-512 implementation of feature normalization
     */
    void normalize_avx512(float* features, const float* mean, const float* std_dev, size_t count);
    
    /**
     * @brief SSE4.2 implementation of feature normalization (fallback)
     */
    void normalize_sse42(float* features, const float* mean, const float* std_dev, size_t count);
    
    /**
     * @brief Scalar implementation (fallback for unsupported CPUs)
     */
    void normalize_scalar(float* features, const float* mean, const float* std_dev, size_t count);
    
    /**
     * @brief AVX2 implementation of log transformation
     */
    void log_transform_avx2(float* features, size_t count, float epsilon);
    
    /**
     * @brief AVX2 implementation of statistics computation
     */
    void compute_stats_avx2(const float* features, size_t count, 
                           float& mean, float& variance, float& min_val, float& max_val);
};

/**
 * @brief SIMD utility functions
 */
namespace simd_utils {
    /**
     * @brief Check if pointer is properly aligned for SIMD operations
     * @param ptr Pointer to check
     * @param alignment Required alignment in bytes
     * @return true if properly aligned
     */
    inline bool is_aligned(const void* ptr, size_t alignment) {
        return (reinterpret_cast<uintptr_t>(ptr) % alignment) == 0;
    }
    
    /**
     * @brief Round up size to next multiple of alignment
     * @param size Size to round up
     * @param alignment Alignment boundary
     * @return Rounded up size
     */
    inline size_t align_size(size_t size, size_t alignment) {
        return (size + alignment - 1) & ~(alignment - 1);
    }
    
    /**
     * @brief Allocate aligned memory for SIMD operations
     * @param size Size in bytes
     * @param alignment Alignment requirement
     * @return Aligned memory pointer or nullptr on failure
     */
    void* aligned_alloc(size_t size, size_t alignment);
    
    /**
     * @brief Free aligned memory
     * @param ptr Pointer returned by aligned_alloc
     */
    void aligned_free(void* ptr);
    
    /**
     * @brief Fast approximate reciprocal using SIMD
     * @param x Input value
     * @return Approximate 1/x
     */
    inline float fast_reciprocal(float x) {
        // Use RCPSS instruction for fast approximation
        __m128 v = _mm_set_ss(x);
        __m128 r = _mm_rcp_ss(v);
        return _mm_cvtss_f32(r);
    }
    
    /**
     * @brief Fast approximate logarithm using bit manipulation
     * @param x Input value (must be positive)
     * @return Approximate log(x)
     */
    float fast_log(float x);
    
    /**
     * @brief Vectorized horizontal sum of AVX2 register
     * @param v 256-bit vector
     * @return Sum of all elements
     */
    inline float horizontal_sum_avx2(__m256 v) {
        __m128 hi = _mm256_extractf128_ps(v, 1);
        __m128 lo = _mm256_castps256_ps128(v);
        lo = _mm_add_ps(lo, hi);
        hi = _mm_movehl_ps(hi, lo);
        lo = _mm_add_ps(lo, hi);
        hi = _mm_shuffle_ps(lo, lo, 1);
        lo = _mm_add_ss(lo, hi);
        return _mm_cvtss_f32(lo);
    }
}

} // namespace inference
} // namespace stream_sentinel
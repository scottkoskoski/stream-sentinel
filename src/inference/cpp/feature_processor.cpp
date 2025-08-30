/**
 * @file feature_processor.cpp
 * @brief Implementation of SIMD-optimized feature processing
 * 
 * This file implements vectorized feature processing operations for maximum
 * performance in fraud detection inference.
 * 
 * @author Stream-Sentinel Team
 * @version 2.0.0
 */

#include "feature_processor.hpp"
#include <cstring>
#include <cstdlib>
#include <chrono>
#include <algorithm>
#include <numeric>

#ifdef _MSC_VER
#include <intrin.h>
#else
#include <x86intrin.h>
#include <cpuid.h>
#endif

namespace stream_sentinel {
namespace inference {

namespace {
    /**
     * @brief Check CPUID for specific feature support
     */
    bool check_cpuid_feature(uint32_t leaf, uint32_t subleaf, uint32_t reg, uint32_t bit) {
        uint32_t eax, ebx, ecx, edx;
        
#ifdef _MSC_VER
        int cpu_info[4];
        __cpuidex(cpu_info, leaf, subleaf);
        eax = cpu_info[0]; ebx = cpu_info[1]; ecx = cpu_info[2]; edx = cpu_info[3];
#else
        __cpuid_count(leaf, subleaf, eax, ebx, ecx, edx);
#endif
        
        uint32_t target_reg = (reg == 0) ? eax : (reg == 1) ? ebx : (reg == 2) ? ecx : edx;
        return (target_reg & (1u << bit)) != 0;
    }
}

// AlignedFeatureVector Implementation
AlignedFeatureVector::AlignedFeatureVector(size_t size) : size_(size) {
    // Align size to 32-byte boundary for AVX2 optimization
    aligned_size_ = simd_utils::align_size(size * sizeof(float), 32) / sizeof(float);
    
    // Allocate aligned memory using custom allocator
    float* raw_ptr = static_cast<float*>(simd_utils::aligned_alloc(
        aligned_size_ * sizeof(float), 32));
    
    if (!raw_ptr) {
        throw std::bad_alloc();
    }
    
    // Initialize with zeros for predictable behavior
    std::memset(raw_ptr, 0, aligned_size_ * sizeof(float));
    
    // Wrap in unique_ptr with custom deleter
    data_ = std::unique_ptr<float[], std::function<void(float*)>>(
        raw_ptr, [](float* ptr) { simd_utils::aligned_free(ptr); });
}

// FeatureProcessor Implementation
FeatureProcessor::FeatureProcessor(const FeatureProcessingConfig& config) 
    : config_(config) {
}

bool FeatureProcessor::initialize() {
    if (initialized_) {
        return true; // Already initialized
    }
    
    // Detect SIMD capabilities at runtime
    detect_simd_capabilities();
    
    // Pre-allocate aligned buffers for zero-allocation hot path
    const size_t buffer_size = config_.expected_feature_count;
    
    try {
        temp_buffer1_ = std::make_unique<AlignedFeatureVector>(buffer_size);
        temp_buffer2_ = std::make_unique<AlignedFeatureVector>(buffer_size);
        mean_buffer_ = std::make_unique<AlignedFeatureVector>(buffer_size);
        std_buffer_ = std::make_unique<AlignedFeatureVector>(buffer_size);
    } catch (const std::bad_alloc&) {
        return false; // Memory allocation failed
    }
    
    // Initialize normalization parameters (in production, load from model metadata)
    // For now, use reasonable defaults
    for (size_t i = 0; i < buffer_size; ++i) {
        (*mean_buffer_)[i] = 0.0f; // Zero mean
        (*std_buffer_)[i] = 1.0f;  // Unit variance
    }
    
    initialized_ = true;
    return true;
}

bool FeatureProcessor::process_features(const std::vector<float>& raw_features,
                                       AlignedFeatureVector& processed_features,
                                       FeatureProcessingResult& result) {
    if (!initialized_) {
        return false;
    }
    
    if (!validate_features(raw_features)) {
        return false;
    }
    
    auto start_time = std::chrono::high_resolution_clock::now();
    uint32_t simd_ops = 0;
    
    // Ensure output vector is properly sized
    if (processed_features.size() != raw_features.size()) {
        return false; // Size mismatch
    }
    
    // Copy input features to aligned output buffer
    const size_t feature_count = raw_features.size();
    std::memcpy(processed_features.data(), raw_features.data(), 
                feature_count * sizeof(float));
    
    // Stage 1: Normalization using SIMD
    if (config_.enable_simd && simd_caps_.has_avx2) {
        normalize_avx2(processed_features.data(), mean_buffer_->data(), 
                      std_buffer_->data(), feature_count);
        simd_ops += (feature_count + 7) / 8; // AVX2 processes 8 floats per op
    } else if (config_.enable_simd && simd_caps_.has_sse42) {
        normalize_sse42(processed_features.data(), mean_buffer_->data(), 
                       std_buffer_->data(), feature_count);
        simd_ops += (feature_count + 3) / 4; // SSE processes 4 floats per op
    } else {
        normalize_scalar(processed_features.data(), mean_buffer_->data(), 
                        std_buffer_->data(), feature_count);
    }
    
    // Stage 2: Log transformation for certain features (if enabled)
    if (config_.enable_fast_math) {
        if (config_.enable_simd && simd_caps_.has_avx2) {
            log_transform_avx2(processed_features.data(), feature_count, 
                              static_cast<float>(config_.normalization_epsilon));
            simd_ops += (feature_count + 7) / 8;
        }
    }
    
    auto end_time = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
        end_time - start_time);
    
    // Update processing result
    result.processing_time_ns = duration.count();
    result.features_processed = static_cast<uint32_t>(feature_count);
    result.simd_operations = simd_ops;
    result.cache_hit = false; // For now, implement caching later if needed
    result.vectorization_ratio = static_cast<double>(simd_ops * 8) / feature_count;
    
    // Update internal statistics
    total_processing_time_ns_ += duration.count();
    total_operations_++;
    
    return true;
}

bool FeatureProcessor::process_features_batch(
    const std::vector<std::vector<float>>& batch_features,
    std::vector<AlignedFeatureVector>& batch_processed,
    std::vector<FeatureProcessingResult>& results) {
    
    if (batch_features.empty()) {
        return false;
    }
    
    batch_processed.clear();
    results.clear();
    batch_processed.reserve(batch_features.size());
    results.reserve(batch_features.size());
    
    // Process each feature vector in batch
    for (const auto& features : batch_features) {
        batch_processed.emplace_back(features.size());
        results.emplace_back();
        
        if (!process_features(features, batch_processed.back(), results.back())) {
            return false;
        }
    }
    
    return true;
}

bool FeatureProcessor::normalize_features_simd(AlignedFeatureVector& features,
                                              const AlignedFeatureVector& mean,
                                              const AlignedFeatureVector& std_dev) {
    if (!initialized_ || features.size() != mean.size() || mean.size() != std_dev.size()) {
        return false;
    }
    
    const size_t count = features.size();
    
    if (config_.enable_simd && simd_caps_.has_avx2) {
        normalize_avx2(features.data(), mean.data(), std_dev.data(), count);
    } else if (config_.enable_simd && simd_caps_.has_sse42) {
        normalize_sse42(features.data(), mean.data(), std_dev.data(), count);
    } else {
        normalize_scalar(features.data(), mean.data(), std_dev.data(), count);
    }
    
    return true;
}

bool FeatureProcessor::log_transform_simd(AlignedFeatureVector& features, float epsilon) {
    if (!initialized_) {
        return false;
    }
    
    const size_t count = features.size();
    
    if (config_.enable_simd && simd_caps_.has_avx2) {
        log_transform_avx2(features.data(), count, epsilon);
    } else {
        // Scalar fallback
        for (size_t i = 0; i < count; ++i) {
            features[i] = std::log(std::max(features[i], epsilon));
        }
    }
    
    return true;
}

bool FeatureProcessor::compute_statistics_simd(const AlignedFeatureVector& features,
                                              float& mean, float& variance,
                                              float& min_val, float& max_val) {
    if (!initialized_ || features.size() == 0) {
        return false;
    }
    
    const size_t count = features.size();
    
    if (config_.enable_simd && simd_caps_.has_avx2) {
        compute_stats_avx2(features.data(), count, mean, variance, min_val, max_val);
    } else {
        // Scalar fallback
        const float* data = features.data();
        min_val = max_val = data[0];
        double sum = 0.0, sum_sq = 0.0;
        
        for (size_t i = 0; i < count; ++i) {
            const float val = data[i];
            sum += val;
            sum_sq += val * val;
            min_val = std::min(min_val, val);
            max_val = std::max(max_val, val);
        }
        
        mean = static_cast<float>(sum / count);
        variance = static_cast<float>((sum_sq / count) - (mean * mean));
    }
    
    return true;
}

bool FeatureProcessor::warmup(uint32_t num_warmup_calls) {
    if (!initialized_) {
        return false;
    }
    
    // Create dummy feature vector for warmup
    std::vector<float> dummy_features(config_.expected_feature_count, 0.5f);
    AlignedFeatureVector dummy_output(dummy_features.size());
    FeatureProcessingResult dummy_result;
    
    // Perform warmup processing to optimize CPU cache
    for (uint32_t i = 0; i < num_warmup_calls; ++i) {
        if (!process_features(dummy_features, dummy_output, dummy_result)) {
            return false;
        }
    }
    
    // Reset statistics after warmup
    total_processing_time_ns_ = 0;
    total_operations_ = 0;
    
    return true;
}

void FeatureProcessor::detect_simd_capabilities() {
    // Initialize capabilities structure
    simd_caps_ = {};
    
    // Check for SSE 4.2 support
    simd_caps_.has_sse42 = check_cpuid_feature(1, 0, 2, 20);
    
    // Check for AVX2 support
    simd_caps_.has_avx2 = check_cpuid_feature(7, 0, 1, 5);
    
    // Check for FMA support
    simd_caps_.has_fma = check_cpuid_feature(1, 0, 2, 12);
    
    // Check for AVX-512 support (AVX-512F)
    simd_caps_.has_avx512 = check_cpuid_feature(7, 0, 1, 16);
    
    // Get cache line size (typically 64 bytes on modern x86)
    simd_caps_.cache_line_size = 64;
}

bool FeatureProcessor::validate_features(const std::vector<float>& features) const {
    if (features.empty() || features.size() > 10000) {
        return false; // Reasonable size bounds
    }
    
    // Quick validation for invalid values
    for (float f : features) {
        if (std::isnan(f) || std::isinf(f)) {
            return false;
        }
    }
    
    return true;
}

// SIMD Implementation Methods

void FeatureProcessor::normalize_avx2(float* features, const float* mean, 
                                     const float* std_dev, size_t count) {
    const size_t simd_count = count & ~7u; // Round down to multiple of 8
    
    // Process 8 floats per iteration using AVX2
    for (size_t i = 0; i < simd_count; i += 8) {
        __m256 f = _mm256_load_ps(&features[i]);
        __m256 m = _mm256_load_ps(&mean[i]);
        __m256 s = _mm256_load_ps(&std_dev[i]);
        
        // Compute (features - mean) / std_dev
        f = _mm256_sub_ps(f, m);
        f = _mm256_div_ps(f, s);
        
        _mm256_store_ps(&features[i], f);
    }
    
    // Handle remaining elements with scalar code
    for (size_t i = simd_count; i < count; ++i) {
        features[i] = (features[i] - mean[i]) / std_dev[i];
    }
}

void FeatureProcessor::normalize_avx512(float* features, const float* mean, 
                                       const float* std_dev, size_t count) {
    // AVX-512 implementation (16 floats per operation)
    const size_t simd_count = count & ~15u; // Round down to multiple of 16
    
    for (size_t i = 0; i < simd_count; i += 16) {
        __m512 f = _mm512_load_ps(&features[i]);
        __m512 m = _mm512_load_ps(&mean[i]);
        __m512 s = _mm512_load_ps(&std_dev[i]);
        
        // Compute (features - mean) / std_dev
        f = _mm512_sub_ps(f, m);
        f = _mm512_div_ps(f, s);
        
        _mm512_store_ps(&features[i], f);
    }
    
    // Handle remaining elements
    for (size_t i = simd_count; i < count; ++i) {
        features[i] = (features[i] - mean[i]) / std_dev[i];
    }
}

void FeatureProcessor::normalize_sse42(float* features, const float* mean, 
                                      const float* std_dev, size_t count) {
    const size_t simd_count = count & ~3u; // Round down to multiple of 4
    
    // Process 4 floats per iteration using SSE
    for (size_t i = 0; i < simd_count; i += 4) {
        __m128 f = _mm_load_ps(&features[i]);
        __m128 m = _mm_load_ps(&mean[i]);
        __m128 s = _mm_load_ps(&std_dev[i]);
        
        // Compute (features - mean) / std_dev
        f = _mm_sub_ps(f, m);
        f = _mm_div_ps(f, s);
        
        _mm_store_ps(&features[i], f);
    }
    
    // Handle remaining elements
    for (size_t i = simd_count; i < count; ++i) {
        features[i] = (features[i] - mean[i]) / std_dev[i];
    }
}

void FeatureProcessor::normalize_scalar(float* features, const float* mean, 
                                       const float* std_dev, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        features[i] = (features[i] - mean[i]) / std_dev[i];
    }
}

void FeatureProcessor::log_transform_avx2(float* features, size_t count, float epsilon) {
    const __m256 eps_vec = _mm256_set1_ps(epsilon);
    const size_t simd_count = count & ~7u;
    
    for (size_t i = 0; i < simd_count; i += 8) {
        __m256 f = _mm256_load_ps(&features[i]);
        
        // Clamp to minimum epsilon value
        f = _mm256_max_ps(f, eps_vec);
        
        // Fast log approximation using bit manipulation
        // This is simplified - in production, use more accurate approximation
        for (int j = 0; j < 8; ++j) {
            features[i + j] = simd_utils::fast_log(features[i + j]);
        }
    }
    
    // Handle remaining elements
    for (size_t i = simd_count; i < count; ++i) {
        features[i] = simd_utils::fast_log(std::max(features[i], epsilon));
    }
}

void FeatureProcessor::compute_stats_avx2(const float* features, size_t count,
                                         float& mean, float& variance, 
                                         float& min_val, float& max_val) {
    if (count == 0) return;
    
    __m256 sum_vec = _mm256_setzero_ps();
    __m256 sum_sq_vec = _mm256_setzero_ps();
    __m256 min_vec = _mm256_set1_ps(features[0]);
    __m256 max_vec = _mm256_set1_ps(features[0]);
    
    const size_t simd_count = count & ~7u;
    
    // SIMD accumulation
    for (size_t i = 0; i < simd_count; i += 8) {
        __m256 f = _mm256_load_ps(&features[i]);
        
        sum_vec = _mm256_add_ps(sum_vec, f);
        sum_sq_vec = _mm256_fmadd_ps(f, f, sum_sq_vec); // f*f + sum_sq_vec
        min_vec = _mm256_min_ps(min_vec, f);
        max_vec = _mm256_max_ps(max_vec, f);
    }
    
    // Horizontal reduction
    float sum = simd_utils::horizontal_sum_avx2(sum_vec);
    float sum_sq = simd_utils::horizontal_sum_avx2(sum_sq_vec);
    
    // Find min/max from vectors
    alignas(32) float min_arr[8], max_arr[8];
    _mm256_store_ps(min_arr, min_vec);
    _mm256_store_ps(max_arr, max_vec);
    
    min_val = *std::min_element(min_arr, min_arr + 8);
    max_val = *std::max_element(max_arr, max_arr + 8);
    
    // Handle remaining elements
    for (size_t i = simd_count; i < count; ++i) {
        const float val = features[i];
        sum += val;
        sum_sq += val * val;
        min_val = std::min(min_val, val);
        max_val = std::max(max_val, val);
    }
    
    mean = sum / count;
    variance = (sum_sq / count) - (mean * mean);
}

// SIMD utility functions implementation
namespace simd_utils {
    void* aligned_alloc(size_t size, size_t alignment) {
        void* ptr = nullptr;
        
#if defined(_WIN32)
        ptr = _aligned_malloc(size, alignment);
#elif defined(__ANDROID__) || defined(ANDROID)
        ptr = memalign(alignment, size);
#else
        if (posix_memalign(&ptr, alignment, size) != 0) {
            ptr = nullptr;
        }
#endif
        
        return ptr;
    }
    
    void aligned_free(void* ptr) {
        if (!ptr) return;
        
#if defined(_WIN32)
        _aligned_free(ptr);
#else
        free(ptr);
#endif
    }
    
    float fast_log(float x) {
        // Fast log approximation using bit manipulation
        // Based on the IEEE 754 representation
        union { float f; uint32_t i; } u;
        u.f = x;
        
        // Extract exponent and mantissa
        const uint32_t exp = (u.i >> 23) & 0xFF;
        const uint32_t mantissa = u.i & 0x7FFFFF;
        
        // Approximate log using bit manipulation
        // This is a simplified approximation - use more accurate methods in production
        return static_cast<float>((exp - 127) * 0.693147f + 
                                 (mantissa / 8388608.0f - 1.0f) * 0.693147f);
    }
}

} // namespace inference
} // namespace stream_sentinel
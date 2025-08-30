/**
 * @file memory_pool.hpp
 * @brief High-performance memory management for zero-allocation inference
 * 
 * This module implements custom memory pools and allocators optimized for
 * machine learning inference workloads, providing zero-allocation hot paths
 * and cache-friendly memory layouts for maximum performance.
 * 
 * Key Features:
 * - Zero-allocation hot path for inference operations
 * - Cache-aligned memory allocation (64-byte boundaries)
 * - Thread-safe concurrent allocation with lock-free operations
 * - Memory pool recycling to eliminate fragmentation
 * - NUMA-aware allocation for multi-socket systems
 * - Comprehensive memory usage tracking and optimization
 * 
 * Performance Targets:
 * - Sub-microsecond allocation/deallocation
 * - Zero memory allocations during inference hot path
 * - <1% memory overhead vs direct allocation
 * - 99%+ memory pool hit rate
 * 
 * @author Stream-Sentinel Team
 * @version 2.0.0
 */

#pragma once

#include <memory>
#include <vector>
#include <atomic>
#include <mutex>
#include <cstdint>
#include <cstdlib>
#include <new>
#include <cassert>

namespace stream_sentinel {
namespace inference {

/**
 * @brief Memory pool configuration parameters
 */
struct MemoryPoolConfig {
    size_t initial_pool_size_mb = 64;      ///< Initial pool size in MB
    size_t max_pool_size_mb = 256;         ///< Maximum pool size in MB
    size_t block_alignment = 64;           ///< Memory alignment (cache line size)
    uint32_t num_size_classes = 16;        ///< Number of different block sizes
    bool enable_numa_awareness = true;     ///< Enable NUMA-aware allocation
    bool enable_prefaulting = true;        ///< Pre-fault pages for consistent latency
    double growth_factor = 1.5;            ///< Pool growth factor when expanding
    uint32_t max_cached_blocks = 1024;     ///< Maximum blocks cached per size class
};

/**
 * @brief Memory allocation statistics
 */
struct MemoryStats {
    std::atomic<uint64_t> total_allocations{0};    ///< Total allocation count
    std::atomic<uint64_t> total_deallocations{0};  ///< Total deallocation count
    std::atomic<uint64_t> cache_hits{0};           ///< Pool cache hit count
    std::atomic<uint64_t> cache_misses{0};         ///< Pool cache miss count
    std::atomic<uint64_t> bytes_allocated{0};      ///< Total bytes allocated
    std::atomic<uint64_t> bytes_peak{0};           ///< Peak memory usage
    std::atomic<uint64_t> allocation_time_ns{0};   ///< Total allocation time
    
    // Current state
    size_t current_pool_size_mb = 0;               ///< Current pool size
    size_t available_memory_mb = 0;                ///< Available memory in pool
    double fragmentation_ratio = 0.0;              ///< Memory fragmentation ratio
    uint32_t active_blocks = 0;                    ///< Currently allocated blocks
};

/**
 * @brief Memory block header for tracking and validation
 */
struct alignas(64) BlockHeader {
    uint32_t magic_number;          ///< Magic number for corruption detection
    uint32_t size_class;            ///< Size class index
    uint64_t allocation_time;       ///< Allocation timestamp
    uint64_t thread_id;             ///< Allocating thread ID
    void* pool_ptr;                 ///< Pointer back to owning pool
    
    static constexpr uint32_t MAGIC = 0xDEADBEEF;
    
    /**
     * @brief Validate block header integrity
     * @return true if header is valid
     */
    bool is_valid() const {
        return magic_number == MAGIC;
    }
};

/**
 * @brief Fixed-size block allocator for specific size classes
 */
class FixedSizeAllocator {
public:
    /**
     * @brief Construct fixed-size allocator
     * @param block_size Size of blocks to allocate
     * @param initial_blocks Initial number of blocks to pre-allocate
     * @param alignment Memory alignment requirement
     */
    FixedSizeAllocator(size_t block_size, size_t initial_blocks, size_t alignment = 64);
    
    /**
     * @brief Destructor
     */
    ~FixedSizeAllocator();
    
    // Disable copy construction and assignment
    FixedSizeAllocator(const FixedSizeAllocator&) = delete;
    FixedSizeAllocator& operator=(const FixedSizeAllocator&) = delete;
    
    /**
     * @brief Allocate a block from this allocator
     * @return Pointer to allocated block or nullptr if failed
     */
    void* allocate();
    
    /**
     * @brief Deallocate a block back to this allocator
     * @param ptr Pointer to block to deallocate
     * @return true if deallocation successful
     */
    bool deallocate(void* ptr);
    
    /**
     * @brief Get block size for this allocator
     * @return Block size in bytes
     */
    size_t get_block_size() const { return block_size_; }
    
    /**
     * @brief Get number of available blocks
     * @return Number of free blocks
     */
    size_t get_available_blocks() const;
    
    /**
     * @brief Expand the allocator with more blocks
     * @param additional_blocks Number of additional blocks to add
     * @return true if expansion successful
     */
    bool expand(size_t additional_blocks);

private:
    const size_t block_size_;
    const size_t alignment_;
    
    // Lock-free free list using atomic pointers
    struct FreeBlock {
        std::atomic<FreeBlock*> next;
    };
    
    std::atomic<FreeBlock*> free_list_head_{nullptr};
    std::vector<std::unique_ptr<uint8_t[]>> memory_chunks_;
    std::mutex expansion_mutex_;
    
    std::atomic<size_t> total_blocks_{0};
    std::atomic<size_t> allocated_blocks_{0};
    
    /**
     * @brief Allocate a new memory chunk
     * @param num_blocks Number of blocks in chunk
     * @return Pointer to chunk or nullptr if failed
     */
    uint8_t* allocate_chunk(size_t num_blocks);
    
    /**
     * @brief Initialize free list for a new chunk
     * @param chunk Pointer to memory chunk
     * @param num_blocks Number of blocks in chunk
     */
    void initialize_free_list(uint8_t* chunk, size_t num_blocks);
};

/**
 * @brief High-performance memory pool for ML inference workloads
 * 
 * This class provides a thread-safe, high-performance memory allocation
 * system optimized for the specific patterns of machine learning inference:
 * - Frequent allocation/deallocation of similar-sized objects
 * - Predictable allocation patterns
 * - Need for cache-aligned memory
 * - Zero-allocation hot paths
 */
class MemoryPool {
public:
    /**
     * @brief Construct memory pool with configuration
     * @param config Memory pool configuration
     */
    explicit MemoryPool(const MemoryPoolConfig& config);
    
    /**
     * @brief Destructor with resource cleanup
     */
    ~MemoryPool();
    
    // Disable copy construction and assignment
    MemoryPool(const MemoryPool&) = delete;
    MemoryPool& operator=(const MemoryPool&) = delete;
    
    /**
     * @brief Initialize the memory pool
     * @return true if initialization successful
     */
    bool initialize();
    
    /**
     * @brief Allocate memory from the pool
     * @param size Size in bytes to allocate
     * @param alignment Alignment requirement (default: cache line aligned)
     * @return Pointer to allocated memory or nullptr if failed
     * 
     * @note This is optimized for hot path performance
     */
    void* allocate(size_t size, size_t alignment = 64);
    
    /**
     * @brief Deallocate memory back to the pool
     * @param ptr Pointer to memory to deallocate
     * @return true if deallocation successful
     */
    bool deallocate(void* ptr);
    
    /**
     * @brief Allocate typed object with construction
     * @tparam T Type to allocate
     * @tparam Args Constructor argument types
     * @param args Constructor arguments
     * @return Pointer to constructed object or nullptr if failed
     */
    template<typename T, typename... Args>
    T* allocate_object(Args&&... args) {
        static_assert(std::is_trivially_destructible_v<T> || 
                      std::has_virtual_destructor_v<T>,
                      "Type must be trivially destructible or have virtual destructor");
        
        void* ptr = allocate(sizeof(T), alignof(T));
        if (!ptr) return nullptr;
        
        try {
            return new(ptr) T(std::forward<Args>(args)...);
        } catch (...) {
            deallocate(ptr);
            return nullptr;
        }
    }
    
    /**
     * @brief Deallocate typed object with destruction
     * @tparam T Type to deallocate
     * @param obj Pointer to object to deallocate
     * @return true if deallocation successful
     */
    template<typename T>
    bool deallocate_object(T* obj) {
        if (!obj) return false;
        
        if constexpr (!std::is_trivially_destructible_v<T>) {
            obj->~T();
        }
        
        return deallocate(obj);
    }
    
    /**
     * @brief Pre-allocate memory blocks for zero-allocation hot path
     * @param size_distribution Vector of (size, count) pairs
     * @return true if pre-allocation successful
     */
    bool preallocate_blocks(const std::vector<std::pair<size_t, size_t>>& size_distribution);
    
    /**
     * @brief Get current memory statistics
     * @return Memory usage statistics
     */
    const MemoryStats& get_stats() const;
    
    /**
     * @brief Reset memory statistics
     */
    void reset_stats();
    
    /**
     * @brief Compact memory pool to reduce fragmentation
     * @return true if compaction successful
     */
    bool compact();
    
    /**
     * @brief Get memory pool configuration
     * @return Current configuration
     */
    const MemoryPoolConfig& get_config() const { return config_; }

private:
    MemoryPoolConfig config_;
    bool initialized_ = false;
    
    // Size class allocators for different block sizes
    std::vector<std::unique_ptr<FixedSizeAllocator>> size_class_allocators_;
    std::vector<size_t> size_class_sizes_;
    
    // Statistics
    mutable MemoryStats stats_;
    mutable std::mutex stats_mutex_;
    
    /**
     * @brief Get size class index for requested size
     * @param size Requested size in bytes
     * @return Size class index
     */
    uint32_t get_size_class(size_t size) const;
    
    /**
     * @brief Initialize size class allocators
     * @return true if initialization successful
     */
    bool initialize_size_classes();
    
    /**
     * @brief Update allocation statistics
     * @param size Allocated size
     * @param allocation_time_ns Time taken for allocation
     * @param cache_hit Whether allocation was served from cache
     */
    void update_allocation_stats(size_t size, uint64_t allocation_time_ns, bool cache_hit);
    
    /**
     * @brief Update deallocation statistics
     * @param size Deallocated size
     */
    void update_deallocation_stats(size_t size);
};

/**
 * @brief Custom allocator that uses MemoryPool
 * @tparam T Type to allocate
 */
template<typename T>
class PoolAllocator {
public:
    using value_type = T;
    using pointer = T*;
    using const_pointer = const T*;
    using reference = T&;
    using const_reference = const T&;
    using size_type = std::size_t;
    using difference_type = std::ptrdiff_t;
    
    template<typename U>
    struct rebind {
        using other = PoolAllocator<U>;
    };
    
    /**
     * @brief Construct pool allocator
     * @param pool Memory pool to use for allocation
     */
    explicit PoolAllocator(MemoryPool& pool) : pool_(&pool) {}
    
    /**
     * @brief Copy constructor
     */
    template<typename U>
    PoolAllocator(const PoolAllocator<U>& other) : pool_(other.pool_) {}
    
    /**
     * @brief Allocate memory for n objects
     * @param n Number of objects to allocate
     * @return Pointer to allocated memory
     */
    pointer allocate(size_type n) {
        if (n == 0) return nullptr;
        
        void* ptr = pool_->allocate(n * sizeof(T), alignof(T));
        if (!ptr) throw std::bad_alloc();
        
        return static_cast<pointer>(ptr);
    }
    
    /**
     * @brief Deallocate memory
     * @param ptr Pointer to memory to deallocate
     * @param n Number of objects (unused)
     */
    void deallocate(pointer ptr, size_type n = 0) {
        if (ptr) {
            pool_->deallocate(ptr);
        }
    }
    
    /**
     * @brief Equality comparison
     */
    template<typename U>
    bool operator==(const PoolAllocator<U>& other) const {
        return pool_ == other.pool_;
    }
    
    /**
     * @brief Inequality comparison
     */
    template<typename U>
    bool operator!=(const PoolAllocator<U>& other) const {
        return !(*this == other);
    }

private:
    MemoryPool* pool_;
    
    template<typename U>
    friend class PoolAllocator;
};

/**
 * @brief Utility functions for memory management
 */
namespace memory_utils {
    /**
     * @brief Get system page size
     * @return System page size in bytes
     */
    size_t get_page_size();
    
    /**
     * @brief Get CPU cache line size
     * @return Cache line size in bytes
     */
    size_t get_cache_line_size();
    
    /**
     * @brief Check if address is properly aligned
     * @param ptr Pointer to check
     * @param alignment Required alignment
     * @return true if properly aligned
     */
    inline bool is_aligned(const void* ptr, size_t alignment) {
        return (reinterpret_cast<uintptr_t>(ptr) & (alignment - 1)) == 0;
    }
    
    /**
     * @brief Prefault memory pages to avoid page faults during hot path
     * @param ptr Pointer to memory region
     * @param size Size of memory region
     * @return true if prefaulting successful
     */
    bool prefault_memory(void* ptr, size_t size);
    
    /**
     * @brief Get NUMA node for current thread
     * @return NUMA node ID or -1 if NUMA not available
     */
    int get_numa_node();
    
    /**
     * @brief Allocate memory on specific NUMA node
     * @param size Size to allocate
     * @param numa_node NUMA node ID
     * @return Pointer to allocated memory or nullptr
     */
    void* numa_alloc(size_t size, int numa_node);
    
    /**
     * @brief Free NUMA-allocated memory
     * @param ptr Pointer to free
     * @param size Size of allocation
     */
    void numa_free(void* ptr, size_t size);
}

} // namespace inference
} // namespace stream_sentinel
/**
 * @file memory_pool.cpp
 * @brief Implementation of high-performance memory pool for zero-allocation inference
 * 
 * This file implements custom memory pools optimized for ML inference workloads.
 * 
 * @author Stream-Sentinel Team
 * @version 2.0.0
 */

#include "memory_pool.hpp"
#include <algorithm>
#include <cstring>
#include <thread>
#include <chrono>

#if defined(__linux__)
#include <sys/mman.h>
#include <numa.h>
#include <unistd.h>
#elif defined(_WIN32)
#include <windows.h>
#include <memoryapi.h>
#elif defined(__APPLE__)
#include <sys/mman.h>
#include <unistd.h>
#endif

namespace stream_sentinel {
namespace inference {

namespace {
    /**
     * @brief Get current thread ID for tracking
     */
    uint64_t get_thread_id() {
        std::hash<std::thread::id> hasher;
        return hasher(std::this_thread::get_id());
    }
    
    /**
     * @brief Get high-resolution timestamp
     */
    uint64_t get_timestamp_ns() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::high_resolution_clock::now().time_since_epoch()
        ).count();
    }
}

// FixedSizeAllocator Implementation
FixedSizeAllocator::FixedSizeAllocator(size_t block_size, size_t initial_blocks, size_t alignment)
    : block_size_(block_size), alignment_(alignment) {
    
    // Ensure block size is at least as large as a pointer for free list
    block_size_ = std::max(block_size_, sizeof(void*));
    
    // Align block size to alignment boundary
    block_size_ = memory_utils::is_aligned(reinterpret_cast<void*>(block_size_), alignment_) ?
                  block_size_ : ((block_size_ + alignment_ - 1) & ~(alignment_ - 1));
    
    // Initialize with initial blocks
    if (initial_blocks > 0) {
        expand(initial_blocks);
    }
}

FixedSizeAllocator::~FixedSizeAllocator() {
    // Memory chunks are automatically freed by unique_ptr destructors
}

void* FixedSizeAllocator::allocate() {
    // Attempt to pop from free list (lock-free)
    FreeBlock* current_head = free_list_head_.load(std::memory_order_acquire);
    
    while (current_head != nullptr) {
        FreeBlock* next = current_head->next.load(std::memory_order_relaxed);
        
        // Try to update head to next
        if (free_list_head_.compare_exchange_weak(
                current_head, next, 
                std::memory_order_release, std::memory_order_acquire)) {
            
            allocated_blocks_.fetch_add(1, std::memory_order_relaxed);
            return current_head;
        }
        // CAS failed, current_head was updated, try again
    }
    
    // Free list empty, need to expand
    {
        std::lock_guard<std::mutex> lock(expansion_mutex_);
        
        // Double-check after acquiring lock
        current_head = free_list_head_.load(std::memory_order_acquire);
        if (current_head != nullptr) {
            // Someone else expanded, try again
            return allocate();
        }
        
        // Expand with more blocks
        const size_t expansion_size = std::max(size_t(64), total_blocks_.load() / 4);
        if (!expand(expansion_size)) {
            return nullptr; // Expansion failed
        }
    }
    
    // Try allocation again after expansion
    return allocate();
}

bool FixedSizeAllocator::deallocate(void* ptr) {
    if (!ptr) return true;
    
    // Create new free block
    FreeBlock* block = static_cast<FreeBlock*>(ptr);
    
    // Push onto free list (lock-free)
    FreeBlock* current_head = free_list_head_.load(std::memory_order_relaxed);
    do {
        block->next.store(current_head, std::memory_order_relaxed);
    } while (!free_list_head_.compare_exchange_weak(
        current_head, block,
        std::memory_order_release, std::memory_order_relaxed));
    
    allocated_blocks_.fetch_sub(1, std::memory_order_relaxed);
    return true;
}

size_t FixedSizeAllocator::get_available_blocks() const {
    return total_blocks_.load() - allocated_blocks_.load();
}

bool FixedSizeAllocator::expand(size_t additional_blocks) {
    if (additional_blocks == 0) return true;
    
    // Allocate new chunk
    uint8_t* chunk = allocate_chunk(additional_blocks);
    if (!chunk) return false;
    
    // Initialize free list for new chunk
    initialize_free_list(chunk, additional_blocks);
    
    total_blocks_.fetch_add(additional_blocks, std::memory_order_release);
    return true;
}

uint8_t* FixedSizeAllocator::allocate_chunk(size_t num_blocks) {
    const size_t chunk_size = num_blocks * block_size_;
    
    uint8_t* chunk = nullptr;
    
#if defined(__linux__) || defined(__APPLE__)
    // Use mmap for large allocations to avoid fragmentation
    if (chunk_size >= 64 * 1024) {
        chunk = static_cast<uint8_t*>(mmap(nullptr, chunk_size, 
                                          PROT_READ | PROT_WRITE,
                                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
        if (chunk == MAP_FAILED) {
            chunk = nullptr;
        }
    }
#endif
    
    if (!chunk) {
        // Fallback to aligned allocation
        chunk = static_cast<uint8_t*>(memory_utils::aligned_alloc(chunk_size, alignment_));
    }
    
    if (!chunk) return nullptr;
    
    // Store chunk for cleanup
    memory_chunks_.emplace_back(chunk, [chunk_size](uint8_t* ptr) {
        // Custom deleter that handles both mmap and aligned_alloc
#if defined(__linux__) || defined(__APPLE__)
        if (chunk_size >= 64 * 1024) {
            munmap(ptr, chunk_size);
        } else {
            memory_utils::aligned_free(ptr);
        }
#else
        memory_utils::aligned_free(ptr);
#endif
    });
    
    // Prefault memory to avoid page faults during hot path
    if (chunk_size >= 4096) {
        memory_utils::prefault_memory(chunk, chunk_size);
    }
    
    return chunk;
}

void FixedSizeAllocator::initialize_free_list(uint8_t* chunk, size_t num_blocks) {
    // Thread all blocks together in free list
    for (size_t i = 0; i < num_blocks; ++i) {
        FreeBlock* block = reinterpret_cast<FreeBlock*>(chunk + i * block_size_);
        
        if (i == num_blocks - 1) {
            // Last block points to current head
            FreeBlock* current_head = free_list_head_.load(std::memory_order_relaxed);
            block->next.store(current_head, std::memory_order_relaxed);
            
            // Update head to first block of chunk
            FreeBlock* first_block = reinterpret_cast<FreeBlock*>(chunk);
            free_list_head_.store(first_block, std::memory_order_release);
        } else {
            // Point to next block in chunk
            FreeBlock* next_block = reinterpret_cast<FreeBlock*>(chunk + (i + 1) * block_size_);
            block->next.store(next_block, std::memory_order_relaxed);
        }
    }
}

// MemoryPool Implementation
MemoryPool::MemoryPool(const MemoryPoolConfig& config) : config_(config) {}

MemoryPool::~MemoryPool() {
    // Allocators are automatically cleaned up by unique_ptr destructors
}

bool MemoryPool::initialize() {
    if (initialized_) return true;
    
    // Initialize size class allocators
    if (!initialize_size_classes()) {
        return false;
    }
    
    // Initialize statistics
    stats_ = MemoryStats{};
    
    initialized_ = true;
    return true;
}

void* MemoryPool::allocate(size_t size, size_t alignment) {
    if (!initialized_ || size == 0) return nullptr;
    
    auto start_time = get_timestamp_ns();
    
    // Get appropriate size class
    uint32_t size_class = get_size_class(size);
    
    // Allocate from size class allocator
    void* ptr = size_class_allocators_[size_class]->allocate();
    
    if (ptr) {
        // Add block header for tracking
        BlockHeader* header = static_cast<BlockHeader*>(ptr);
        header->magic_number = BlockHeader::MAGIC;
        header->size_class = size_class;
        header->allocation_time = get_timestamp_ns();
        header->thread_id = get_thread_id();
        header->pool_ptr = this;
        
        // Return pointer past header
        ptr = static_cast<uint8_t*>(ptr) + sizeof(BlockHeader);
        
        // Update statistics
        auto allocation_time = get_timestamp_ns() - start_time;
        update_allocation_stats(size, allocation_time, true);
    }
    
    return ptr;
}

bool MemoryPool::deallocate(void* ptr) {
    if (!ptr) return true;
    
    // Get block header
    BlockHeader* header = reinterpret_cast<BlockHeader*>(
        static_cast<uint8_t*>(ptr) - sizeof(BlockHeader));
    
    // Validate header
    if (!header->is_valid() || header->pool_ptr != this) {
        return false; // Invalid block
    }
    
    uint32_t size_class = header->size_class;
    if (size_class >= size_class_allocators_.size()) {
        return false; // Invalid size class
    }
    
    // Clear header for security
    std::memset(header, 0, sizeof(BlockHeader));
    
    // Return to size class allocator
    bool success = size_class_allocators_[size_class]->deallocate(header);
    
    if (success) {
        update_deallocation_stats(size_class_sizes_[size_class]);
    }
    
    return success;
}

bool MemoryPool::preallocate_blocks(const std::vector<std::pair<size_t, size_t>>& size_distribution) {
    if (!initialized_) return false;
    
    // Preallocate blocks based on expected usage pattern
    for (const auto& [size, count] : size_distribution) {
        uint32_t size_class = get_size_class(size);
        
        // Expand allocator to have enough blocks
        size_t current_available = size_class_allocators_[size_class]->get_available_blocks();
        if (current_available < count) {
            if (!size_class_allocators_[size_class]->expand(count - current_available)) {
                return false;
            }
        }
    }
    
    return true;
}

const MemoryStats& MemoryPool::get_stats() const {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    return stats_;
}

void MemoryPool::reset_stats() {
    std::lock_guard<std::mutex> lock(stats_mutex_);
    stats_ = MemoryStats{};
}

bool MemoryPool::compact() {
    // Memory compaction is complex with concurrent access
    // For now, just return success (implement full compaction later if needed)
    return true;
}

uint32_t MemoryPool::get_size_class(size_t size) const {
    // Add header size to requested size
    size += sizeof(BlockHeader);
    
    // Find appropriate size class (powers of 2 progression)
    for (uint32_t i = 0; i < size_class_sizes_.size(); ++i) {
        if (size <= size_class_sizes_[i]) {
            return i;
        }
    }
    
    // Return largest size class if size is too big
    return static_cast<uint32_t>(size_class_sizes_.size() - 1);
}

bool MemoryPool::initialize_size_classes() {
    const uint32_t num_classes = config_.num_size_classes;
    
    // Create size classes in powers of 2 progression
    size_class_sizes_.reserve(num_classes);
    size_class_allocators_.reserve(num_classes);
    
    size_t current_size = 64; // Start with 64 bytes
    
    for (uint32_t i = 0; i < num_classes; ++i) {
        size_class_sizes_.push_back(current_size);
        
        // Calculate initial blocks for this size class
        const size_t initial_blocks = std::max(size_t(16), 
                                              (config_.initial_pool_size_mb * 1024 * 1024) / (current_size * num_classes));
        
        // Create allocator for this size class
        auto allocator = std::make_unique<FixedSizeAllocator>(
            current_size, initial_blocks, config_.block_alignment);
        
        if (!allocator) return false;
        
        size_class_allocators_.push_back(std::move(allocator));
        
        // Increase size for next class (roughly 2x growth)
        current_size = static_cast<size_t>(current_size * config_.growth_factor);
        
        // Cap maximum size class at 64KB
        if (current_size > 64 * 1024) {
            current_size = 64 * 1024;
        }
    }
    
    return true;
}

void MemoryPool::update_allocation_stats(size_t size, uint64_t allocation_time_ns, bool cache_hit) {
    stats_.total_allocations.fetch_add(1, std::memory_order_relaxed);
    stats_.bytes_allocated.fetch_add(size, std::memory_order_relaxed);
    stats_.allocation_time_ns.fetch_add(allocation_time_ns, std::memory_order_relaxed);
    
    if (cache_hit) {
        stats_.cache_hits.fetch_add(1, std::memory_order_relaxed);
    } else {
        stats_.cache_misses.fetch_add(1, std::memory_order_relaxed);
    }
    
    // Update peak memory usage
    uint64_t current_bytes = stats_.bytes_allocated.load(std::memory_order_relaxed);
    uint64_t current_peak = stats_.bytes_peak.load(std::memory_order_relaxed);
    
    while (current_bytes > current_peak) {
        if (stats_.bytes_peak.compare_exchange_weak(current_peak, current_bytes,
                                                   std::memory_order_release,
                                                   std::memory_order_relaxed)) {
            break;
        }
    }
}

void MemoryPool::update_deallocation_stats(size_t size) {
    stats_.total_deallocations.fetch_add(1, std::memory_order_relaxed);
    stats_.bytes_allocated.fetch_sub(size, std::memory_order_relaxed);
}

// Memory utility functions implementation
namespace memory_utils {
    size_t get_page_size() {
#if defined(_WIN32)
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        return si.dwPageSize;
#else
        return static_cast<size_t>(sysconf(_SC_PAGESIZE));
#endif
    }
    
    size_t get_cache_line_size() {
        // Most modern x86/x64 processors use 64-byte cache lines
#if defined(_WIN32)
        DWORD buffer_size = 0;
        GetLogicalProcessorInformation(nullptr, &buffer_size);
        if (buffer_size > 0) {
            auto buffer = std::make_unique<uint8_t[]>(buffer_size);
            auto info = reinterpret_cast<PSYSTEM_LOGICAL_PROCESSOR_INFORMATION>(buffer.get());
            if (GetLogicalProcessorInformation(info, &buffer_size)) {
                for (size_t i = 0; i < buffer_size / sizeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION); ++i) {
                    if (info[i].Relationship == RelationCache && info[i].Cache.Level == 1) {
                        return info[i].Cache.LineSize;
                    }
                }
            }
        }
#elif defined(__linux__)
        long cache_line_size = sysconf(_SC_LEVEL1_DCACHE_LINESIZE);
        if (cache_line_size > 0) {
            return static_cast<size_t>(cache_line_size);
        }
#endif
        // Default fallback
        return 64;
    }
    
    bool prefault_memory(void* ptr, size_t size) {
        if (!ptr || size == 0) return false;
        
#if defined(__linux__)
        // Use madvise to prefault pages
        if (madvise(ptr, size, MADV_WILLNEED) == 0) {
            return true;
        }
#elif defined(_WIN32)
        // Use VirtualLock to fault in pages
        if (VirtualLock(ptr, size)) {
            VirtualUnlock(ptr, size); // Unlock immediately after faulting
            return true;
        }
#endif
        
        // Fallback: touch every page
        const size_t page_size = get_page_size();
        volatile uint8_t* pages = static_cast<volatile uint8_t*>(ptr);
        
        for (size_t offset = 0; offset < size; offset += page_size) {
            pages[offset] = pages[offset]; // Read to fault in page
        }
        
        return true;
    }
    
    int get_numa_node() {
#if defined(__linux__) && defined(NUMA_SUPPORT)
        if (numa_available() >= 0) {
            return numa_node_of_cpu(sched_getcpu());
        }
#endif
        return -1; // NUMA not available or supported
    }
    
    void* numa_alloc(size_t size, int numa_node) {
#if defined(__linux__) && defined(NUMA_SUPPORT)
        if (numa_available() >= 0 && numa_node >= 0) {
            return numa_alloc_onnode(size, numa_node);
        }
#endif
        // Fallback to regular allocation
        return aligned_alloc(size, get_cache_line_size());
    }
    
    void numa_free(void* ptr, size_t size) {
        if (!ptr) return;
        
#if defined(__linux__) && defined(NUMA_SUPPORT)
        if (numa_available() >= 0) {
            numa_free(ptr, size);
            return;
        }
#endif
        // Fallback to regular free
        aligned_free(ptr);
    }
}

} // namespace inference
} // namespace stream_sentinel
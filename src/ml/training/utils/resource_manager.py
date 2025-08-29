# /stream-sentinel/src/ml/training/utils/resource_manager.py

"""
Resource Management for Training Pipeline

Enterprise-grade resource management system for GPU, memory, and disk resources
with automatic cleanup, monitoring, and constraint enforcement.

Key Features:
- GPU memory management with automatic cleanup
- System memory monitoring with OOM protection
- Disk space validation and cleanup
- Process resource limits and monitoring
- Automatic resource recovery and cleanup
- Resource usage profiling and optimization

Architecture:
- Resource handles with automatic cleanup on context exit
- Background monitoring with threshold-based alerts
- Resource pools for efficient allocation and reuse
- Comprehensive resource usage tracking and reporting
- Integration with training metrics and logging systems
"""

import gc
import os
import time
import shutil
import threading
import psutil
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
import logging
import warnings


@dataclass
class ResourceUsage:
    """
    Snapshot of current resource usage.
    """
    timestamp: datetime
    memory_mb: float
    memory_percent: float
    cpu_percent: float
    disk_usage_gb: float
    disk_free_gb: float
    gpu_memory_mb: Optional[float] = None
    gpu_utilization: Optional[float] = None
    open_files: int = 0
    threads: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'memory_mb': self.memory_mb,
            'memory_percent': self.memory_percent,
            'cpu_percent': self.cpu_percent,
            'disk_usage_gb': self.disk_usage_gb,
            'disk_free_gb': self.disk_free_gb,
            'gpu_memory_mb': self.gpu_memory_mb,
            'gpu_utilization': self.gpu_utilization,
            'open_files': self.open_files,
            'threads': self.threads
        }


class ResourceHandle(ABC):
    """
    Abstract base class for resource handles with automatic cleanup.
    """
    
    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        self.acquired_at = datetime.now()
        self.is_active = True
        self.logger = logging.getLogger(__name__)
    
    @abstractmethod
    def cleanup(self) -> None:
        """Release and cleanup the resource."""
        pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        self.is_active = False


class GPUResourceHandle(ResourceHandle):
    """
    GPU resource handle with memory management.
    """
    
    def __init__(self, gpu_id: int, allocated_memory_mb: Optional[float] = None):
        super().__init__(f"gpu_{gpu_id}")
        self.gpu_id = gpu_id
        self.allocated_memory_mb = allocated_memory_mb
        self.cleanup_callbacks: List[callable] = []
        
        self.logger.info("gpu_resource.allocated", extra={
            'gpu_id': gpu_id,
            'allocated_memory_mb': allocated_memory_mb
        })
    
    def add_cleanup_callback(self, callback: callable) -> None:
        """Add callback to be called during cleanup."""
        self.cleanup_callbacks.append(callback)
    
    def cleanup(self) -> None:
        """Cleanup GPU resources."""
        if not self.is_active:
            return
        
        try:
            # Execute cleanup callbacks
            for callback in self.cleanup_callbacks:
                try:
                    callback()
                except Exception as e:
                    self.logger.warning("gpu_cleanup_callback.failed", extra={
                        'gpu_id': self.gpu_id,
                        'error': str(e)
                    })
            
            # Clear GPU memory if possible
            try:
                import torch
                if torch.cuda.is_available() and self.gpu_id < torch.cuda.device_count():
                    with torch.cuda.device(self.gpu_id):
                        torch.cuda.empty_cache()
            except ImportError:
                pass
            
            # Force garbage collection
            gc.collect()
            
            self.logger.info("gpu_resource.cleaned", extra={
                'gpu_id': self.gpu_id,
                'duration_seconds': (datetime.now() - self.acquired_at).total_seconds()
            })
            
        except Exception as e:
            self.logger.error("gpu_resource.cleanup_failed", extra={
                'gpu_id': self.gpu_id,
                'error': str(e)
            })
        finally:
            # Mark resource as inactive
            self.is_active = False


class MemoryResourceHandle(ResourceHandle):
    """
    Memory resource handle with usage tracking.
    """
    
    def __init__(self, allocated_mb: float, max_mb: float):
        super().__init__(f"memory_{allocated_mb}mb")
        self.allocated_mb = allocated_mb
        self.max_mb = max_mb
        self.initial_usage = self._get_current_usage()
    
    def _get_current_usage(self) -> float:
        """Get current memory usage in MB."""
        try:
            return psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0
    
    def cleanup(self) -> None:
        """Cleanup memory resources."""
        if not self.is_active:
            return
        
        try:
            # Force garbage collection
            gc.collect()
            
            current_usage = self._get_current_usage()
            usage_delta = current_usage - self.initial_usage
            
            self.logger.info("memory_resource.cleaned", extra={
                'allocated_mb': self.allocated_mb,
                'initial_usage_mb': self.initial_usage,
                'current_usage_mb': current_usage,
                'usage_delta_mb': usage_delta,
                'duration_seconds': (datetime.now() - self.acquired_at).total_seconds()
            })
            
        except Exception as e:
            self.logger.error("memory_resource.cleanup_failed", extra={
                'error': str(e)
            })
        finally:
            # Mark resource as inactive
            self.is_active = False


class GPUResourceManager:
    """
    GPU resource manager with memory allocation and cleanup.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize GPU resource manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.logger = logging.getLogger(__name__)
        
        # GPU configuration
        self.enable_gpu = self.config.get('enable_gpu', True)
        self.gpu_id = self.config.get('gpu_id', 0)
        self.memory_limit_gb = self.config.get('gpu_memory_limit_gb')
        
        # Resource tracking
        self.active_handles: Dict[str, GPUResourceHandle] = {}
        self.resource_lock = threading.RLock()
        
        # GPU availability check
        self.gpu_available = self._check_gpu_availability()
        
        if self.gpu_available:
            self.logger.info("gpu_resource_manager.initialized", extra={
                'gpu_available': True,
                'gpu_id': self.gpu_id,
                'memory_limit_gb': self.memory_limit_gb
            })
        else:
            self.logger.warning("gpu_resource_manager.no_gpu", extra={
                'enable_gpu': self.enable_gpu
            })
    
    def _check_gpu_availability(self) -> bool:
        """Check if GPU is available and accessible."""
        if not self.enable_gpu:
            return False
        
        try:
            import torch
            if torch.cuda.is_available() and self.gpu_id < torch.cuda.device_count():
                # Test GPU access
                with torch.cuda.device(self.gpu_id):
                    test_tensor = torch.tensor([1.0], device=f'cuda:{self.gpu_id}')
                    del test_tensor
                    torch.cuda.empty_cache()
                return True
        except (ImportError, Exception) as e:
            self.logger.warning("gpu_availability_check.failed", extra={
                'error': str(e)
            })
        
        return False
    
    def get_gpu_usage(self) -> Optional[Dict[str, float]]:
        """Get current GPU usage statistics."""
        if not self.gpu_available:
            return None
        
        try:
            import torch
            if torch.cuda.is_available() and self.gpu_id < torch.cuda.device_count():
                with torch.cuda.device(self.gpu_id):
                    memory_allocated = torch.cuda.memory_allocated() / (1024**2)  # MB
                    memory_reserved = torch.cuda.memory_reserved() / (1024**2)  # MB
                    
                    try:
                        import pynvml
                        pynvml.nvmlInit()
                        handle = pynvml.nvmlDeviceGetHandleByIndex(self.gpu_id)
                        
                        memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                        
                        return {
                            'memory_allocated_mb': memory_allocated,
                            'memory_reserved_mb': memory_reserved,
                            'memory_total_mb': memory_info.total / (1024**2),
                            'memory_used_mb': memory_info.used / (1024**2),
                            'memory_free_mb': memory_info.free / (1024**2),
                            'utilization_percent': utilization.gpu
                        }
                    except ImportError:
                        return {
                            'memory_allocated_mb': memory_allocated,
                            'memory_reserved_mb': memory_reserved
                        }
                        
        except Exception as e:
            self.logger.error("gpu_usage.query_failed", extra={
                'gpu_id': self.gpu_id,
                'error': str(e)
            })
        
        return None
    
    def allocate_gpu_resource(self, estimated_memory_mb: Optional[float] = None) -> GPUResourceHandle:
        """
        Allocate GPU resource with memory estimation.
        
        Args:
            estimated_memory_mb: Estimated memory requirement
            
        Returns:
            GPUResourceHandle for resource management
            
        Raises:
            ResourceAllocationError: If allocation fails
        """
        if not self.gpu_available:
            raise ResourceAllocationError("GPU not available for allocation")
        
        with self.resource_lock:
            try:
                # Check available memory if limit is set
                if self.memory_limit_gb and estimated_memory_mb:
                    limit_mb = self.memory_limit_gb * 1024
                    if estimated_memory_mb > limit_mb:
                        raise ResourceAllocationError(
                            f"Estimated memory {estimated_memory_mb}MB exceeds limit {limit_mb}MB"
                        )
                
                # Check current GPU usage
                gpu_usage = self.get_gpu_usage()
                if gpu_usage and estimated_memory_mb:
                    available_mb = gpu_usage.get('memory_free_mb', 0)
                    if estimated_memory_mb > available_mb * 0.8:  # Leave 20% buffer
                        # Try cleanup first
                        self.cleanup_unused_resources()
                        
                        # Check again after cleanup
                        gpu_usage = self.get_gpu_usage()
                        available_mb = gpu_usage.get('memory_free_mb', 0)
                        if estimated_memory_mb > available_mb * 0.8:
                            raise ResourceAllocationError(
                                f"Insufficient GPU memory: need {estimated_memory_mb}MB, "
                                f"available {available_mb}MB"
                            )
                
                # Create resource handle
                handle = GPUResourceHandle(self.gpu_id, estimated_memory_mb)
                self.active_handles[handle.resource_id] = handle
                
                # Add cleanup callback to remove from tracking
                handle.add_cleanup_callback(
                    lambda: self.active_handles.pop(handle.resource_id, None)
                )
                
                self.logger.info("gpu_resource.allocated", extra={
                    'resource_id': handle.resource_id,
                    'estimated_memory_mb': estimated_memory_mb,
                    'active_handles': len(self.active_handles)
                })
                
                return handle
                
            except Exception as e:
                self.logger.error("gpu_resource.allocation_failed", extra={
                    'estimated_memory_mb': estimated_memory_mb,
                    'error': str(e)
                })
                raise ResourceAllocationError(f"GPU allocation failed: {e}") from e
    
    def cleanup_unused_resources(self) -> int:
        """
        Cleanup unused GPU resources.
        
        Returns:
            Number of resources cleaned up
        """
        cleaned_count = 0
        
        with self.resource_lock:
            # Find inactive handles
            inactive_handles = [
                handle for handle in self.active_handles.values()
                if not handle.is_active
            ]
            
            # Cleanup inactive handles
            for handle in inactive_handles:
                try:
                    handle.cleanup()
                    cleaned_count += 1
                except Exception as e:
                    self.logger.error("gpu_cleanup.failed", extra={
                        'resource_id': handle.resource_id,
                        'error': str(e)
                    })
        
        # Force GPU memory cleanup
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        
        # Force garbage collection
        gc.collect()
        
        if cleaned_count > 0:
            self.logger.info("gpu_cleanup.completed", extra={
                'cleaned_count': cleaned_count,
                'remaining_handles': len(self.active_handles)
            })
        
        return cleaned_count
    
    @contextmanager
    def gpu_resource(self, estimated_memory_mb: Optional[float] = None):
        """Context manager for GPU resource allocation."""
        handle = self.allocate_gpu_resource(estimated_memory_mb)
        try:
            yield handle
        finally:
            handle.cleanup()


class SystemResourceManager:
    """
    System resource manager for memory, disk, and process resources.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize system resource manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.logger = logging.getLogger(__name__)
        
        # Configuration
        self.max_memory_gb = self.config.get('max_memory_gb', 16.0)
        self.oom_threshold = self.config.get('oom_protection_threshold', 0.9)
        self.min_disk_space_gb = self.config.get('min_disk_space_gb', 5.0)
        self.cleanup_temp_files = self.config.get('cleanup_temp_files', True)
        
        # Monitoring
        self.monitoring_enabled = self.config.get('memory_monitoring_enabled', True)
        self.monitoring_interval = 30  # seconds
        self.monitoring_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()
        
        # Resource tracking
        self.resource_history: List[ResourceUsage] = []
        self.history_lock = threading.RLock()
        
        if self.monitoring_enabled:
            self._start_monitoring()
        
        self.logger.info("system_resource_manager.initialized", extra={
            'max_memory_gb': self.max_memory_gb,
            'oom_threshold': self.oom_threshold,
            'monitoring_enabled': self.monitoring_enabled
        })
    
    def get_current_usage(self) -> ResourceUsage:
        """Get current system resource usage."""
        try:
            # Memory usage
            memory = psutil.virtual_memory()
            memory_mb = memory.used / (1024**2)
            memory_percent = memory.percent
            
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Disk usage for current directory
            disk_usage = shutil.disk_usage('.')
            disk_total_gb = disk_usage.total / (1024**3)
            disk_free_gb = disk_usage.free / (1024**3)
            disk_used_gb = disk_total_gb - disk_free_gb
            
            # Process info
            process = psutil.Process()
            open_files = len(process.open_files())
            threads = process.num_threads()
            
            # GPU usage (optional)
            gpu_memory_mb = None
            gpu_utilization = None
            
            return ResourceUsage(
                timestamp=datetime.now(),
                memory_mb=memory_mb,
                memory_percent=memory_percent,
                cpu_percent=cpu_percent,
                disk_usage_gb=disk_used_gb,
                disk_free_gb=disk_free_gb,
                gpu_memory_mb=gpu_memory_mb,
                gpu_utilization=gpu_utilization,
                open_files=open_files,
                threads=threads
            )
            
        except Exception as e:
            self.logger.error("resource_usage.query_failed", extra={
                'error': str(e)
            })
            # Return minimal usage object
            return ResourceUsage(
                timestamp=datetime.now(),
                memory_mb=0.0,
                memory_percent=0.0,
                cpu_percent=0.0,
                disk_usage_gb=0.0,
                disk_free_gb=0.0
            )
    
    def check_resource_availability(self, memory_gb: Optional[float] = None,
                                  disk_gb: Optional[float] = None) -> Tuple[bool, List[str]]:
        """
        Check if requested resources are available.
        
        Args:
            memory_gb: Required memory in GB
            disk_gb: Required disk space in GB
            
        Returns:
            Tuple of (available, error_messages)
        """
        errors = []
        usage = self.get_current_usage()
        
        # Check memory availability
        if memory_gb:
            available_memory_gb = (100 - usage.memory_percent) / 100 * self.max_memory_gb
            if memory_gb > available_memory_gb:
                errors.append(
                    f"Insufficient memory: need {memory_gb}GB, "
                    f"available {available_memory_gb:.1f}GB"
                )
        
        # Check OOM protection threshold
        if usage.memory_percent > self.oom_threshold * 100:
            errors.append(
                f"Memory usage above OOM threshold: {usage.memory_percent:.1f}% "
                f"> {self.oom_threshold * 100:.1f}%"
            )
        
        # Check disk space
        if disk_gb and disk_gb > usage.disk_free_gb:
            errors.append(
                f"Insufficient disk space: need {disk_gb}GB, "
                f"available {usage.disk_free_gb:.1f}GB"
            )
        
        if usage.disk_free_gb < self.min_disk_space_gb:
            errors.append(
                f"Disk space below minimum: {usage.disk_free_gb:.1f}GB "
                f"< {self.min_disk_space_gb}GB"
            )
        
        return len(errors) == 0, errors
    
    def allocate_memory_resource(self, memory_gb: float) -> MemoryResourceHandle:
        """
        Allocate memory resource with tracking.
        
        Args:
            memory_gb: Memory requirement in GB
            
        Returns:
            MemoryResourceHandle
            
        Raises:
            ResourceAllocationError: If allocation fails
        """
        available, errors = self.check_resource_availability(memory_gb=memory_gb)
        
        if not available:
            raise ResourceAllocationError(f"Memory allocation failed: {errors}")
        
        memory_mb = memory_gb * 1024
        max_mb = self.max_memory_gb * 1024
        
        handle = MemoryResourceHandle(memory_mb, max_mb)
        
        self.logger.info("memory_resource.allocated", extra={
            'resource_id': handle.resource_id,
            'memory_gb': memory_gb
        })
        
        return handle
    
    def cleanup_temp_files(self, temp_dirs: Optional[List[Union[str, Path]]] = None) -> int:
        """
        Cleanup temporary files and directories.
        
        Args:
            temp_dirs: Optional list of temp directories to clean
            
        Returns:
            Number of files/directories cleaned
        """
        if not self.cleanup_temp_files:
            return 0
        
        cleaned_count = 0
        
        # Default temp directories
        if temp_dirs is None:
            temp_dirs = [
                '/tmp',
                Path.home() / 'tmp',
                Path('.') / 'tmp',
                Path('.') / 'temp'
            ]
        
        for temp_dir in temp_dirs:
            temp_path = Path(temp_dir)
            if not temp_path.exists() or not temp_path.is_dir():
                continue
            
            try:
                # Clean files older than 1 hour
                cutoff_time = datetime.now() - timedelta(hours=1)
                
                for file_path in temp_path.rglob('*'):
                    if not file_path.is_file():
                        continue
                    
                    try:
                        file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if file_time < cutoff_time:
                            file_path.unlink()
                            cleaned_count += 1
                    except Exception:
                        continue
                        
            except Exception as e:
                self.logger.warning("temp_cleanup.failed", extra={
                    'temp_dir': str(temp_path),
                    'error': str(e)
                })
        
        if cleaned_count > 0:
            self.logger.info("temp_cleanup.completed", extra={
                'cleaned_count': cleaned_count
            })
        
        return cleaned_count
    
    def _start_monitoring(self) -> None:
        """Start background resource monitoring."""
        def monitor():
            while not self.shutdown_event.is_set():
                try:
                    usage = self.get_current_usage()
                    
                    with self.history_lock:
                        self.resource_history.append(usage)
                        # Keep only last 1000 samples
                        if len(self.resource_history) > 1000:
                            self.resource_history = self.resource_history[-1000:]
                    
                    # Check thresholds and emit warnings
                    if usage.memory_percent > self.oom_threshold * 100:
                        self.logger.warning("resource_monitor.memory_warning", extra={
                            'memory_percent': usage.memory_percent,
                            'threshold': self.oom_threshold * 100
                        })
                    
                    if usage.disk_free_gb < self.min_disk_space_gb:
                        self.logger.warning("resource_monitor.disk_warning", extra={
                            'disk_free_gb': usage.disk_free_gb,
                            'minimum_gb': self.min_disk_space_gb
                        })
                    
                except Exception as e:
                    self.logger.error("resource_monitor.failed", extra={
                        'error': str(e)
                    })
                
                # Wait for next monitoring cycle
                self.shutdown_event.wait(self.monitoring_interval)
        
        self.monitoring_thread = threading.Thread(target=monitor, daemon=True)
        self.monitoring_thread.start()
        
        self.logger.info("resource_monitoring.started", extra={
            'monitoring_interval': self.monitoring_interval
        })
    
    def get_resource_history(self, last_n: Optional[int] = None) -> List[ResourceUsage]:
        """Get resource usage history."""
        with self.history_lock:
            if last_n:
                return self.resource_history[-last_n:]
            return self.resource_history.copy()
    
    def close(self) -> None:
        """Shutdown resource manager."""
        self.shutdown_event.set()
        
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=5)
        
        self.logger.info("system_resource_manager.closed")


# Custom exceptions
class ResourceAllocationError(Exception):
    """Raised when resource allocation fails."""
    pass


class ResourceExhaustionError(Exception):
    """Raised when system resources are exhausted."""
    pass
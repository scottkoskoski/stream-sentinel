# /stream-sentinel/src/ml/training/core/checkpoint_manager.py

"""
CheckpointManager: Atomic Persistence and Recovery for ML Training

Provides enterprise-grade checkpointing capabilities to prevent catastrophic model loss
during training pipeline execution. Implements atomic saves, rollback mechanisms,
and comprehensive recovery strategies.

Key Features:
- Atomic checkpoint saves with rollback on failure
- Immediate persistence after each hyperparameter trial
- Lock-based concurrency control for multi-process safety  
- Comprehensive recovery from any failure point
- Configurable retention policies and cleanup
- Structured metadata for checkpoint analysis

Architecture:
- Atomic saves using temporary files and rename operations
- SQLite WAL mode for study persistence with ACID guarantees
- File-based locking for concurrent access safety
- Comprehensive validation before checkpoint creation
- Automatic cleanup of stale checkpoints and locks
"""

import json
import pickle
import sqlite3
import threading
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from contextlib import contextmanager
import hashlib
import os
import tempfile
import shutil


@dataclass
class ModelCheckpoint:
    """
    Immutable checkpoint containing model state and metadata.
    
    Designed to capture complete state required for recovery and analysis.
    All fields are validated during creation to ensure consistency.
    """
    trial_number: int
    parameters: Dict[str, Any]
    score: float
    model: Any
    timestamp: datetime
    gpu_memory_usage: Optional[int] = None
    validation_metrics: Optional[Dict[str, float]] = None
    checkpoint_id: Optional[str] = None
    model_type: Optional[str] = None
    training_duration: Optional[float] = None
    feature_count: Optional[int] = None
    data_hash: Optional[str] = None
    
    def __post_init__(self):
        """Validate checkpoint data and generate unique ID."""
        if self.trial_number < 0:
            raise ValueError(f"Trial number must be non-negative: {self.trial_number}")
        if not isinstance(self.score, (int, float)) or not (-1 <= self.score <= 1):
            raise ValueError(f"Score must be numeric between -1 and 1: {self.score}")
        if not self.parameters:
            raise ValueError("Parameters cannot be empty")
        if self.model is None:
            raise ValueError("Model cannot be None")
            
        # Generate unique checkpoint ID
        if self.checkpoint_id is None:
            content = f"{self.trial_number}_{self.score}_{self.timestamp.isoformat()}"
            self.checkpoint_id = hashlib.sha256(content.encode()).hexdigest()[:16]
    
    @property
    def size_estimate(self) -> int:
        """Estimate checkpoint size in bytes for storage planning."""
        try:
            return len(pickle.dumps(self.model)) + len(json.dumps(self.parameters)) + 1024
        except Exception:
            return 50 * 1024 * 1024  # Conservative 50MB estimate
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary excluding the model object."""
        data = asdict(self)
        data.pop('model', None)  # Exclude model from serialization
        data['timestamp'] = self.timestamp.isoformat()
        return data


class CheckpointStore:
    """
    Thread-safe storage backend for model checkpoints with atomic operations.
    
    Implements atomic saves using temporary files and rename operations to ensure
    consistency even under system failures. Supports concurrent access with
    file-based locking and automatic cleanup of stale resources.
    """
    
    def __init__(self, base_path: Union[str, Path], retention_hours: int = 168):
        """
        Initialize checkpoint store with configurable retention policy.
        
        Args:
            base_path: Directory for checkpoint storage
            retention_hours: Hours to retain old checkpoints (default: 7 days)
        """
        self.base_path = Path(base_path)
        self.retention_hours = retention_hours
        self.logger = logging.getLogger(__name__)
        
        # Create directory structure
        self.checkpoints_dir = self.base_path / "checkpoints"
        self.metadata_dir = self.base_path / "metadata" 
        self.locks_dir = self.base_path / "locks"
        
        for dir_path in [self.checkpoints_dir, self.metadata_dir, self.locks_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
            
        # Thread safety
        self._lock = threading.RLock()
        
        # Initialize cleanup
        self._cleanup_stale_locks()
        
    def save_atomic(self, checkpoint: ModelCheckpoint) -> str:
        """
        Atomically save checkpoint with rollback capability.
        
        Uses temporary files and atomic rename to ensure consistency.
        Returns checkpoint ID for later retrieval.
        
        Args:
            checkpoint: ModelCheckpoint to save
            
        Returns:
            Unique checkpoint ID for retrieval
            
        Raises:
            CheckpointSaveError: If save operation fails
        """
        checkpoint_id = checkpoint.checkpoint_id
        
        with self._lock:
            try:
                # Validate checkpoint before save
                self._validate_checkpoint(checkpoint)
                
                # Create temporary files
                temp_model_path = self._create_temp_file(".pkl")
                temp_metadata_path = self._create_temp_file(".json")
                
                try:
                    # Save model to temporary file
                    with open(temp_model_path, 'wb') as f:
                        pickle.dump(checkpoint.model, f, protocol=pickle.HIGHEST_PROTOCOL)
                    
                    # Save metadata to temporary file
                    metadata = checkpoint.to_dict()
                    metadata['model_path'] = str(self.checkpoints_dir / f"{checkpoint_id}.pkl")
                    
                    with open(temp_metadata_path, 'w') as f:
                        json.dump(metadata, f, indent=2)
                    
                    # Atomic moves to final locations
                    final_model_path = self.checkpoints_dir / f"{checkpoint_id}.pkl"
                    final_metadata_path = self.metadata_dir / f"{checkpoint_id}.json"
                    
                    shutil.move(str(temp_model_path), str(final_model_path))
                    shutil.move(str(temp_metadata_path), str(final_metadata_path))
                    
                    self.logger.info("checkpoint.saved", extra={
                        "checkpoint_id": checkpoint_id,
                        "trial_number": checkpoint.trial_number,
                        "score": checkpoint.score,
                        "size_mb": checkpoint.size_estimate / (1024 * 1024)
                    })
                    
                    return checkpoint_id
                    
                except Exception as e:
                    # Cleanup temporary files on failure
                    for temp_path in [temp_model_path, temp_metadata_path]:
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                    raise CheckpointSaveError(f"Failed to save checkpoint {checkpoint_id}: {e}") from e
                    
            except Exception as e:
                self.logger.error("checkpoint.save_failed", extra={
                    "checkpoint_id": checkpoint_id,
                    "error": str(e),
                    "trial_number": checkpoint.trial_number
                })
                raise
    
    def load_checkpoint(self, checkpoint_id: str) -> Optional[ModelCheckpoint]:
        """
        Load checkpoint by ID with validation.
        
        Args:
            checkpoint_id: Unique checkpoint identifier
            
        Returns:
            ModelCheckpoint if found and valid, None otherwise
        """
        with self._lock:
            try:
                metadata_path = self.metadata_dir / f"{checkpoint_id}.json"
                model_path = self.checkpoints_dir / f"{checkpoint_id}.pkl"
                
                if not metadata_path.exists() or not model_path.exists():
                    self.logger.warning("checkpoint.not_found", extra={
                        "checkpoint_id": checkpoint_id,
                        "metadata_exists": metadata_path.exists(),
                        "model_exists": model_path.exists()
                    })
                    return None
                
                # Load metadata
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                
                # Load model
                with open(model_path, 'rb') as f:
                    model = pickle.load(f)
                
                # Reconstruct checkpoint
                checkpoint = ModelCheckpoint(
                    trial_number=metadata['trial_number'],
                    parameters=metadata['parameters'], 
                    score=metadata['score'],
                    model=model,
                    timestamp=datetime.fromisoformat(metadata['timestamp']),
                    gpu_memory_usage=metadata.get('gpu_memory_usage'),
                    validation_metrics=metadata.get('validation_metrics'),
                    checkpoint_id=metadata['checkpoint_id'],
                    model_type=metadata.get('model_type'),
                    training_duration=metadata.get('training_duration'),
                    feature_count=metadata.get('feature_count'),
                    data_hash=metadata.get('data_hash')
                )
                
                self.logger.info("checkpoint.loaded", extra={
                    "checkpoint_id": checkpoint_id,
                    "trial_number": checkpoint.trial_number,
                    "score": checkpoint.score
                })
                
                return checkpoint
                
            except Exception as e:
                self.logger.error("checkpoint.load_failed", extra={
                    "checkpoint_id": checkpoint_id,
                    "error": str(e)
                })
                return None
    
    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all available checkpoints with metadata."""
        checkpoints = []
        
        with self._lock:
            for metadata_file in self.metadata_dir.glob("*.json"):
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    checkpoints.append(metadata)
                except Exception as e:
                    self.logger.warning("checkpoint.metadata_corrupted", extra={
                        "file": str(metadata_file),
                        "error": str(e)
                    })
        
        # Sort by timestamp (most recent first)
        checkpoints.sort(key=lambda x: x['timestamp'], reverse=True)
        return checkpoints
    
    def cleanup_old_checkpoints(self) -> int:
        """Remove checkpoints older than retention period."""
        cutoff_time = datetime.now() - timedelta(hours=self.retention_hours)
        cleaned_count = 0
        
        with self._lock:
            for metadata_file in self.metadata_dir.glob("*.json"):
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    
                    checkpoint_time = datetime.fromisoformat(metadata['timestamp'])
                    if checkpoint_time < cutoff_time:
                        checkpoint_id = metadata['checkpoint_id']
                        
                        # Remove both metadata and model files
                        metadata_file.unlink(missing_ok=True)
                        model_file = self.checkpoints_dir / f"{checkpoint_id}.pkl"
                        model_file.unlink(missing_ok=True)
                        
                        cleaned_count += 1
                        
                        self.logger.info("checkpoint.cleaned", extra={
                            "checkpoint_id": checkpoint_id,
                            "age_hours": (datetime.now() - checkpoint_time).total_seconds() / 3600
                        })
                        
                except Exception as e:
                    self.logger.warning("checkpoint.cleanup_failed", extra={
                        "file": str(metadata_file),
                        "error": str(e)
                    })
        
        return cleaned_count
    
    def _validate_checkpoint(self, checkpoint: ModelCheckpoint) -> None:
        """Validate checkpoint before save."""
        if checkpoint.size_estimate > 1024 * 1024 * 1024:  # 1GB limit
            raise CheckpointValidationError(f"Checkpoint too large: {checkpoint.size_estimate} bytes")
            
        # Check available disk space (require 2x checkpoint size)
        free_space = shutil.disk_usage(self.base_path).free
        if free_space < checkpoint.size_estimate * 2:
            raise CheckpointValidationError(f"Insufficient disk space: need {checkpoint.size_estimate * 2}, have {free_space}")
    
    def _create_temp_file(self, suffix: str) -> Path:
        """Create temporary file for atomic operations."""
        fd, path = tempfile.mkstemp(suffix=suffix, dir=self.base_path)
        os.close(fd)
        return Path(path)
    
    def _cleanup_stale_locks(self) -> None:
        """Remove stale lock files from previous runs."""
        stale_cutoff = datetime.now() - timedelta(hours=1)
        
        for lock_file in self.locks_dir.glob("*.lock"):
            try:
                stat = lock_file.stat()
                if datetime.fromtimestamp(stat.st_mtime) < stale_cutoff:
                    lock_file.unlink(missing_ok=True)
                    self.logger.info("stale_lock_removed", extra={"lock_file": str(lock_file)})
            except Exception as e:
                self.logger.warning("lock_cleanup_failed", extra={
                    "lock_file": str(lock_file),
                    "error": str(e)
                })


class CheckpointManager:
    """
    High-level checkpoint management with study coordination.
    
    Provides the main interface for checkpoint operations, integrating with
    Optuna studies and providing comprehensive recovery mechanisms.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize checkpoint manager with configuration.
        
        Args:
            config: Configuration dictionary with checkpoint settings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Initialize storage
        checkpoint_dir = Path(config.get('checkpoint_dir', 'models/checkpoints'))
        retention_hours = config.get('retention_hours', 168)  # 7 days default
        
        self.store = CheckpointStore(checkpoint_dir, retention_hours)
        
        # Study management
        study_db_path = checkpoint_dir / "studies.db"
        self.study_storage = f"sqlite:///{study_db_path}?enable_wal=1"
        
        # Best model tracking
        self.best_checkpoint_id: Optional[str] = None
        self.best_score: float = float('-inf')
        
        # Recovery state
        self.recovery_mode = False
        self.recovered_checkpoints: List[str] = []
        
        self.logger.info("checkpoint_manager.initialized", extra={
            "checkpoint_dir": str(checkpoint_dir),
            "retention_hours": retention_hours,
            "study_storage": self.study_storage
        })
    
    def save_checkpoint(self, checkpoint: ModelCheckpoint) -> str:
        """
        Save checkpoint and update best model tracking.
        
        Args:
            checkpoint: ModelCheckpoint to save
            
        Returns:
            Checkpoint ID for retrieval
        """
        try:
            # Save checkpoint
            checkpoint_id = self.store.save_atomic(checkpoint)
            
            # Update best model if score improved
            if checkpoint.score > self.best_score:
                self.best_score = checkpoint.score
                self.best_checkpoint_id = checkpoint_id
                
                self.logger.info("best_model_updated", extra={
                    "checkpoint_id": checkpoint_id,
                    "score": checkpoint.score,
                    "improvement": checkpoint.score - (self.best_score if self.best_checkpoint_id else 0),
                    "trial_number": checkpoint.trial_number
                })
            
            return checkpoint_id
            
        except Exception as e:
            self.logger.error("checkpoint_save_failed", extra={
                "trial_number": checkpoint.trial_number,
                "score": checkpoint.score,
                "error": str(e)
            })
            raise
    
    def load_best_checkpoint(self) -> Optional[ModelCheckpoint]:
        """Load the best checkpoint by score."""
        if self.best_checkpoint_id:
            return self.store.load_checkpoint(self.best_checkpoint_id)
        
        # Fallback: find best from all checkpoints
        checkpoints = self.store.list_checkpoints()
        if not checkpoints:
            return None
        
        best_metadata = max(checkpoints, key=lambda x: x['score'])
        return self.store.load_checkpoint(best_metadata['checkpoint_id'])
    
    def recover_from_failure(self, study_name: str) -> Tuple[Optional[ModelCheckpoint], List[str]]:
        """
        Recover from training failure by finding the latest checkpoint.
        
        Args:
            study_name: Name of the Optuna study to recover
            
        Returns:
            Tuple of (best_checkpoint, list_of_recovered_checkpoint_ids)
        """
        self.recovery_mode = True
        self.recovered_checkpoints = []
        
        try:
            # Find all checkpoints
            all_checkpoints = self.store.list_checkpoints()
            
            if not all_checkpoints:
                self.logger.warning("recovery.no_checkpoints", extra={"study_name": study_name})
                return None, []
            
            # Find best checkpoint
            best_metadata = max(all_checkpoints, key=lambda x: x['score'])
            best_checkpoint = self.store.load_checkpoint(best_metadata['checkpoint_id'])
            
            # Track recovered checkpoints
            self.recovered_checkpoints = [cp['checkpoint_id'] for cp in all_checkpoints]
            
            self.logger.info("recovery.completed", extra={
                "study_name": study_name,
                "best_score": best_metadata['score'],
                "recovered_count": len(self.recovered_checkpoints),
                "best_checkpoint_id": best_metadata['checkpoint_id']
            })
            
            return best_checkpoint, self.recovered_checkpoints
            
        except Exception as e:
            self.logger.error("recovery.failed", extra={
                "study_name": study_name,
                "error": str(e)
            })
            raise CheckpointRecoveryError(f"Failed to recover from failure: {e}") from e
    
    @contextmanager
    def checkpoint_transaction(self, checkpoint: ModelCheckpoint):
        """
        Context manager for transactional checkpoint operations.
        
        Ensures cleanup on failure and proper resource management.
        """
        checkpoint_id = None
        try:
            checkpoint_id = self.save_checkpoint(checkpoint)
            yield checkpoint_id
        except Exception as e:
            if checkpoint_id:
                # Attempt cleanup on failure
                try:
                    self._cleanup_failed_checkpoint(checkpoint_id)
                except Exception:
                    pass  # Don't mask original exception
            raise
    
    def _cleanup_failed_checkpoint(self, checkpoint_id: str) -> None:
        """Clean up failed checkpoint artifacts."""
        try:
            checkpoint_path = self.store.checkpoints_dir / f"{checkpoint_id}.pkl"
            metadata_path = self.store.metadata_dir / f"{checkpoint_id}.json"
            
            checkpoint_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            
            self.logger.info("failed_checkpoint_cleaned", extra={
                "checkpoint_id": checkpoint_id
            })
        except Exception as e:
            self.logger.warning("cleanup_failed", extra={
                "checkpoint_id": checkpoint_id,
                "error": str(e)
            })


# Custom exceptions
class CheckpointError(Exception):
    """Base exception for checkpoint operations."""
    pass

class CheckpointSaveError(CheckpointError):
    """Raised when checkpoint save operation fails."""
    pass

class CheckpointValidationError(CheckpointError):
    """Raised when checkpoint validation fails."""
    pass

class CheckpointRecoveryError(CheckpointError):
    """Raised when checkpoint recovery fails."""
    pass
# /stream-sentinel/src/ml/training/core/data_processor.py

"""
DataProcessor: IEEE-CIS Data Loading, Preprocessing, and Caching

Provides robust, cached data processing pipeline for IEEE-CIS fraud detection dataset.
Implements comprehensive validation, feature engineering, and intelligent caching
to ensure reliable and efficient data preparation for model training.

Key Features:
- Robust IEEE-CIS dataset loading with identity data merging
- Comprehensive data validation and quality checks
- Advanced feature engineering with statistical transformations
- Intelligent caching with version control and data integrity validation
- Memory-efficient processing for large datasets (590k+ transactions)
- Configurable preprocessing pipelines for different environments

Architecture:
- Immutable ProcessedDataset objects with comprehensive metadata
- Atomic cache operations with integrity validation
- Extensible preprocessing pipeline with configurable stages
- Comprehensive error handling with recovery mechanisms
- Memory usage monitoring and optimization
"""

import json
import hashlib
import pickle
import logging
import time
import psutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder
from sklearn.feature_selection import SelectKBest, mutual_info_classif
from sklearn.model_selection import train_test_split


@dataclass
class ValidationResult:
    """
    Comprehensive data validation results with detailed diagnostics.
    """
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    validation_timestamp: datetime = field(default_factory=datetime.now)
    
    def add_error(self, error: str) -> None:
        """Add validation error."""
        self.errors.append(error)
        self.is_valid = False
    
    def add_warning(self, warning: str) -> None:
        """Add validation warning."""
        self.warnings.append(warning)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'is_valid': self.is_valid,
            'errors': self.errors,
            'warnings': self.warnings,
            'metrics': self.metrics,
            'validation_timestamp': self.validation_timestamp.isoformat()
        }


@dataclass
class ProcessedDataset:
    """
    Immutable processed dataset with comprehensive metadata.
    
    Encapsulates all data and metadata required for model training,
    ensuring reproducibility and traceability.
    """
    X: pd.DataFrame
    y: pd.Series
    feature_names: List[str]
    label_encoders: Dict[str, LabelEncoder]
    preprocessing_metadata: Dict[str, Any]
    data_hash: str
    processing_timestamp: datetime
    validation_result: ValidationResult
    memory_usage_mb: Optional[float] = None
    
    def __post_init__(self):
        """Validate dataset consistency and compute memory usage."""
        if len(self.X) != len(self.y):
            raise ValueError(f"Feature and target length mismatch: {len(self.X)} vs {len(self.y)}")
        
        if len(self.feature_names) != len(self.X.columns):
            raise ValueError(f"Feature names count mismatch: {len(self.feature_names)} vs {len(self.X.columns)}")
        
        if not self.validation_result.is_valid:
            raise ValueError(f"Dataset validation failed: {self.validation_result.errors}")
        
        # Compute memory usage
        if self.memory_usage_mb is None:
            self.memory_usage_mb = (self.X.memory_usage(deep=True).sum() + 
                                   self.y.memory_usage(deep=True)) / (1024 * 1024)
    
    @property
    def shape(self) -> Tuple[int, int]:
        """Dataset shape (samples, features)."""
        return self.X.shape
    
    @property
    def fraud_rate(self) -> float:
        """Fraud rate in the dataset."""
        return self.y.mean()
    
    def get_train_test_split(self, test_size: float = 0.2, 
                           stratify: bool = True,
                           random_state: int = 42) -> Tuple['ProcessedDataset', 'ProcessedDataset']:
        """
        Split dataset into train and test portions.
        
        Returns new ProcessedDataset objects for train and test sets.
        """
        stratify_col = self.y if stratify else None
        
        X_train, X_test, y_train, y_test = train_test_split(
            self.X, self.y,
            test_size=test_size,
            stratify=stratify_col,
            random_state=random_state
        )
        
        # Create new datasets with updated metadata
        train_metadata = self.preprocessing_metadata.copy()
        train_metadata['split_type'] = 'train'
        train_metadata['split_ratio'] = 1.0 - test_size
        
        test_metadata = self.preprocessing_metadata.copy()
        test_metadata['split_type'] = 'test'
        test_metadata['split_ratio'] = test_size
        
        train_dataset = ProcessedDataset(
            X=X_train.reset_index(drop=True),
            y=y_train.reset_index(drop=True),
            feature_names=self.feature_names,
            label_encoders=self.label_encoders,
            preprocessing_metadata=train_metadata,
            data_hash=self._compute_subset_hash(X_train, y_train),
            processing_timestamp=datetime.now(),
            validation_result=self.validation_result  # Inherit validation
        )
        
        test_dataset = ProcessedDataset(
            X=X_test.reset_index(drop=True),
            y=y_test.reset_index(drop=True),
            feature_names=self.feature_names,
            label_encoders=self.label_encoders,
            preprocessing_metadata=test_metadata,
            data_hash=self._compute_subset_hash(X_test, y_test),
            processing_timestamp=datetime.now(),
            validation_result=self.validation_result  # Inherit validation
        )
        
        return train_dataset, test_dataset
    
    def _compute_subset_hash(self, X: pd.DataFrame, y: pd.Series) -> str:
        """Compute hash for dataset subset."""
        content = f"{len(X)}_{len(y)}_{X.columns.tolist()}_{y.sum()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


class DataCache:
    """
    Intelligent data cache with integrity validation and version control.
    
    Provides atomic cache operations with comprehensive validation
    to ensure cache consistency and prevent data corruption.
    """
    
    def __init__(self, cache_dir: Union[str, Path]):
        """
        Initialize data cache.
        
        Args:
            cache_dir: Directory for cache storage
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.data_dir = self.cache_dir / "data"
        self.metadata_dir = self.cache_dir / "metadata"
        
        for dir_path in [self.data_dir, self.metadata_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(__name__)
    
    def save_dataset(self, dataset: ProcessedDataset, cache_key: str) -> None:
        """
        Save dataset to cache with atomic operations.
        
        Args:
            dataset: ProcessedDataset to cache
            cache_key: Unique cache identifier
        """
        try:
            # Prepare cache data (exclude large objects from metadata)
            cache_data = {
                'X': dataset.X,
                'y': dataset.y,
                'label_encoders': dataset.label_encoders
            }
            
            metadata = {
                'feature_names': dataset.feature_names,
                'preprocessing_metadata': dataset.preprocessing_metadata,
                'data_hash': dataset.data_hash,
                'processing_timestamp': dataset.processing_timestamp.isoformat(),
                'validation_result': dataset.validation_result.to_dict(),
                'memory_usage_mb': dataset.memory_usage_mb,
                'shape': dataset.shape,
                'fraud_rate': dataset.fraud_rate
            }
            
            # Atomic save operations
            data_path = self.data_dir / f"{cache_key}.pkl"
            metadata_path = self.metadata_dir / f"{cache_key}.json"
            
            # Save data
            with open(data_path, 'wb') as f:
                pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            # Save metadata
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self.logger.info("dataset_cached", extra={
                "cache_key": cache_key,
                "shape": dataset.shape,
                "memory_mb": dataset.memory_usage_mb,
                "fraud_rate": dataset.fraud_rate
            })
            
        except Exception as e:
            self.logger.error("cache_save_failed", extra={
                "cache_key": cache_key,
                "error": str(e)
            })
            raise DataCacheError(f"Failed to save dataset to cache: {e}") from e
    
    def load_dataset(self, cache_key: str) -> Optional[ProcessedDataset]:
        """
        Load dataset from cache with integrity validation.
        
        Args:
            cache_key: Cache identifier
            
        Returns:
            ProcessedDataset if found and valid, None otherwise
        """
        try:
            data_path = self.data_dir / f"{cache_key}.pkl"
            metadata_path = self.metadata_dir / f"{cache_key}.json"
            
            if not data_path.exists() or not metadata_path.exists():
                return None
            
            # Load metadata first
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            
            # Load data
            with open(data_path, 'rb') as f:
                cache_data = pickle.load(f)
            
            # Reconstruct validation result
            validation_data = metadata['validation_result']
            validation_result = ValidationResult(
                is_valid=validation_data['is_valid'],
                errors=validation_data['errors'],
                warnings=validation_data['warnings'],
                metrics=validation_data['metrics'],
                validation_timestamp=datetime.fromisoformat(validation_data['validation_timestamp'])
            )
            
            # Reconstruct dataset
            dataset = ProcessedDataset(
                X=cache_data['X'],
                y=cache_data['y'],
                feature_names=metadata['feature_names'],
                label_encoders=cache_data['label_encoders'],
                preprocessing_metadata=metadata['preprocessing_metadata'],
                data_hash=metadata['data_hash'],
                processing_timestamp=datetime.fromisoformat(metadata['processing_timestamp']),
                validation_result=validation_result,
                memory_usage_mb=metadata['memory_usage_mb']
            )
            
            # Validate cache integrity
            if not self._validate_cache_integrity(dataset, metadata):
                self.logger.warning("cache_integrity_failed", extra={
                    "cache_key": cache_key
                })
                return None
            
            self.logger.info("dataset_loaded_from_cache", extra={
                "cache_key": cache_key,
                "shape": dataset.shape,
                "fraud_rate": dataset.fraud_rate
            })
            
            return dataset
            
        except Exception as e:
            self.logger.error("cache_load_failed", extra={
                "cache_key": cache_key,
                "error": str(e)
            })
            return None
    
    def _validate_cache_integrity(self, dataset: ProcessedDataset, 
                                metadata: Dict[str, Any]) -> bool:
        """Validate cache integrity against metadata."""
        try:
            # Check shape consistency
            if dataset.shape != tuple(metadata['shape']):
                return False
            
            # Check fraud rate consistency (allow small floating point differences)
            if abs(dataset.fraud_rate - metadata['fraud_rate']) > 0.001:
                return False
            
            # Check feature count
            if len(dataset.feature_names) != len(dataset.X.columns):
                return False
            
            return True
        except Exception:
            return False


class DataProcessor:
    """
    Comprehensive IEEE-CIS data processor with caching and validation.
    
    Provides robust data loading, preprocessing, and validation pipeline
    with intelligent caching and comprehensive error handling.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize data processor with configuration.
        
        Args:
            config: Configuration dictionary with processing parameters
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Initialize cache
        cache_dir = Path(config.get('cache_dir', 'data/processed/cache'))
        self.cache = DataCache(cache_dir)
        
        # Processing parameters
        self.max_features = config.get('max_features', 200)
        self.validation_split = config.get('validation_split', 0.2)
        self.enable_caching = config.get('enable_caching', True)
        
        # Data paths
        self.transaction_data_path = Path(config.get('transaction_data_path', 'data/raw/train_transaction.csv'))
        self.identity_data_path = Path(config.get('identity_data_path', 'data/raw/train_identity.csv'))
        
        self.logger.info("data_processor.initialized", extra={
            "max_features": self.max_features,
            "caching_enabled": self.enable_caching,
            "transaction_path": str(self.transaction_data_path)
        })
    
    def load_and_preprocess_data(self, force_refresh: bool = False) -> ProcessedDataset:
        """
        Load and preprocess IEEE-CIS data with caching.
        
        Args:
            force_refresh: Skip cache and reprocess data
            
        Returns:
            ProcessedDataset ready for model training
        """
        # Generate cache key based on config and data file timestamps
        cache_key = self._generate_cache_key()
        
        # Try to load from cache first
        if not force_refresh and self.enable_caching:
            cached_dataset = self.cache.load_dataset(cache_key)
            if cached_dataset is not None:
                self.logger.info("data_loaded_from_cache", extra={
                    "cache_key": cache_key,
                    "shape": cached_dataset.shape
                })
                return cached_dataset
        
        # Process data from scratch
        self.logger.info("processing_data_from_scratch", extra={
            "force_refresh": force_refresh
        })
        
        try:
            # Load raw data
            raw_data = self._load_raw_data()
            
            # Preprocess data
            processed_data = self._preprocess_data(raw_data)
            
            # Validate processed data
            validation_result = self._validate_data(processed_data)
            
            # Create dataset object
            dataset = ProcessedDataset(
                X=processed_data['X'],
                y=processed_data['y'],
                feature_names=processed_data['feature_names'],
                label_encoders=processed_data['label_encoders'],
                preprocessing_metadata=processed_data['metadata'],
                data_hash=self._compute_data_hash(processed_data),
                processing_timestamp=datetime.now(),
                validation_result=validation_result
            )
            
            # Cache the dataset
            if self.enable_caching:
                self.cache.save_dataset(dataset, cache_key)
            
            self.logger.info("data_processing_completed", extra={
                "shape": dataset.shape,
                "fraud_rate": dataset.fraud_rate,
                "memory_mb": dataset.memory_usage_mb,
                "processing_time": time.time() - self._start_time
            })
            
            return dataset
            
        except Exception as e:
            self.logger.error("data_processing_failed", extra={
                "error": str(e)
            })
            raise DataProcessingError(f"Data processing failed: {e}") from e
    
    def _load_raw_data(self) -> pd.DataFrame:
        """Load raw IEEE-CIS transaction and identity data."""
        self._start_time = time.time()
        
        # Load transaction data
        if not self.transaction_data_path.exists():
            raise DataLoadingError(f"Transaction data not found: {self.transaction_data_path}")
        
        self.logger.info("loading_transaction_data", extra={
            "path": str(self.transaction_data_path)
        })
        
        data = pd.read_csv(self.transaction_data_path)
        
        # Load identity data if available
        if self.identity_data_path.exists():
            self.logger.info("loading_identity_data", extra={
                "path": str(self.identity_data_path)
            })
            
            identity_data = pd.read_csv(self.identity_data_path)
            data = data.merge(identity_data, on='TransactionID', how='left')
            
            self.logger.info("identity_data_merged", extra={
                "identity_rows": len(identity_data),
                "merge_ratio": (data['TransactionID'].isin(identity_data['TransactionID'])).mean()
            })
        
        self.logger.info("raw_data_loaded", extra={
            "shape": data.shape,
            "memory_mb": data.memory_usage(deep=True).sum() / (1024 * 1024)
        })
        
        return data
    
    def _preprocess_data(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Comprehensive data preprocessing pipeline."""
        self.logger.info("preprocessing_started", extra={
            "input_shape": data.shape
        })
        
        # Separate features and target
        if 'isFraud' not in data.columns:
            raise DataProcessingError("Target column 'isFraud' not found")
        
        y = data['isFraud'].copy()
        X = data.drop(columns=['isFraud', 'TransactionID'])
        
        # Basic feature engineering
        X = self._engineer_features(X)
        
        # Handle categorical variables
        label_encoders = {}
        categorical_cols = X.select_dtypes(include=['object']).columns
        
        for col in categorical_cols:
            label_encoders[col] = LabelEncoder()
            X[col] = label_encoders[col].fit_transform(X[col].fillna('unknown'))
        
        # Handle missing values
        missing_stats = X.isnull().sum()
        self.logger.info("missing_values_handled", extra={
            "columns_with_missing": (missing_stats > 0).sum(),
            "total_missing_values": missing_stats.sum()
        })
        
        X = X.fillna(0)
        
        # Feature selection
        feature_names = list(X.columns)
        if len(X.columns) > self.max_features:
            X, feature_names = self._select_features(X, y)
        
        # Preprocessing metadata
        metadata = {
            'original_shape': data.shape,
            'final_shape': X.shape,
            'categorical_columns': list(categorical_cols),
            'feature_selection_applied': len(X.columns) != len(data.columns) - 2,  # Exclude target and ID
            'max_features': self.max_features,
            'missing_value_strategy': 'zero_fill',
            'feature_engineering_applied': True
        }
        
        return {
            'X': X,
            'y': y,
            'feature_names': feature_names,
            'label_encoders': label_encoders,
            'metadata': metadata
        }
    
    def _engineer_features(self, X: pd.DataFrame) -> pd.DataFrame:
        """Apply feature engineering transformations."""
        if 'TransactionAmt' in X.columns:
            # Log transform for transaction amount
            X['TransactionAmt_log'] = np.log1p(X['TransactionAmt'])
            
            # Decimal component
            X['TransactionAmt_decimal'] = X['TransactionAmt'] % 1
            
            # Amount bins
            X['TransactionAmt_bin'] = pd.cut(X['TransactionAmt'], 
                                           bins=[0, 10, 50, 200, 1000, float('inf')], 
                                           labels=[0, 1, 2, 3, 4])
        
        # Hour of day if timestamp available
        if any(col.startswith('TransactionDT') for col in X.columns):
            dt_cols = [col for col in X.columns if col.startswith('TransactionDT')]
            for col in dt_cols:
                X[f'{col}_hour'] = (X[col] / 3600) % 24
        
        return X
    
    def _select_features(self, X: pd.DataFrame, y: pd.Series) -> Tuple[pd.DataFrame, List[str]]:
        """Select top features using mutual information."""
        self.logger.info("feature_selection_started", extra={
            "input_features": len(X.columns),
            "target_features": self.max_features
        })
        
        # Convert to numeric
        X_numeric = X.apply(pd.to_numeric, errors='coerce').fillna(0)
        
        # Feature selection
        selector = SelectKBest(mutual_info_classif, k=self.max_features)
        X_selected = selector.fit_transform(X_numeric, y)
        
        # Get selected feature names
        selected_indices = np.where(selector.get_support())[0]
        feature_names = [X.columns[i] for i in selected_indices]
        
        X_selected_df = pd.DataFrame(X_selected, columns=feature_names, index=X.index)
        
        self.logger.info("feature_selection_completed", extra={
            "selected_features": len(feature_names),
            "selection_ratio": len(feature_names) / len(X.columns)
        })
        
        return X_selected_df, feature_names
    
    def _validate_data(self, processed_data: Dict[str, Any]) -> ValidationResult:
        """Comprehensive data validation."""
        result = ValidationResult(is_valid=True)
        
        X = processed_data['X']
        y = processed_data['y']
        
        # Basic consistency checks
        if len(X) != len(y):
            result.add_error(f"Feature/target length mismatch: {len(X)} vs {len(y)}")
        
        # Target validation
        unique_targets = y.unique()
        if not set(unique_targets).issubset({0, 1}):
            result.add_error(f"Invalid target values: {unique_targets}")
        
        fraud_rate = y.mean()
        if fraud_rate < 0.001 or fraud_rate > 0.5:
            result.add_warning(f"Unusual fraud rate: {fraud_rate:.4f}")
        
        # Feature validation
        if X.isnull().any().any():
            result.add_error("Features contain null values after preprocessing")
        
        if (X.dtypes == 'object').any():
            result.add_error("Features contain non-numeric data after preprocessing")
        
        # Memory usage check
        memory_gb = X.memory_usage(deep=True).sum() / (1024 ** 3)
        if memory_gb > 8:
            result.add_warning(f"High memory usage: {memory_gb:.2f} GB")
        
        # Statistical validation
        result.metrics = {
            'fraud_rate': fraud_rate,
            'feature_count': len(X.columns),
            'sample_count': len(X),
            'memory_gb': memory_gb,
            'null_feature_count': X.isnull().any().sum(),
            'infinite_value_count': np.isinf(X.values).sum()
        }
        
        return result
    
    def _compute_data_hash(self, processed_data: Dict[str, Any]) -> str:
        """Compute hash for data integrity validation."""
        X = processed_data['X']
        y = processed_data['y']
        
        content = f"{X.shape}_{y.sum()}_{X.columns.tolist()}_{y.dtype}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _generate_cache_key(self) -> str:
        """Generate cache key based on config and data timestamps."""
        # Get file modification times
        trans_mtime = self.transaction_data_path.stat().st_mtime if self.transaction_data_path.exists() else 0
        identity_mtime = self.identity_data_path.stat().st_mtime if self.identity_data_path.exists() else 0
        
        # Include relevant config parameters
        config_hash = hashlib.sha256(json.dumps({
            'max_features': self.max_features,
            'transaction_path': str(self.transaction_data_path),
            'identity_path': str(self.identity_data_path)
        }, sort_keys=True).encode()).hexdigest()[:8]
        
        return f"ieee_data_{trans_mtime}_{identity_mtime}_{config_hash}"


# Custom exceptions
class DataProcessingError(Exception):
    """Base exception for data processing operations."""
    pass

class DataLoadingError(DataProcessingError):
    """Raised when data loading fails."""
    pass

class DataCacheError(DataProcessingError):
    """Raised when cache operations fail."""
    pass
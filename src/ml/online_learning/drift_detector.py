# /stream-sentinel/src/ml/online_learning/drift_detector.py

"""
Model Drift Detection System for Online Learning

This module implements comprehensive drift detection to monitor model performance
degradation and data distribution changes. It uses multiple statistical tests
and performance metrics to detect when models need retraining.

Key features:
- Statistical drift detection (KS test, PSI, Chi-square)
- Performance-based drift monitoring
- Feature-level drift analysis
- Concept drift detection
- Automated alerting and retraining triggers
"""

import json
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict, field
from enum import Enum
from collections import deque, defaultdict
import redis
from confluent_kafka import Producer
from scipy import stats
from scipy.stats import ks_2samp, chi2_contingency
import warnings
warnings.filterwarnings('ignore')

from .config import OnlineLearningConfig, get_online_learning_config


class DriftType(Enum):
    """Types of drift detection."""
    DATA_DRIFT = "data_drift"
    CONCEPT_DRIFT = "concept_drift" 
    PERFORMANCE_DRIFT = "performance_drift"
    FEATURE_DRIFT = "feature_drift"


class DriftSeverity(Enum):
    """Severity levels for detected drift."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class DriftAlert:
    """Alert for detected model drift."""
    alert_id: str
    drift_type: DriftType
    severity: DriftSeverity
    confidence: float
    timestamp: str
    affected_features: List[str]
    
    # Statistical measures
    drift_score: float
    p_value: float
    test_statistic: float
    
    # Metadata
    reference_period: str
    current_period: str
    samples_analyzed: int
    
    # Recommendations
    requires_retraining: bool
    suggested_actions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary for serialization."""
        alert_dict = asdict(self)
        alert_dict["drift_type"] = self.drift_type.value
        alert_dict["severity"] = self.severity.value
        return alert_dict


@dataclass
class PerformanceMetrics:
    """Performance metrics for drift detection."""
    timestamp: str
    auc_score: float
    precision: float
    recall: float
    f1_score: float
    accuracy: float
    false_positive_rate: float
    false_negative_rate: float
    
    # Volume metrics
    total_predictions: int
    fraud_predictions: int
    fraud_rate: float
    
    # Latency metrics
    avg_prediction_time_ms: float
    p95_prediction_time_ms: float


class DriftDetector:
    """
    Comprehensive drift detection system for fraud detection models.
    
    This class monitors multiple types of drift:
    1. Data drift - changes in input feature distributions
    2. Concept drift - changes in the relationship between features and target
    3. Performance drift - degradation in model performance metrics
    4. Feature drift - individual feature distribution changes
    """
    
    def __init__(self, config: Optional[OnlineLearningConfig] = None):
        self.config = config or get_online_learning_config()
        self.logger = logging.getLogger(__name__)
        
        # Initialize Redis and Kafka
        self._init_redis()
        self._init_kafka()
        
        # Reference data for drift detection
        self.reference_data: Optional[pd.DataFrame] = None
        self.reference_labels: Optional[np.ndarray] = None
        self.reference_features: List[str] = []
        
        # Current window data
        self.current_window = deque(maxlen=self.config.drift_detection.performance_window_size)
        self.performance_history = deque(maxlen=1000)  # Keep last 1000 performance measurements
        
        # Feature statistics cache
        self.feature_stats_cache = {}
        self.baseline_feature_stats = {}
        
        # Drift detection state
        self.drift_alerts = []
        self.last_drift_check = None
        self.drift_detection_stats = {
            "total_checks": 0,
            "drift_alerts_triggered": 0,
            "false_positives": 0,
            "retraining_triggered": 0
        }
        
        self.logger.info("DriftDetector initialized successfully")
    
    def _init_redis(self) -> None:
        """Initialize Redis connection for drift data storage."""
        try:
            self.redis_drift = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db_drift,
                decode_responses=True
            )
            self.redis_drift.ping()
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis connection: {e}")
            raise
    
    def _init_kafka(self) -> None:
        """Initialize Kafka producer for drift alerts."""
        try:
            producer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'linger.ms': 5,
                'compression.type': 'lz4'
            }
            self.producer = Producer(producer_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka producer: {e}")
            raise
    
    def set_reference_data(self, X: pd.DataFrame, y: np.ndarray) -> None:
        """
        Set reference dataset for drift detection.
        
        Args:
            X: Reference feature matrix
            y: Reference target labels
        """
        self.reference_data = X.copy()
        self.reference_labels = y.copy()
        self.reference_features = list(X.columns)
        
        # Calculate baseline feature statistics
        self._calculate_baseline_statistics()
        
        # Store in Redis for persistence
        self._store_reference_data()
        
        self.logger.info(f"Reference data set with {len(X)} samples and {len(X.columns)} features")
    
    def _calculate_baseline_statistics(self) -> None:
        """Calculate baseline statistics for all features."""
        if self.reference_data is None:
            return
        
        self.baseline_feature_stats = {}
        
        for feature in self.reference_features:
            feature_data = self.reference_data[feature].dropna()
            
            if pd.api.types.is_numeric_dtype(feature_data):
                # Numerical feature statistics
                stats_dict = {
                    'type': 'numerical',
                    'mean': float(feature_data.mean()),
                    'std': float(feature_data.std()),
                    'min': float(feature_data.min()),
                    'max': float(feature_data.max()),
                    'median': float(feature_data.median()),
                    'q25': float(feature_data.quantile(0.25)),
                    'q75': float(feature_data.quantile(0.75)),
                    'skewness': float(feature_data.skew()),
                    'kurtosis': float(feature_data.kurtosis())
                }
            else:
                # Categorical feature statistics
                value_counts = feature_data.value_counts()
                stats_dict = {
                    'type': 'categorical',
                    'unique_values': len(value_counts),
                    'most_common': value_counts.head(10).to_dict(),
                    'distribution': (value_counts / len(feature_data)).to_dict()
                }
            
            self.baseline_feature_stats[feature] = stats_dict
    
    def _store_reference_data(self) -> None:
        """Store reference data and statistics in Redis."""
        try:
            # Store feature statistics
            stats_key = "drift_baseline_stats"
            self.redis_drift.set(stats_key, json.dumps(self.baseline_feature_stats))
            
            # Store reference data summary
            reference_summary = {
                'feature_count': len(self.reference_features),
                'sample_count': len(self.reference_data),
                'features': self.reference_features,
                'fraud_rate': float(self.reference_labels.mean()),
                'timestamp': datetime.now().isoformat()
            }
            
            summary_key = "drift_reference_summary"
            self.redis_drift.set(summary_key, json.dumps(reference_summary))
            
        except Exception as e:
            self.logger.error(f"Failed to store reference data: {e}")
    
    def add_prediction_sample(self, features: Dict[str, Any], prediction: float, 
                            actual_label: Optional[int] = None) -> None:
        """
        Add a new prediction sample for drift monitoring.
        
        Args:
            features: Feature dictionary for the prediction
            prediction: Model prediction (probability)
            actual_label: True label if available (for performance drift)
        """
        sample = {
            'timestamp': datetime.now().isoformat(),
            'features': features,
            'prediction': prediction,
            'actual_label': actual_label
        }
        
        self.current_window.append(sample)
        
        # Store in Redis with expiration
        sample_key = f"drift_sample:{int(time.time() * 1000)}"
        self.redis_drift.setex(sample_key, 86400, json.dumps(sample))
    
    def add_performance_metrics(self, metrics: PerformanceMetrics) -> None:
        """
        Add performance metrics for performance drift detection.
        
        Args:
            metrics: Performance metrics object
        """
        self.performance_history.append(metrics)
        
        # Store in Redis
        metrics_key = f"performance_metrics:{metrics.timestamp}"
        self.redis_drift.setex(metrics_key, 604800, json.dumps(asdict(metrics)))
        
        self.logger.debug(f"Added performance metrics: AUC={metrics.auc_score:.4f}")
    
    def detect_drift(self, force_check: bool = False) -> List[DriftAlert]:
        """
        Run comprehensive drift detection analysis.
        
        Args:
            force_check: Force drift check even if minimum samples not met
            
        Returns:
            List of drift alerts if drift is detected
        """
        if not self._should_run_drift_check() and not force_check:
            return []
        
        alerts = []
        self.drift_detection_stats["total_checks"] += 1
        
        try:
            # 1. Data drift detection
            data_drift_alerts = self._detect_data_drift()
            alerts.extend(data_drift_alerts)
            
            # 2. Performance drift detection
            performance_drift_alerts = self._detect_performance_drift()
            alerts.extend(performance_drift_alerts)
            
            # 3. Feature-level drift detection
            feature_drift_alerts = self._detect_feature_drift()
            alerts.extend(feature_drift_alerts)
            
            # 4. Concept drift detection
            concept_drift_alerts = self._detect_concept_drift()
            alerts.extend(concept_drift_alerts)
            
            # Process and store alerts
            if alerts:
                self._process_drift_alerts(alerts)
                self.drift_detection_stats["drift_alerts_triggered"] += len(alerts)
            
            self.last_drift_check = datetime.now()
            
        except Exception as e:
            self.logger.error(f"Drift detection failed: {e}")
        
        return alerts
    
    def _should_run_drift_check(self) -> bool:
        """Determine if drift check should be run."""
        # Check if enough samples accumulated
        if len(self.current_window) < self.config.drift_detection.min_current_samples:
            return False
        
        # Check if enough time passed since last check
        if self.last_drift_check is not None:
            time_since_last = datetime.now() - self.last_drift_check
            if time_since_last.total_seconds() < 3600:  # At least 1 hour between checks
                return False
        
        return True
    
    def _detect_data_drift(self) -> List[DriftAlert]:
        """Detect overall data distribution drift."""
        if self.reference_data is None or len(self.current_window) == 0:
            return []
        
        alerts = []
        
        try:
            # Convert current window to DataFrame
            current_features = []
            for sample in self.current_window:
                current_features.append(sample['features'])
            
            current_df = pd.DataFrame(current_features)
            
            # Ensure same features
            common_features = set(self.reference_features) & set(current_df.columns)
            if len(common_features) < len(self.reference_features) * 0.8:
                self.logger.warning("Insufficient feature overlap for drift detection")
                return []
            
            # Population Stability Index (PSI) for overall drift
            psi_score = self._calculate_population_stability_index(
                self.reference_data[list(common_features)],
                current_df[list(common_features)]
            )
            
            if psi_score > self.config.drift_detection.drift_threshold:
                severity = self._determine_drift_severity(psi_score, "psi")
                
                alert = DriftAlert(
                    alert_id=f"data_drift_{int(time.time())}",
                    drift_type=DriftType.DATA_DRIFT,
                    severity=severity,
                    confidence=min(1.0, psi_score / 0.5),
                    timestamp=datetime.now().isoformat(),
                    affected_features=list(common_features),
                    drift_score=psi_score,
                    p_value=0.0,  # PSI doesn't have p-value
                    test_statistic=psi_score,
                    reference_period=f"{len(self.reference_data)} samples",
                    current_period=f"{len(current_df)} samples",
                    samples_analyzed=len(current_df),
                    requires_retraining=psi_score > 0.25,
                    suggested_actions=self._get_drift_recommendations(DriftType.DATA_DRIFT, psi_score)
                )
                
                alerts.append(alert)
                self.logger.warning(f"Data drift detected: PSI={psi_score:.4f}")
        
        except Exception as e:
            self.logger.error(f"Data drift detection failed: {e}")
        
        return alerts
    
    def _detect_performance_drift(self) -> List[DriftAlert]:
        """Detect performance degradation drift."""
        if len(self.performance_history) < 10:  # Need minimum history
            return []
        
        alerts = []
        
        try:
            # Get baseline performance (first 20% of data)
            baseline_size = max(2, len(self.performance_history) // 5)
            baseline_metrics = list(self.performance_history)[:baseline_size]
            recent_metrics = list(self.performance_history)[-baseline_size:]
            
            # Compare AUC scores
            baseline_auc = np.mean([m.auc_score for m in baseline_metrics])
            recent_auc = np.mean([m.auc_score for m in recent_metrics])
            
            auc_degradation = baseline_auc - recent_auc
            
            if auc_degradation > self.config.drift_detection.performance_threshold:
                # Statistical test for significance
                baseline_scores = [m.auc_score for m in baseline_metrics]
                recent_scores = [m.auc_score for m in recent_metrics]
                
                t_stat, p_value = stats.ttest_ind(baseline_scores, recent_scores)
                
                severity = self._determine_drift_severity(auc_degradation, "performance")
                
                alert = DriftAlert(
                    alert_id=f"performance_drift_{int(time.time())}",
                    drift_type=DriftType.PERFORMANCE_DRIFT,
                    severity=severity,
                    confidence=1.0 - p_value if p_value < 0.05 else 0.5,
                    timestamp=datetime.now().isoformat(),
                    affected_features=["model_performance"],
                    drift_score=auc_degradation,
                    p_value=p_value,
                    test_statistic=abs(t_stat),
                    reference_period=f"Baseline AUC: {baseline_auc:.4f}",
                    current_period=f"Recent AUC: {recent_auc:.4f}",
                    samples_analyzed=len(recent_metrics),
                    requires_retraining=auc_degradation > 0.1,
                    suggested_actions=self._get_drift_recommendations(DriftType.PERFORMANCE_DRIFT, auc_degradation)
                )
                
                alerts.append(alert)
                self.logger.warning(f"Performance drift detected: AUC degradation={auc_degradation:.4f}")
        
        except Exception as e:
            self.logger.error(f"Performance drift detection failed: {e}")
        
        return alerts
    
    def _detect_feature_drift(self) -> List[DriftAlert]:
        """Detect drift in individual features."""
        if self.reference_data is None or len(self.current_window) == 0:
            return []
        
        alerts = []
        
        try:
            # Convert current window to DataFrame
            current_features = []
            for sample in self.current_window:
                current_features.append(sample['features'])
            
            current_df = pd.DataFrame(current_features)
            
            # Check each feature individually
            for feature in self.reference_features:
                if feature not in current_df.columns:
                    continue
                
                drift_result = self._detect_single_feature_drift(feature, current_df[feature])
                
                if drift_result['is_drift']:
                    severity = self._determine_drift_severity(drift_result['drift_score'], "feature")
                    
                    alert = DriftAlert(
                        alert_id=f"feature_drift_{feature}_{int(time.time())}",
                        drift_type=DriftType.FEATURE_DRIFT,
                        severity=severity,
                        confidence=drift_result['confidence'],
                        timestamp=datetime.now().isoformat(),
                        affected_features=[feature],
                        drift_score=drift_result['drift_score'],
                        p_value=drift_result['p_value'],
                        test_statistic=drift_result['test_statistic'],
                        reference_period=f"Reference samples",
                        current_period=f"Current window",
                        samples_analyzed=len(current_df),
                        requires_retraining=drift_result['drift_score'] > 0.3,
                        suggested_actions=self._get_drift_recommendations(DriftType.FEATURE_DRIFT, drift_result['drift_score'])
                    )
                    
                    alerts.append(alert)
                    self.logger.warning(f"Feature drift detected in {feature}: score={drift_result['drift_score']:.4f}")
        
        except Exception as e:
            self.logger.error(f"Feature drift detection failed: {e}")
        
        return alerts
    
    def _detect_concept_drift(self) -> List[DriftAlert]:
        """Detect concept drift (changes in target distribution)."""
        if len(self.current_window) == 0:
            return []
        
        alerts = []
        
        try:
            # Get predictions and labels from current window
            predictions = []
            labels = []
            
            for sample in self.current_window:
                if sample.get('actual_label') is not None:
                    predictions.append(sample['prediction'])
                    labels.append(sample['actual_label'])
            
            if len(predictions) < 50:  # Need minimum labeled samples
                return []
            
            # Compare current fraud rate with reference
            current_fraud_rate = np.mean(labels)
            reference_fraud_rate = self.reference_labels.mean()
            
            rate_difference = abs(current_fraud_rate - reference_fraud_rate)
            
            # Statistical test for difference in proportions
            n1, n2 = len(self.reference_labels), len(labels)
            x1, x2 = self.reference_labels.sum(), sum(labels)
            
            # Two-proportion z-test
            p_combined = (x1 + x2) / (n1 + n2)
            se = np.sqrt(p_combined * (1 - p_combined) * (1/n1 + 1/n2))
            
            if se > 0:
                z_stat = abs(reference_fraud_rate - current_fraud_rate) / se
                p_value = 2 * (1 - stats.norm.cdf(abs(z_stat)))
            else:
                z_stat, p_value = 0, 1
            
            if rate_difference > 0.01 and p_value < 0.05:  # 1% difference threshold
                severity = self._determine_drift_severity(rate_difference, "concept")
                
                alert = DriftAlert(
                    alert_id=f"concept_drift_{int(time.time())}",
                    drift_type=DriftType.CONCEPT_DRIFT,
                    severity=severity,
                    confidence=1.0 - p_value,
                    timestamp=datetime.now().isoformat(),
                    affected_features=["target_distribution"],
                    drift_score=rate_difference,
                    p_value=p_value,
                    test_statistic=z_stat,
                    reference_period=f"Reference fraud rate: {reference_fraud_rate:.4f}",
                    current_period=f"Current fraud rate: {current_fraud_rate:.4f}",
                    samples_analyzed=len(labels),
                    requires_retraining=rate_difference > 0.02,
                    suggested_actions=self._get_drift_recommendations(DriftType.CONCEPT_DRIFT, rate_difference)
                )
                
                alerts.append(alert)
                self.logger.warning(f"Concept drift detected: fraud rate change={rate_difference:.4f}")
        
        except Exception as e:
            self.logger.error(f"Concept drift detection failed: {e}")
        
        return alerts
    
    def _detect_single_feature_drift(self, feature: str, current_data: pd.Series) -> Dict[str, Any]:
        """Detect drift for a single feature."""
        reference_data = self.reference_data[feature].dropna()
        current_data = current_data.dropna()
        
        if len(current_data) < 30:  # Minimum samples
            return {'is_drift': False, 'drift_score': 0.0, 'confidence': 0.0, 'p_value': 1.0, 'test_statistic': 0.0}
        
        if pd.api.types.is_numeric_dtype(reference_data):
            # Numerical feature - use KS test
            ks_stat, p_value = ks_2samp(reference_data, current_data)
            is_drift = p_value < self.config.drift_detection.drift_threshold
            
            return {
                'is_drift': is_drift,
                'drift_score': ks_stat,
                'confidence': 1.0 - p_value if is_drift else 0.0,
                'p_value': p_value,
                'test_statistic': ks_stat
            }
        
        else:
            # Categorical feature - use Chi-square test
            try:
                # Get value counts
                ref_counts = reference_data.value_counts()
                cur_counts = current_data.value_counts()
                
                # Align indices
                all_values = set(ref_counts.index) | set(cur_counts.index)
                ref_aligned = ref_counts.reindex(all_values, fill_value=0)
                cur_aligned = cur_counts.reindex(all_values, fill_value=0)
                
                # Chi-square test
                chi2_stat, p_value, _, _ = chi2_contingency([ref_aligned, cur_aligned])
                is_drift = p_value < self.config.drift_detection.categorical_drift_threshold
                
                # Normalize chi2 stat for interpretability
                normalized_chi2 = chi2_stat / (len(ref_aligned) + len(cur_aligned))
                
                return {
                    'is_drift': is_drift,
                    'drift_score': normalized_chi2,
                    'confidence': 1.0 - p_value if is_drift else 0.0,
                    'p_value': p_value,
                    'test_statistic': chi2_stat
                }
                
            except Exception:
                # Fallback to simple distribution comparison
                ref_dist = reference_data.value_counts(normalize=True)
                cur_dist = current_data.value_counts(normalize=True)
                
                # Calculate JS divergence as drift score
                drift_score = self._jensen_shannon_divergence(ref_dist, cur_dist)
                is_drift = drift_score > 0.1
                
                return {
                    'is_drift': is_drift,
                    'drift_score': drift_score,
                    'confidence': min(1.0, drift_score * 2),
                    'p_value': 1.0 - min(1.0, drift_score * 2),
                    'test_statistic': drift_score
                }
    
    def _calculate_population_stability_index(self, reference: pd.DataFrame, current: pd.DataFrame) -> float:
        """Calculate Population Stability Index (PSI) between two datasets."""
        if len(reference) == 0 or len(current) == 0:
            return 0.0
        
        psi_values = []
        
        for column in reference.columns:
            if column not in current.columns:
                continue
            
            ref_col = reference[column].dropna()
            cur_col = current[column].dropna()
            
            if len(ref_col) == 0 or len(cur_col) == 0:
                continue
            
            if pd.api.types.is_numeric_dtype(ref_col):
                # For numerical features, create bins
                try:
                    bins = np.histogram_bin_edges(ref_col, bins=10)
                    ref_hist, _ = np.histogram(ref_col, bins=bins)
                    cur_hist, _ = np.histogram(cur_col, bins=bins)
                    
                    # Normalize to probabilities
                    ref_dist = ref_hist / len(ref_col)
                    cur_dist = cur_hist / len(cur_col)
                    
                    # Calculate PSI
                    psi = self._calculate_psi_for_distributions(ref_dist, cur_dist)
                    if not np.isnan(psi) and not np.isinf(psi):
                        psi_values.append(psi)
                        
                except Exception:
                    continue
            else:
                # For categorical features
                try:
                    ref_counts = ref_col.value_counts(normalize=True)
                    cur_counts = cur_col.value_counts(normalize=True)
                    
                    # Align indices
                    all_categories = set(ref_counts.index) | set(cur_counts.index)
                    ref_aligned = ref_counts.reindex(all_categories, fill_value=1e-8)
                    cur_aligned = cur_counts.reindex(all_categories, fill_value=1e-8)
                    
                    psi = self._calculate_psi_for_distributions(ref_aligned.values, cur_aligned.values)
                    if not np.isnan(psi) and not np.isinf(psi):
                        psi_values.append(psi)
                        
                except Exception:
                    continue
        
        return np.mean(psi_values) if psi_values else 0.0
    
    def _calculate_psi_for_distributions(self, reference: np.ndarray, current: np.ndarray) -> float:
        """Calculate PSI for two probability distributions."""
        # Add small epsilon to avoid log(0)
        epsilon = 1e-8
        reference = np.where(reference == 0, epsilon, reference)
        current = np.where(current == 0, epsilon, current)
        
        psi = np.sum((current - reference) * np.log(current / reference))
        return psi
    
    def _jensen_shannon_divergence(self, dist1: pd.Series, dist2: pd.Series) -> float:
        """Calculate Jensen-Shannon divergence between two distributions."""
        # Align distributions
        all_values = set(dist1.index) | set(dist2.index)
        dist1_aligned = dist1.reindex(all_values, fill_value=1e-8)
        dist2_aligned = dist2.reindex(all_values, fill_value=1e-8)
        
        # Normalize
        dist1_aligned = dist1_aligned / dist1_aligned.sum()
        dist2_aligned = dist2_aligned / dist2_aligned.sum()
        
        # Calculate JS divergence
        m = 0.5 * (dist1_aligned + dist2_aligned)
        
        def kl_divergence(p, q):
            return np.sum(p * np.log(p / q))
        
        js = 0.5 * kl_divergence(dist1_aligned, m) + 0.5 * kl_divergence(dist2_aligned, m)
        return np.sqrt(js)
    
    def _determine_drift_severity(self, drift_score: float, drift_method: str) -> DriftSeverity:
        """Determine severity level based on drift score and method."""
        if drift_method == "psi":
            if drift_score < 0.1:
                return DriftSeverity.LOW
            elif drift_score < 0.25:
                return DriftSeverity.MEDIUM
            elif drift_score < 0.5:
                return DriftSeverity.HIGH
            else:
                return DriftSeverity.CRITICAL
        
        elif drift_method == "performance":
            if drift_score < 0.05:
                return DriftSeverity.LOW
            elif drift_score < 0.1:
                return DriftSeverity.MEDIUM
            elif drift_score < 0.2:
                return DriftSeverity.HIGH
            else:
                return DriftSeverity.CRITICAL
        
        elif drift_method in ["feature", "concept"]:
            if drift_score < 0.1:
                return DriftSeverity.LOW
            elif drift_score < 0.2:
                return DriftSeverity.MEDIUM
            elif drift_score < 0.4:
                return DriftSeverity.HIGH
            else:
                return DriftSeverity.CRITICAL
        
        return DriftSeverity.MEDIUM
    
    def _get_drift_recommendations(self, drift_type: DriftType, drift_score: float) -> List[str]:
        """Get recommended actions based on drift type and severity."""
        recommendations = []
        
        if drift_type == DriftType.DATA_DRIFT:
            recommendations.extend([
                "Check for changes in data collection process",
                "Validate feature engineering pipeline", 
                "Consider retraining model with recent data"
            ])
            
            if drift_score > 0.25:
                recommendations.append("Immediate model retraining recommended")
        
        elif drift_type == DriftType.PERFORMANCE_DRIFT:
            recommendations.extend([
                "Investigate model degradation causes",
                "Check for label quality issues",
                "Consider ensemble approach with new model"
            ])
            
            if drift_score > 0.1:
                recommendations.append("Emergency model retraining required")
        
        elif drift_type == DriftType.FEATURE_DRIFT:
            recommendations.extend([
                "Analyze affected features individually",
                "Check upstream data pipeline",
                "Consider feature selection updates"
            ])
        
        elif drift_type == DriftType.CONCEPT_DRIFT:
            recommendations.extend([
                "Analyze changing fraud patterns",
                "Update business rules and thresholds",
                "Retrain model with recent fraud examples"
            ])
        
        return recommendations
    
    def _process_drift_alerts(self, alerts: List[DriftAlert]) -> None:
        """Process and store drift alerts."""
        for alert in alerts:
            # Store alert in Redis
            alert_key = f"drift_alert:{alert.alert_id}"
            self.redis_drift.setex(alert_key, 604800, json.dumps(alert.to_dict()))
            
            # Send alert to Kafka
            try:
                self.producer.produce(
                    self.config.drift_alerts_topic,
                    key=alert.alert_id,
                    value=json.dumps(alert.to_dict()),
                    callback=self._delivery_callback
                )
                self.producer.flush()
                
            except Exception as e:
                self.logger.error(f"Failed to send drift alert to Kafka: {e}")
            
            # Add to local alerts list
            self.drift_alerts.append(alert)
            
            # Trigger retraining if needed
            if alert.requires_retraining and self.config.drift_detection.auto_retrain_on_drift:
                self._trigger_model_retraining(alert)
        
        # Keep only recent alerts in memory
        self.drift_alerts = self.drift_alerts[-100:]
    
    def _trigger_model_retraining(self, alert: DriftAlert) -> None:
        """Trigger model retraining due to drift."""
        try:
            retraining_message = {
                'trigger': 'drift_detection',
                'alert_id': alert.alert_id,
                'drift_type': alert.drift_type.value,
                'severity': alert.severity.value,
                'timestamp': datetime.now().isoformat(),
                'priority': 'high' if alert.severity in [DriftSeverity.HIGH, DriftSeverity.CRITICAL] else 'medium'
            }
            
            self.producer.produce(
                self.config.model_updates_topic,
                key=f"retrain_{alert.alert_id}",
                value=json.dumps(retraining_message),
                callback=self._delivery_callback
            )
            self.producer.flush()
            
            self.drift_detection_stats["retraining_triggered"] += 1
            self.logger.info(f"Triggered model retraining due to {alert.drift_type.value}")
            
        except Exception as e:
            self.logger.error(f"Failed to trigger model retraining: {e}")
    
    def _delivery_callback(self, err, msg):
        """Callback for Kafka message delivery."""
        if err:
            self.logger.error(f"Message delivery failed: {err}")
        else:
            self.logger.debug(f"Message delivered to {msg.topic()} [{msg.partition()}]")
    
    def get_drift_summary(self) -> Dict[str, Any]:
        """Get summary of drift detection results."""
        recent_alerts = [alert for alert in self.drift_alerts 
                        if datetime.fromisoformat(alert.timestamp) > datetime.now() - timedelta(hours=24)]
        
        severity_counts = defaultdict(int)
        type_counts = defaultdict(int)
        
        for alert in recent_alerts:
            severity_counts[alert.severity.value] += 1
            type_counts[alert.drift_type.value] += 1
        
        return {
            'total_alerts_24h': len(recent_alerts),
            'severity_distribution': dict(severity_counts),
            'drift_type_distribution': dict(type_counts),
            'last_check': self.last_drift_check.isoformat() if self.last_drift_check else None,
            'detection_statistics': self.drift_detection_stats.copy(),
            'current_window_size': len(self.current_window),
            'performance_history_size': len(self.performance_history)
        }
    
    def reset_drift_detection(self) -> None:
        """Reset drift detection state (for testing or after major changes)."""
        self.current_window.clear()
        self.performance_history.clear()
        self.drift_alerts.clear()
        self.last_drift_check = None
        
        # Clear Redis data
        for key in self.redis_drift.scan_iter(match="drift_*"):
            self.redis_drift.delete(key)
        
        self.logger.info("Drift detection state reset")
# /stream-sentinel/src/ml/online_learning/ab_test_manager.py

"""
A/B Testing Framework for Online Learning

This module implements a comprehensive A/B testing system for comparing
fraud detection models in production. It handles traffic routing, statistical
analysis, and automated decision making for model comparisons.

Key features:
- Multi-armed bandit optimization
- Statistical significance testing
- Automated traffic allocation
- Performance monitoring and comparison
- Early stopping for clear winners/losers
- Comprehensive experiment reporting
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
import redis
from confluent_kafka import Producer, Consumer
from scipy import stats
from scipy.stats import chi2_contingency
import hashlib
import random

from .config import OnlineLearningConfig, get_online_learning_config


class ExperimentStatus(Enum):
    """A/B test experiment status."""
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED_EARLY = "stopped_early"
    FAILED = "failed"


class VariantType(Enum):
    """Types of model variants."""
    CONTROL = "control"
    TREATMENT = "treatment"
    CHAMPION = "champion"
    CHALLENGER = "challenger"


class DecisionResult(Enum):
    """A/B test decision results."""
    INCONCLUSIVE = "inconclusive"
    TREATMENT_WINS = "treatment_wins"
    CONTROL_WINS = "control_wins"
    NO_SIGNIFICANT_DIFFERENCE = "no_significant_difference"


@dataclass
class ModelVariant:
    """Model variant in A/B test."""
    variant_id: str
    model_id: str
    model_version: str
    variant_type: VariantType
    traffic_allocation: float
    
    # Performance tracking
    total_predictions: int = 0
    fraud_predictions: int = 0
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0
    
    # Metrics
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    auc_score: float = 0.0
    false_positive_rate: float = 0.0
    
    # Business metrics
    fraud_detected: int = 0
    fraud_missed: int = 0
    false_alarms: int = 0
    total_fraud_value_detected: float = 0.0
    total_fraud_value_missed: float = 0.0
    
    def calculate_metrics(self) -> None:
        """Calculate performance metrics from confusion matrix."""
        if self.total_predictions == 0:
            return
        
        tp, fp = self.true_positives, self.false_positives
        tn, fn = self.true_negatives, self.false_negatives
        
        # Calculate metrics with safe division
        self.precision = tp / max(1, tp + fp)
        self.recall = tp / max(1, tp + fn)
        self.f1_score = (2 * self.precision * self.recall) / max(1e-8, self.precision + self.recall)
        self.false_positive_rate = fp / max(1, fp + tn)
        
        # Business metrics
        self.fraud_detected = tp
        self.fraud_missed = fn
        self.false_alarms = fp


@dataclass
class ABTestExperiment:
    """A/B test experiment configuration and state."""
    experiment_id: str
    name: str
    description: str
    hypothesis: str
    
    # Experiment configuration
    variants: List[ModelVariant]
    primary_metric: str  # "precision", "recall", "f1", "auc", "business_value"
    minimum_effect_size: float
    significance_level: float
    statistical_power: float
    
    # Experiment lifecycle
    status: ExperimentStatus
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    
    # Traffic and sampling
    total_traffic_percentage: float = 100.0
    target_sample_size: int = 1000
    current_sample_size: int = 0
    
    # Results
    winner: Optional[str] = None
    confidence_level: float = 0.0
    p_value: float = 1.0
    effect_size: float = 0.0
    decision_result: DecisionResult = DecisionResult.INCONCLUSIVE
    
    # Early stopping
    early_stopping_enabled: bool = True
    early_stopping_threshold: float = 0.95  # Confidence threshold for early stopping
    futility_threshold: float = 0.1  # Stop if effect size clearly too small
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        exp_dict = asdict(self)
        exp_dict["status"] = self.status.value
        exp_dict["decision_result"] = self.decision_result.value
        exp_dict["variants"] = [
            {**asdict(v), "variant_type": v.variant_type.value} 
            for v in self.variants
        ]
        return exp_dict


@dataclass
class TrafficAssignment:
    """Traffic assignment for A/B testing."""
    user_id: str
    experiment_id: str
    variant_id: str
    assignment_timestamp: str
    assignment_hash: str
    
    # Context
    user_segment: Optional[str] = None
    geographic_region: Optional[str] = None
    device_type: Optional[str] = None


class ABTestManager:
    """
    Comprehensive A/B testing manager for fraud detection models.
    
    This class provides:
    1. Experiment design and configuration
    2. Traffic routing and assignment
    3. Real-time performance monitoring
    4. Statistical analysis and significance testing
    5. Automated decision making
    6. Early stopping for efficiency
    """
    
    def __init__(self, config: Optional[OnlineLearningConfig] = None):
        self.config = config or get_online_learning_config()
        self.logger = logging.getLogger(__name__)
        
        # Initialize connections
        self._init_redis()
        self._init_kafka()
        
        # Experiment management
        self.active_experiments: Dict[str, ABTestExperiment] = {}
        self.traffic_assignments: Dict[str, TrafficAssignment] = {}
        
        # Statistical analysis
        self.statistical_cache = {}
        self.analysis_history = []
        
        # Load existing experiments
        self._load_active_experiments()
        
        self.logger.info("ABTestManager initialized successfully")
    
    def _init_redis(self) -> None:
        """Initialize Redis connections."""
        try:
            self.redis_ab_tests = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db_ab_tests,
                decode_responses=True
            )
            self.redis_ab_tests.ping()
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis: {e}")
            raise
    
    def _init_kafka(self) -> None:
        """Initialize Kafka producer and consumer."""
        try:
            # Producer for A/B test assignments
            producer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'linger.ms': 5,
                'compression.type': 'lz4'
            }
            self.producer = Producer(producer_config)
            
            # Consumer for prediction results
            consumer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'group.id': 'ab-test-manager',
                'auto.offset.reset': 'latest',
                'enable.auto.commit': True
            }
            self.consumer = Consumer(consumer_config)
            self.consumer.subscribe(["fraud-predictions", "fraud-feedback"])
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def _load_active_experiments(self) -> None:
        """Load active experiments from storage."""
        try:
            experiment_keys = self.redis_ab_tests.keys("experiment:*")
            
            for key in experiment_keys:
                experiment_data = self.redis_ab_tests.get(key)
                if experiment_data:
                    exp_dict = json.loads(experiment_data)
                    
                    # Reconstruct experiment object
                    experiment = ABTestExperiment(**exp_dict)
                    experiment.status = ExperimentStatus(exp_dict["status"])
                    experiment.decision_result = DecisionResult(exp_dict["decision_result"])
                    
                    # Reconstruct variants
                    variants = []
                    for var_dict in exp_dict["variants"]:
                        variant = ModelVariant(**var_dict)
                        variant.variant_type = VariantType(var_dict["variant_type"])
                        variants.append(variant)
                    
                    experiment.variants = variants
                    
                    if experiment.status == ExperimentStatus.RUNNING:
                        self.active_experiments[experiment.experiment_id] = experiment
            
            self.logger.info(f"Loaded {len(self.active_experiments)} active experiments")
            
        except Exception as e:
            self.logger.error(f"Failed to load active experiments: {e}")
    
    def create_experiment(self, name: str, description: str, hypothesis: str,
                         control_model: Tuple[str, str], treatment_model: Tuple[str, str],
                         traffic_split: Tuple[float, float] = (0.5, 0.5),
                         primary_metric: str = "f1",
                         minimum_effect_size: float = 0.05,
                         significance_level: float = 0.05) -> str:
        """
        Create a new A/B test experiment.
        
        Args:
            name: Experiment name
            description: Experiment description  
            hypothesis: Hypothesis being tested
            control_model: (model_id, version) for control
            treatment_model: (model_id, version) for treatment
            traffic_split: Traffic allocation (control, treatment)
            primary_metric: Primary metric to optimize
            minimum_effect_size: Minimum detectable effect size
            significance_level: Statistical significance level
            
        Returns:
            Experiment ID
        """
        experiment_id = f"exp_{int(time.time())}"
        
        try:
            # Create variants
            control_variant = ModelVariant(
                variant_id=f"{experiment_id}_control",
                model_id=control_model[0],
                model_version=control_model[1],
                variant_type=VariantType.CONTROL,
                traffic_allocation=traffic_split[0]
            )
            
            treatment_variant = ModelVariant(
                variant_id=f"{experiment_id}_treatment",
                model_id=treatment_model[0],
                model_version=treatment_model[1],
                variant_type=VariantType.TREATMENT,
                traffic_allocation=traffic_split[1]
            )
            
            # Calculate target sample size
            target_sample_size = self._calculate_sample_size(
                minimum_effect_size, significance_level, 0.8
            )
            
            # Create experiment
            experiment = ABTestExperiment(
                experiment_id=experiment_id,
                name=name,
                description=description,
                hypothesis=hypothesis,
                variants=[control_variant, treatment_variant],
                primary_metric=primary_metric,
                minimum_effect_size=minimum_effect_size,
                significance_level=significance_level,
                statistical_power=0.8,
                status=ExperimentStatus.DRAFT,
                created_at=datetime.now().isoformat(),
                target_sample_size=target_sample_size
            )
            
            # Store experiment
            self._store_experiment(experiment)
            
            self.logger.info(f"Created experiment {experiment_id}: {name}")
            return experiment_id
            
        except Exception as e:
            self.logger.error(f"Failed to create experiment: {e}")
            return ""
    
    def start_experiment(self, experiment_id: str) -> bool:
        """Start an A/B test experiment."""
        try:
            # Load experiment
            experiment = self._load_experiment(experiment_id)
            if not experiment:
                self.logger.error(f"Experiment {experiment_id} not found")
                return False
            
            if experiment.status != ExperimentStatus.DRAFT:
                self.logger.error(f"Experiment {experiment_id} is not in draft status")
                return False
            
            # Validate experiment configuration
            if not self._validate_experiment(experiment):
                return False
            
            # Start experiment
            experiment.status = ExperimentStatus.RUNNING
            experiment.started_at = datetime.now().isoformat()
            
            # Add to active experiments
            self.active_experiments[experiment_id] = experiment
            
            # Store updated experiment
            self._store_experiment(experiment)
            
            # Publish experiment start event
            self._publish_experiment_event("experiment_started", experiment)
            
            self.logger.info(f"Started experiment {experiment_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start experiment: {e}")
            return False
    
    def assign_variant(self, user_id: str, transaction_context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Assign user to a variant for active experiments.
        
        Args:
            user_id: User identifier
            transaction_context: Additional context for assignment
            
        Returns:
            Variant ID for model selection, or None if no active experiment
        """
        if not self.active_experiments:
            return None
        
        try:
            # For simplicity, use first active experiment
            # In production, you'd have more sophisticated experiment selection
            experiment = list(self.active_experiments.values())[0]
            
            # Check if user already assigned
            assignment_key = f"assignment:{experiment.experiment_id}:{user_id}"
            existing_assignment = self.redis_ab_tests.get(assignment_key)
            
            if existing_assignment:
                assignment_data = json.loads(existing_assignment)
                return assignment_data["variant_id"]
            
            # Assign user to variant
            variant_id = self._assign_user_to_variant(user_id, experiment, transaction_context)
            
            if variant_id:
                # Create assignment record
                assignment = TrafficAssignment(
                    user_id=user_id,
                    experiment_id=experiment.experiment_id,
                    variant_id=variant_id,
                    assignment_timestamp=datetime.now().isoformat(),
                    assignment_hash=self._generate_assignment_hash(user_id, experiment.experiment_id)
                )
                
                # Store assignment
                self.redis_ab_tests.setex(assignment_key, 86400, json.dumps(asdict(assignment)))
                
                # Publish assignment event
                self._publish_assignment_event(assignment)
                
                return variant_id
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to assign variant: {e}")
            return None
    
    def record_prediction_result(self, user_id: str, variant_id: str, 
                               prediction: float, actual_label: Optional[int] = None,
                               transaction_amount: float = 0.0) -> None:
        """
        Record prediction result for A/B test analysis.
        
        Args:
            user_id: User identifier
            variant_id: Variant that made the prediction
            prediction: Model prediction (0-1 probability)
            actual_label: True label if available
            transaction_amount: Transaction amount for business metrics
        """
        try:
            # Find experiment and variant
            experiment = None
            variant = None
            
            for exp in self.active_experiments.values():
                for var in exp.variants:
                    if var.variant_id == variant_id:
                        experiment = exp
                        variant = var
                        break
                if variant:
                    break
            
            if not experiment or not variant:
                self.logger.warning(f"Variant {variant_id} not found in active experiments")
                return
            
            # Update variant metrics
            variant.total_predictions += 1
            
            if prediction >= 0.5:  # Fraud prediction
                variant.fraud_predictions += 1
                
                if actual_label == 1:  # True positive
                    variant.true_positives += 1
                    variant.total_fraud_value_detected += transaction_amount
                elif actual_label == 0:  # False positive
                    variant.false_positives += 1
                    
            else:  # Legitimate prediction
                if actual_label == 0:  # True negative
                    variant.true_negatives += 1
                elif actual_label == 1:  # False negative
                    variant.false_negatives += 1
                    variant.total_fraud_value_missed += transaction_amount
            
            # Recalculate metrics
            variant.calculate_metrics()
            
            # Update experiment sample size
            experiment.current_sample_size = sum(v.total_predictions for v in experiment.variants)
            
            # Store updated experiment
            self._store_experiment(experiment)
            
            # Check if we should analyze results
            if experiment.current_sample_size % 100 == 0:  # Analyze every 100 samples
                self._analyze_experiment_results(experiment)
            
        except Exception as e:
            self.logger.error(f"Failed to record prediction result: {e}")
    
    def _assign_user_to_variant(self, user_id: str, experiment: ABTestExperiment,
                               context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Assign user to variant using consistent hashing."""
        try:
            # Create consistent hash for user + experiment
            hash_input = f"{user_id}:{experiment.experiment_id}".encode()
            hash_value = int(hashlib.md5(hash_input).hexdigest(), 16)
            assignment_ratio = (hash_value % 10000) / 10000.0  # 0.0 to 1.0
            
            # Assign based on traffic allocation
            cumulative_allocation = 0.0
            
            for variant in experiment.variants:
                cumulative_allocation += variant.traffic_allocation
                
                if assignment_ratio <= cumulative_allocation:
                    return variant.variant_id
            
            # Fallback to control
            return experiment.variants[0].variant_id
            
        except Exception as e:
            self.logger.error(f"Failed to assign user to variant: {e}")
            return None
    
    def _calculate_sample_size(self, effect_size: float, alpha: float, power: float) -> int:
        """Calculate required sample size for statistical power."""
        try:
            # Simplified sample size calculation for proportions
            z_alpha = stats.norm.ppf(1 - alpha/2)
            z_beta = stats.norm.ppf(power)
            
            # Assume baseline proportion of 0.5 for conservative estimate
            p = 0.5
            
            n = (2 * p * (1 - p) * (z_alpha + z_beta)**2) / (effect_size**2)
            
            return max(100, int(n))  # Minimum 100 samples per variant
            
        except Exception:
            return 1000  # Default sample size
    
    def _analyze_experiment_results(self, experiment: ABTestExperiment) -> None:
        """Analyze experiment results and update conclusions."""
        try:
            if len(experiment.variants) != 2:
                self.logger.warning("Analysis currently supports only 2-variant experiments")
                return
            
            control = experiment.variants[0]
            treatment = experiment.variants[1]
            
            # Get primary metric values
            control_metric = self._get_metric_value(control, experiment.primary_metric)
            treatment_metric = self._get_metric_value(treatment, experiment.primary_metric)
            
            if control.total_predictions < 50 or treatment.total_predictions < 50:
                return  # Not enough data
            
            # Perform statistical test
            p_value, effect_size, confidence = self._perform_statistical_test(
                control, treatment, experiment.primary_metric
            )
            
            # Update experiment results
            experiment.p_value = p_value
            experiment.effect_size = effect_size
            experiment.confidence_level = confidence
            
            # Determine winner
            if p_value < experiment.significance_level:
                if treatment_metric > control_metric:
                    experiment.winner = treatment.variant_id
                    experiment.decision_result = DecisionResult.TREATMENT_WINS
                else:
                    experiment.winner = control.variant_id
                    experiment.decision_result = DecisionResult.CONTROL_WINS
                    
                # Check for early stopping
                if experiment.early_stopping_enabled and confidence > experiment.early_stopping_threshold:
                    self._stop_experiment_early(experiment, "Statistical significance achieved")
                    
            else:
                experiment.decision_result = DecisionResult.INCONCLUSIVE
                
                # Check for futility
                if (experiment.early_stopping_enabled and 
                    abs(effect_size) < experiment.futility_threshold and 
                    experiment.current_sample_size > experiment.target_sample_size * 0.5):
                    
                    self._stop_experiment_early(experiment, "Futility - effect size too small")
            
            # Store updated results
            self._store_experiment(experiment)
            
            # Add to analysis history
            analysis_result = {
                "timestamp": datetime.now().isoformat(),
                "experiment_id": experiment.experiment_id,
                "sample_size": experiment.current_sample_size,
                "p_value": p_value,
                "effect_size": effect_size,
                "confidence": confidence,
                "decision": experiment.decision_result.value
            }
            
            self.analysis_history.append(analysis_result)
            
            self.logger.info(f"Analyzed experiment {experiment.experiment_id}: "
                           f"p={p_value:.4f}, effect={effect_size:.4f}, "
                           f"decision={experiment.decision_result.value}")
            
        except Exception as e:
            self.logger.error(f"Failed to analyze experiment results: {e}")
    
    def _get_metric_value(self, variant: ModelVariant, metric: str) -> float:
        """Get metric value for a variant."""
        if metric == "precision":
            return variant.precision
        elif metric == "recall":
            return variant.recall
        elif metric == "f1":
            return variant.f1_score
        elif metric == "auc":
            return variant.auc_score
        elif metric == "fpr":
            return variant.false_positive_rate
        elif metric == "business_value":
            detected_value = variant.total_fraud_value_detected
            missed_value = variant.total_fraud_value_missed
            false_alarm_cost = variant.false_alarms * 10  # Assume $10 cost per false alarm
            return detected_value - missed_value - false_alarm_cost
        else:
            return 0.0
    
    def _perform_statistical_test(self, control: ModelVariant, treatment: ModelVariant, 
                                 metric: str) -> Tuple[float, float, float]:
        """Perform statistical test between variants."""
        try:
            if metric in ["precision", "recall", "f1"]:
                # For proportions, use two-proportion z-test
                
                if metric == "precision":
                    c_success = control.true_positives
                    c_total = control.true_positives + control.false_positives
                    t_success = treatment.true_positives
                    t_total = treatment.true_positives + treatment.false_positives
                elif metric == "recall":
                    c_success = control.true_positives
                    c_total = control.true_positives + control.false_negatives
                    t_success = treatment.true_positives
                    t_total = treatment.true_positives + treatment.false_negatives
                else:  # f1 - approximate as harmonic mean
                    c_success = control.true_positives
                    c_total = control.total_predictions
                    t_success = treatment.true_positives
                    t_total = treatment.total_predictions
                
                if c_total == 0 or t_total == 0:
                    return 1.0, 0.0, 0.0
                
                # Two-proportion z-test
                p1 = c_success / c_total
                p2 = t_success / t_total
                
                n1, n2 = c_total, t_total
                p_pooled = (c_success + t_success) / (c_total + t_total)
                
                se = np.sqrt(p_pooled * (1 - p_pooled) * (1/n1 + 1/n2))
                
                if se > 0:
                    z_stat = (p2 - p1) / se
                    p_value = 2 * (1 - stats.norm.cdf(abs(z_stat)))
                    effect_size = p2 - p1
                    confidence = 1 - p_value
                else:
                    p_value, effect_size, confidence = 1.0, 0.0, 0.0
                
                return p_value, effect_size, confidence
            
            else:
                # For other metrics, use t-test (simplified)
                control_values = [self._get_metric_value(control, metric)] * control.total_predictions
                treatment_values = [self._get_metric_value(treatment, metric)] * treatment.total_predictions
                
                if len(control_values) > 1 and len(treatment_values) > 1:
                    t_stat, p_value = stats.ttest_ind(control_values, treatment_values)
                    effect_size = np.mean(treatment_values) - np.mean(control_values)
                    confidence = 1 - p_value if p_value < 1.0 else 0.0
                else:
                    p_value, effect_size, confidence = 1.0, 0.0, 0.0
                
                return p_value, effect_size, confidence
                
        except Exception as e:
            self.logger.error(f"Statistical test failed: {e}")
            return 1.0, 0.0, 0.0
    
    def _stop_experiment_early(self, experiment: ABTestExperiment, reason: str) -> None:
        """Stop experiment early due to significance or futility."""
        try:
            experiment.status = ExperimentStatus.STOPPED_EARLY
            experiment.ended_at = datetime.now().isoformat()
            
            # Remove from active experiments
            if experiment.experiment_id in self.active_experiments:
                del self.active_experiments[experiment.experiment_id]
            
            # Store final results
            self._store_experiment(experiment)
            
            # Publish experiment end event
            self._publish_experiment_event("experiment_stopped_early", experiment, {"reason": reason})
            
            self.logger.info(f"Stopped experiment {experiment.experiment_id} early: {reason}")
            
        except Exception as e:
            self.logger.error(f"Failed to stop experiment early: {e}")
    
    def _validate_experiment(self, experiment: ABTestExperiment) -> bool:
        """Validate experiment configuration before starting."""
        # Check that variants exist and are valid
        if len(experiment.variants) < 2:
            self.logger.error("Experiment must have at least 2 variants")
            return False
        
        # Check traffic allocation sums to 1.0
        total_traffic = sum(v.traffic_allocation for v in experiment.variants)
        if abs(total_traffic - 1.0) > 0.01:
            self.logger.error(f"Traffic allocation must sum to 1.0, got {total_traffic}")
            return False
        
        # Check that models exist (simplified check)
        # In production, you'd verify with model registry
        
        return True
    
    def _store_experiment(self, experiment: ABTestExperiment) -> None:
        """Store experiment in Redis."""
        try:
            experiment_key = f"experiment:{experiment.experiment_id}"
            self.redis_ab_tests.set(experiment_key, json.dumps(experiment.to_dict()))
            
        except Exception as e:
            self.logger.error(f"Failed to store experiment: {e}")
    
    def _load_experiment(self, experiment_id: str) -> Optional[ABTestExperiment]:
        """Load experiment from Redis."""
        try:
            experiment_key = f"experiment:{experiment_id}"
            experiment_data = self.redis_ab_tests.get(experiment_key)
            
            if not experiment_data:
                return None
            
            exp_dict = json.loads(experiment_data)
            
            # Reconstruct experiment
            experiment = ABTestExperiment(**exp_dict)
            experiment.status = ExperimentStatus(exp_dict["status"])
            experiment.decision_result = DecisionResult(exp_dict["decision_result"])
            
            # Reconstruct variants
            variants = []
            for var_dict in exp_dict["variants"]:
                variant = ModelVariant(**var_dict)
                variant.variant_type = VariantType(var_dict["variant_type"])
                variants.append(variant)
            
            experiment.variants = variants
            
            return experiment
            
        except Exception as e:
            self.logger.error(f"Failed to load experiment: {e}")
            return None
    
    def _generate_assignment_hash(self, user_id: str, experiment_id: str) -> str:
        """Generate hash for assignment consistency."""
        hash_input = f"{user_id}:{experiment_id}:{datetime.now().date()}"
        return hashlib.md5(hash_input.encode()).hexdigest()
    
    def _publish_experiment_event(self, event_type: str, experiment: ABTestExperiment,
                                 additional_data: Optional[Dict[str, Any]] = None) -> None:
        """Publish experiment lifecycle events."""
        try:
            event = {
                "event_type": event_type,
                "timestamp": datetime.now().isoformat(),
                "experiment_id": experiment.experiment_id,
                "experiment_data": experiment.to_dict(),
                "additional_data": additional_data or {}
            }
            
            self.producer.produce(
                self.config.ab_test_assignments_topic,
                key=f"{event_type}_{experiment.experiment_id}",
                value=json.dumps(event)
            )
            self.producer.flush()
            
        except Exception as e:
            self.logger.error(f"Failed to publish experiment event: {e}")
    
    def _publish_assignment_event(self, assignment: TrafficAssignment) -> None:
        """Publish traffic assignment event."""
        try:
            event = {
                "event_type": "traffic_assigned",
                "timestamp": datetime.now().isoformat(),
                "assignment": asdict(assignment)
            }
            
            self.producer.produce(
                self.config.ab_test_assignments_topic,
                key=f"assignment_{assignment.user_id}",
                value=json.dumps(event)
            )
            self.producer.flush()
            
        except Exception as e:
            self.logger.error(f"Failed to publish assignment event: {e}")
    
    def get_experiment_results(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive experiment results."""
        try:
            experiment = self._load_experiment(experiment_id)
            if not experiment:
                return None
            
            results = {
                "experiment_info": {
                    "id": experiment.experiment_id,
                    "name": experiment.name,
                    "status": experiment.status.value,
                    "started_at": experiment.started_at,
                    "ended_at": experiment.ended_at,
                    "current_sample_size": experiment.current_sample_size,
                    "target_sample_size": experiment.target_sample_size
                },
                "statistical_results": {
                    "primary_metric": experiment.primary_metric,
                    "p_value": experiment.p_value,
                    "effect_size": experiment.effect_size,
                    "confidence_level": experiment.confidence_level,
                    "decision": experiment.decision_result.value,
                    "winner": experiment.winner
                },
                "variants": []
            }
            
            for variant in experiment.variants:
                variant_results = {
                    "variant_id": variant.variant_id,
                    "variant_type": variant.variant_type.value,
                    "model_id": variant.model_id,
                    "traffic_allocation": variant.traffic_allocation,
                    "total_predictions": variant.total_predictions,
                    "performance_metrics": {
                        "precision": variant.precision,
                        "recall": variant.recall,
                        "f1_score": variant.f1_score,
                        "false_positive_rate": variant.false_positive_rate
                    },
                    "business_metrics": {
                        "fraud_detected": variant.fraud_detected,
                        "fraud_missed": variant.fraud_missed,
                        "false_alarms": variant.false_alarms,
                        "total_fraud_value_detected": variant.total_fraud_value_detected,
                        "total_fraud_value_missed": variant.total_fraud_value_missed
                    }
                }
                
                results["variants"].append(variant_results)
            
            return results
            
        except Exception as e:
            self.logger.error(f"Failed to get experiment results: {e}")
            return None
    
    def list_experiments(self, status_filter: Optional[ExperimentStatus] = None) -> List[Dict[str, Any]]:
        """List experiments with optional status filter."""
        try:
            experiment_keys = self.redis_ab_tests.keys("experiment:*")
            experiments = []
            
            for key in experiment_keys:
                experiment_data = self.redis_ab_tests.get(key)
                if experiment_data:
                    exp_dict = json.loads(experiment_data)
                    
                    if status_filter is None or exp_dict["status"] == status_filter.value:
                        experiments.append({
                            "experiment_id": exp_dict["experiment_id"],
                            "name": exp_dict["name"],
                            "status": exp_dict["status"],
                            "created_at": exp_dict["created_at"],
                            "started_at": exp_dict.get("started_at"),
                            "current_sample_size": exp_dict["current_sample_size"],
                            "decision_result": exp_dict["decision_result"]
                        })
            
            # Sort by creation date (newest first)
            experiments.sort(key=lambda x: x["created_at"], reverse=True)
            
            return experiments
            
        except Exception as e:
            self.logger.error(f"Failed to list experiments: {e}")
            return []
    
    def get_ab_test_statistics(self) -> Dict[str, Any]:
        """Get A/B testing system statistics."""
        all_experiments = self.list_experiments()
        
        status_counts = {}
        for exp in all_experiments:
            status = exp["status"]
            status_counts[status] = status_counts.get(status, 0) + 1
        
        decision_counts = {}
        for exp in all_experiments:
            decision = exp["decision_result"]
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
        
        return {
            "total_experiments": len(all_experiments),
            "active_experiments": len(self.active_experiments),
            "status_distribution": status_counts,
            "decision_distribution": decision_counts,
            "analysis_history_size": len(self.analysis_history),
            "avg_sample_size": np.mean([exp["current_sample_size"] for exp in all_experiments]) if all_experiments else 0
        }
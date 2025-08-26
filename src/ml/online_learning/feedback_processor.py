# /stream-sentinel/src/ml/online_learning/feedback_processor.py

"""
Feedback Processing System for Online Learning

This module handles the collection, validation, and processing of fraud investigation
feedback to enable incremental model learning. It ensures data quality through
multi-level validation and maintains audit trails for compliance.

Key features:
- Real-time feedback collection from investigation results
- Multi-validator consensus for feedback quality assurance
- Temporal feedback weighting based on recency
- Duplicate detection and conflict resolution
- Audit trail maintenance for regulatory compliance
"""

import json
import time
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
from confluent_kafka import Consumer, Producer, KafkaError
import redis
import numpy as np
from pathlib import Path

from .config import OnlineLearningConfig, get_online_learning_config


class FeedbackLabel(Enum):
    """Feedback labels for fraud classification."""
    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNCERTAIN = "uncertain"
    NEEDS_REVIEW = "needs_review"


class FeedbackSource(Enum):
    """Sources of fraud feedback."""
    MANUAL_INVESTIGATION = "manual_investigation"
    AUTOMATED_VERIFICATION = "automated_verification"
    CUSTOMER_DISPUTE = "customer_dispute"
    CHARGEBACK = "chargeback"
    FALSE_POSITIVE_REPORT = "false_positive_report"


@dataclass
class FeedbackRecord:
    """Individual feedback record from fraud investigation."""
    transaction_id: str
    feedback_id: str
    label: FeedbackLabel
    confidence: float  # 0.0 to 1.0
    source: FeedbackSource
    investigator_id: str
    timestamp: str
    investigation_duration_minutes: float
    
    # Additional context
    original_prediction: float
    original_features: Dict[str, Any]
    investigation_notes: Optional[str] = None
    supporting_evidence: Optional[Dict[str, Any]] = None
    
    # Quality metrics
    investigator_experience_score: float = 1.0
    feedback_consistency_score: float = 1.0
    
    def __post_init__(self):
        """Validate feedback record after initialization."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {self.confidence}")
        
        if self.investigator_experience_score < 0.0:
            raise ValueError("Investigator experience score cannot be negative")


@dataclass
class ProcessedFeedback:
    """Processed and validated feedback ready for model training."""
    transaction_id: str
    final_label: FeedbackLabel
    weighted_confidence: float
    consensus_score: float
    feedback_records: List[FeedbackRecord] = field(default_factory=list)
    processing_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    quality_score: float = 1.0
    
    # Training metadata
    should_include_in_training: bool = True
    feedback_weight: float = 1.0
    temporal_weight: float = 1.0


class FeedbackProcessor:
    """
    Processes fraud investigation feedback for online learning.
    
    This class handles the complete feedback processing pipeline:
    1. Collects feedback from multiple sources
    2. Validates feedback quality and consistency
    3. Resolves conflicts between multiple feedback sources
    4. Weights feedback based on temporal and quality factors
    5. Prepares validated feedback for model training
    """
    
    def __init__(self, config: Optional[OnlineLearningConfig] = None):
        self.config = config or get_online_learning_config()
        self.logger = logging.getLogger(__name__)
        
        # Initialize Redis connections
        self._init_redis()
        
        # Initialize Kafka
        self._init_kafka()
        
        # Feedback storage and processing
        self.pending_feedback: Dict[str, List[FeedbackRecord]] = {}
        self.processed_feedback: Dict[str, ProcessedFeedback] = {}
        
        # Quality control metrics
        self.investigator_performance: Dict[str, Dict[str, float]] = {}
        self.feedback_statistics = {
            "total_received": 0,
            "total_processed": 0,
            "consensus_achieved": 0,
            "conflicts_resolved": 0,
            "quality_rejections": 0
        }
        
        self.logger.info("FeedbackProcessor initialized successfully")
    
    def _init_redis(self) -> None:
        """Initialize Redis connections for different databases."""
        try:
            self.redis_feedback = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db_feedback,
                decode_responses=True
            )
            
            self.redis_models = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                db=self.config.redis_db_models,
                decode_responses=True
            )
            
            # Test connections
            self.redis_feedback.ping()
            self.redis_models.ping()
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis connections: {e}")
            raise
    
    def _init_kafka(self) -> None:
        """Initialize Kafka consumer and producer for feedback processing."""
        try:
            # Consumer for feedback collection
            consumer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'group.id': 'feedback-processor',
                'auto.offset.reset': 'latest',
                'enable.auto.commit': True
            }
            self.consumer = Consumer(consumer_config)
            self.consumer.subscribe([self.config.feedback_topic])
            
            # Producer for processed feedback
            producer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'linger.ms': 5,
                'compression.type': 'lz4'
            }
            self.producer = Producer(producer_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def collect_feedback(self, timeout: float = 1.0) -> List[FeedbackRecord]:
        """
        Collect new feedback records from Kafka.
        
        Args:
            timeout: Timeout for Kafka polling in seconds
            
        Returns:
            List of new feedback records
        """
        feedback_records = []
        
        try:
            message = self.consumer.poll(timeout=timeout)
            
            if message is None:
                return feedback_records
                
            if message.error():
                if message.error().code() != KafkaError._PARTITION_EOF:
                    self.logger.error(f"Consumer error: {message.error()}")
                return feedback_records
            
            # Parse feedback message
            try:
                feedback_data = json.loads(message.value())
                feedback_record = self._parse_feedback_message(feedback_data)
                feedback_records.append(feedback_record)
                
                self.feedback_statistics["total_received"] += 1
                self.logger.info(f"Collected feedback for transaction {feedback_record.transaction_id}")
                
            except Exception as e:
                self.logger.error(f"Failed to parse feedback message: {e}")
                
        except Exception as e:
            self.logger.error(f"Error collecting feedback: {e}")
        
        return feedback_records
    
    def _parse_feedback_message(self, data: Dict[str, Any]) -> FeedbackRecord:
        """Parse feedback message from Kafka into FeedbackRecord."""
        return FeedbackRecord(
            transaction_id=data["transaction_id"],
            feedback_id=data["feedback_id"],
            label=FeedbackLabel(data["label"]),
            confidence=data["confidence"],
            source=FeedbackSource(data["source"]),
            investigator_id=data["investigator_id"],
            timestamp=data["timestamp"],
            investigation_duration_minutes=data["investigation_duration_minutes"],
            original_prediction=data["original_prediction"],
            original_features=data["original_features"],
            investigation_notes=data.get("investigation_notes"),
            supporting_evidence=data.get("supporting_evidence"),
            investigator_experience_score=data.get("investigator_experience_score", 1.0),
            feedback_consistency_score=data.get("feedback_consistency_score", 1.0)
        )
    
    def add_feedback(self, feedback_record: FeedbackRecord) -> bool:
        """
        Add feedback record to pending processing queue.
        
        Args:
            feedback_record: Validated feedback record
            
        Returns:
            True if added successfully, False otherwise
        """
        try:
            transaction_id = feedback_record.transaction_id
            
            if transaction_id not in self.pending_feedback:
                self.pending_feedback[transaction_id] = []
            
            self.pending_feedback[transaction_id].append(feedback_record)
            
            # Store in Redis for persistence
            feedback_key = f"pending_feedback:{transaction_id}"
            feedback_data = json.dumps([asdict(record) for record in self.pending_feedback[transaction_id]])
            self.redis_feedback.setex(feedback_key, 86400, feedback_data)  # 24 hour TTL
            
            self.logger.debug(f"Added feedback for transaction {transaction_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to add feedback: {e}")
            return False
    
    def process_pending_feedback(self) -> List[ProcessedFeedback]:
        """
        Process all pending feedback records to create training-ready data.
        
        Returns:
            List of processed feedback ready for model training
        """
        processed_batch = []
        
        for transaction_id in list(self.pending_feedback.keys()):
            feedback_records = self.pending_feedback[transaction_id]
            
            # Check if we should process this transaction
            if self._should_process_feedback(transaction_id, feedback_records):
                processed = self._process_transaction_feedback(transaction_id, feedback_records)
                
                if processed:
                    processed_batch.append(processed)
                    
                    # Move from pending to processed
                    self.processed_feedback[transaction_id] = processed
                    del self.pending_feedback[transaction_id]
                    
                    # Update Redis
                    self._store_processed_feedback(processed)
                    self._remove_pending_feedback(transaction_id)
                    
                    self.feedback_statistics["total_processed"] += 1
        
        return processed_batch
    
    def _should_process_feedback(self, transaction_id: str, feedback_records: List[FeedbackRecord]) -> bool:
        """Determine if feedback for a transaction should be processed."""
        if not feedback_records:
            return False
        
        # Check minimum feedback requirements
        if len(feedback_records) < self.config.feedback.min_confirmations:
            # Check if enough time has passed to process with single feedback
            latest_feedback = max(feedback_records, key=lambda x: x.timestamp)
            feedback_age = datetime.now() - datetime.fromisoformat(latest_feedback.timestamp)
            
            if feedback_age.total_seconds() < 3600:  # Less than 1 hour old
                return False
        
        # Check feedback age
        max_age_hours = self.config.feedback.max_feedback_age_hours
        current_time = datetime.now()
        
        for record in feedback_records:
            record_age = current_time - datetime.fromisoformat(record.timestamp)
            if record_age.total_seconds() > (max_age_hours * 3600):
                self.logger.warning(f"Feedback for {transaction_id} is too old, skipping")
                return False
        
        return True
    
    def _process_transaction_feedback(self, transaction_id: str, feedback_records: List[FeedbackRecord]) -> Optional[ProcessedFeedback]:
        """Process feedback for a single transaction."""
        try:
            # Validate feedback quality
            valid_records = self._validate_feedback_quality(feedback_records)
            
            if not valid_records:
                self.feedback_statistics["quality_rejections"] += 1
                self.logger.warning(f"All feedback for {transaction_id} failed quality validation")
                return None
            
            # Resolve conflicts and determine consensus
            final_label, consensus_score = self._resolve_feedback_conflicts(valid_records)
            
            if consensus_score < self.config.feedback.feedback_validation_threshold:
                self.logger.warning(f"Consensus score {consensus_score} below threshold for {transaction_id}")
                return None
            
            # Calculate weighted confidence
            weighted_confidence = self._calculate_weighted_confidence(valid_records)
            
            # Calculate temporal weight
            temporal_weight = self._calculate_temporal_weight(valid_records)
            
            # Calculate overall quality score
            quality_score = self._calculate_quality_score(valid_records, consensus_score)
            
            processed_feedback = ProcessedFeedback(
                transaction_id=transaction_id,
                final_label=final_label,
                weighted_confidence=weighted_confidence,
                consensus_score=consensus_score,
                feedback_records=valid_records,
                quality_score=quality_score,
                temporal_weight=temporal_weight,
                feedback_weight=min(1.0, quality_score * temporal_weight),
                should_include_in_training=(quality_score > 0.7 and consensus_score > 0.8)
            )
            
            if consensus_score > 0.9:
                self.feedback_statistics["consensus_achieved"] += 1
            elif len(valid_records) > 1:
                self.feedback_statistics["conflicts_resolved"] += 1
            
            return processed_feedback
            
        except Exception as e:
            self.logger.error(f"Failed to process feedback for {transaction_id}: {e}")
            return None
    
    def _validate_feedback_quality(self, feedback_records: List[FeedbackRecord]) -> List[FeedbackRecord]:
        """Validate feedback records for quality and consistency."""
        valid_records = []
        
        for record in feedback_records:
            # Check confidence threshold
            if record.confidence < self.config.feedback.min_investigator_confidence:
                continue
            
            # Check investigator performance history
            investigator_score = self._get_investigator_performance(record.investigator_id)
            if investigator_score < 0.6:  # Poor performance threshold
                continue
            
            # Check investigation duration (too fast might indicate poor quality)
            if record.investigation_duration_minutes < 2.0:  # Less than 2 minutes
                continue
            
            # Check for obvious inconsistencies
            if self._has_feedback_inconsistencies(record):
                continue
            
            valid_records.append(record)
        
        return valid_records
    
    def _resolve_feedback_conflicts(self, feedback_records: List[FeedbackRecord]) -> Tuple[FeedbackLabel, float]:
        """Resolve conflicts between multiple feedback sources."""
        if len(feedback_records) == 1:
            return feedback_records[0].label, 1.0
        
        # Weight feedback by investigator experience and confidence
        weighted_votes = {}
        total_weight = 0.0
        
        for record in feedback_records:
            label = record.label
            weight = (record.confidence * 
                     record.investigator_experience_score * 
                     record.feedback_consistency_score)
            
            if label not in weighted_votes:
                weighted_votes[label] = 0.0
            
            weighted_votes[label] += weight
            total_weight += weight
        
        # Normalize weights
        if total_weight > 0:
            for label in weighted_votes:
                weighted_votes[label] /= total_weight
        
        # Find consensus
        if not weighted_votes:
            return FeedbackLabel.UNCERTAIN, 0.0
        
        final_label = max(weighted_votes, key=weighted_votes.get)
        consensus_score = weighted_votes[final_label]
        
        return final_label, consensus_score
    
    def _calculate_weighted_confidence(self, feedback_records: List[FeedbackRecord]) -> float:
        """Calculate weighted confidence score from feedback records."""
        if not feedback_records:
            return 0.0
        
        total_weighted_confidence = 0.0
        total_weight = 0.0
        
        for record in feedback_records:
            weight = (record.investigator_experience_score * 
                     record.feedback_consistency_score)
            total_weighted_confidence += record.confidence * weight
            total_weight += weight
        
        return total_weighted_confidence / total_weight if total_weight > 0 else 0.0
    
    def _calculate_temporal_weight(self, feedback_records: List[FeedbackRecord]) -> float:
        """Calculate temporal weight based on feedback recency."""
        if not feedback_records:
            return 0.0
        
        current_time = datetime.now()
        decay_hours = self.config.feedback.weight_decay_hours
        
        total_weight = 0.0
        total_records = 0
        
        for record in feedback_records:
            record_time = datetime.fromisoformat(record.timestamp)
            hours_old = (current_time - record_time).total_seconds() / 3600
            
            # Exponential decay
            temporal_weight = np.exp(-hours_old / decay_hours)
            total_weight += temporal_weight
            total_records += 1
        
        return total_weight / total_records if total_records > 0 else 0.0
    
    def _calculate_quality_score(self, feedback_records: List[FeedbackRecord], consensus_score: float) -> float:
        """Calculate overall quality score for feedback."""
        if not feedback_records:
            return 0.0
        
        # Base quality from consensus
        base_quality = consensus_score
        
        # Adjust for number of feedback sources
        source_bonus = min(0.2, len(feedback_records) * 0.1)
        
        # Average investigator experience
        avg_experience = np.mean([r.investigator_experience_score for r in feedback_records])
        
        # Average confidence
        avg_confidence = np.mean([r.confidence for r in feedback_records])
        
        quality_score = (base_quality + source_bonus) * avg_experience * avg_confidence
        
        return min(1.0, quality_score)
    
    def _get_investigator_performance(self, investigator_id: str) -> float:
        """Get historical performance score for investigator."""
        if investigator_id not in self.investigator_performance:
            # Load from Redis or initialize
            performance_key = f"investigator_performance:{investigator_id}"
            performance_data = self.redis_feedback.get(performance_key)
            
            if performance_data:
                self.investigator_performance[investigator_id] = json.loads(performance_data)
            else:
                self.investigator_performance[investigator_id] = {
                    "accuracy": 1.0,
                    "consistency": 1.0,
                    "total_investigations": 0
                }
        
        performance = self.investigator_performance[investigator_id]
        return (performance["accuracy"] + performance["consistency"]) / 2.0
    
    def _has_feedback_inconsistencies(self, record: FeedbackRecord) -> bool:
        """Check for obvious inconsistencies in feedback record."""
        # Check if prediction and feedback are extremely contradictory
        if record.label == FeedbackLabel.FRAUD and record.original_prediction < 0.1:
            if record.confidence > 0.9:
                return True
        
        if record.label == FeedbackLabel.LEGITIMATE and record.original_prediction > 0.9:
            if record.confidence > 0.9:
                return True
        
        return False
    
    def _store_processed_feedback(self, processed_feedback: ProcessedFeedback) -> None:
        """Store processed feedback in Redis."""
        try:
            key = f"processed_feedback:{processed_feedback.transaction_id}"
            data = json.dumps(asdict(processed_feedback))
            self.redis_feedback.setex(key, 604800, data)  # 7 day TTL
        except Exception as e:
            self.logger.error(f"Failed to store processed feedback: {e}")
    
    def _remove_pending_feedback(self, transaction_id: str) -> None:
        """Remove pending feedback from Redis."""
        try:
            key = f"pending_feedback:{transaction_id}"
            self.redis_feedback.delete(key)
        except Exception as e:
            self.logger.error(f"Failed to remove pending feedback: {e}")
    
    def get_training_feedback(self, limit: int = 1000) -> List[ProcessedFeedback]:
        """
        Get processed feedback ready for model training.
        
        Args:
            limit: Maximum number of feedback records to return
            
        Returns:
            List of processed feedback suitable for training
        """
        training_feedback = []
        
        for processed in self.processed_feedback.values():
            if processed.should_include_in_training and len(training_feedback) < limit:
                training_feedback.append(processed)
        
        # Sort by processing timestamp (most recent first)
        training_feedback.sort(key=lambda x: x.processing_timestamp, reverse=True)
        
        return training_feedback[:limit]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get feedback processing statistics."""
        stats = self.feedback_statistics.copy()
        stats.update({
            "pending_transactions": len(self.pending_feedback),
            "processed_transactions": len(self.processed_feedback),
            "investigator_count": len(self.investigator_performance),
            "average_consensus_score": self._calculate_average_consensus_score(),
            "quality_distribution": self._get_quality_distribution()
        })
        return stats
    
    def _calculate_average_consensus_score(self) -> float:
        """Calculate average consensus score across processed feedback."""
        if not self.processed_feedback:
            return 0.0
        
        total_score = sum(fb.consensus_score for fb in self.processed_feedback.values())
        return total_score / len(self.processed_feedback)
    
    def _get_quality_distribution(self) -> Dict[str, int]:
        """Get distribution of quality scores."""
        distribution = {"high": 0, "medium": 0, "low": 0}
        
        for feedback in self.processed_feedback.values():
            if feedback.quality_score >= 0.8:
                distribution["high"] += 1
            elif feedback.quality_score >= 0.6:
                distribution["medium"] += 1
            else:
                distribution["low"] += 1
        
        return distribution
    
    def cleanup_old_feedback(self, max_age_days: int = 30) -> int:
        """
        Clean up old feedback records to manage memory usage.
        
        Args:
            max_age_days: Maximum age in days for keeping feedback
            
        Returns:
            Number of records cleaned up
        """
        cleaned_count = 0
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(days=max_age_days)
        
        # Clean processed feedback
        to_remove = []
        for transaction_id, feedback in self.processed_feedback.items():
            feedback_time = datetime.fromisoformat(feedback.processing_timestamp)
            if feedback_time < cutoff_time:
                to_remove.append(transaction_id)
        
        for transaction_id in to_remove:
            del self.processed_feedback[transaction_id]
            cleaned_count += 1
        
        self.logger.info(f"Cleaned up {cleaned_count} old feedback records")
        return cleaned_count
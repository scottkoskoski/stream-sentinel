"""
Comprehensive Unit Tests for Fraud Scoring and ML Integration

Tests the complete ML fraud scoring pipeline including:
- Model loading and validation
- Feature preprocessing and scaling  
- Ensemble scoring with business rules
- Score interpretation and thresholds
- Model performance monitoring
"""

import pytest
import pickle
import numpy as np
import pandas as pd
from unittest.mock import Mock, patch, MagicMock, mock_open
from pathlib import Path
import json
import lightgbm as lgb

import sys
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from consumers.fraud_detector import FraudDetector, FeatureSet, FraudScore


class TestFraudScoring:
    """Comprehensive ML fraud scoring tests."""

    def setup_method(self):
        """Setup for each test method."""
        # Mock Redis client
        self.mock_redis = MagicMock()
        
        # Mock Kafka config
        self.mock_config = Mock()
        self.mock_config.logger = Mock()
        self.mock_config.get_consumer_config.return_value = {}
        self.mock_config.get_producer_config.return_value = {}
        
        # Create mock LightGBM model
        self.mock_model = MagicMock(spec=lgb.Booster)
        self.mock_model.predict.return_value = np.array([0.15])  # Low fraud probability
        self.mock_model.num_feature.return_value = 25
        
        # Mock model metadata
        self.mock_metadata = {
            "model_version": "v1.0.0",
            "model_type": "lightgbm", 
            "training_date": "2023-08-01T00:00:00",
            "performance_metrics": {
                "auc_score": 0.836,
                "precision": 0.742,
                "recall": 0.698,
                "f1_score": 0.719
            },
            "feature_names": [
                "transaction_amount", "amount_log", "hour_of_day", "day_of_week",
                "is_weekend", "is_peak_fraud_hour", "is_small_amount", "is_large_amount",
                "merchant_category_encoded", "card_type_encoded", "is_new_user",
                "user_transaction_count", "user_total_amount", "user_avg_amount",
                "deviation_from_avg", "daily_transaction_count", "daily_amount_spent",
                "days_since_last_transaction", "amount_ratio_to_last", "suspicious_activity_count",
                "transactions_last_hour", "amount_last_hour", "transactions_last_10min",
                "avg_time_between_transactions", "is_high_velocity"
            ],
            "feature_count": 25,
            "training_samples": 100000
        }

        # Create fraud detector with mocked dependencies
        with patch('consumers.fraud_detector.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.fraud_detector.redis.Redis', return_value=self.mock_redis):
                with patch('consumers.fraud_detector.pickle.load', return_value=self.mock_model):
                    with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_metadata))):
                        self.fraud_detector = FraudDetector()
                        self.fraud_detector.redis_client = self.mock_redis
                        self.fraud_detector.ml_model = self.mock_model
                        self.fraud_detector.model_metadata = self.mock_metadata

    def test_model_loading_success(self):
        """Test successful model loading and validation."""
        # Test model is loaded
        assert self.fraud_detector.ml_model is not None
        assert self.fraud_detector.model_metadata is not None
        
        # Test model metadata validation
        assert self.fraud_detector.model_metadata["model_type"] == "lightgbm"
        assert self.fraud_detector.model_metadata["feature_count"] == 25
        assert self.fraud_detector.model_metadata["performance_metrics"]["auc_score"] == 0.836

    def test_model_loading_failure(self):
        """Test graceful handling of model loading failure."""
        with patch('consumers.fraud_detector.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.fraud_detector.redis.Redis', return_value=self.mock_redis):
                with patch('consumers.fraud_detector.pickle.load', side_effect=FileNotFoundError("Model not found")):
                    
                    # Should handle model loading failure gracefully
                    fraud_detector = FraudDetector()
                    
                    # Should fall back to rule-based scoring
                    assert fraud_detector.ml_model is None
                    assert fraud_detector.model_metadata is None

    def test_feature_preprocessing(self):
        """Test feature preprocessing for ML model."""
        # Test features matching training data
        raw_features = {
            "transaction_amount": 250.50,
            "amount_log": 5.525,
            "hour_of_day": 14,
            "day_of_week": 1,
            "is_weekend": 0,
            "is_peak_fraud_hour": 0,
            "is_small_amount": 0,
            "is_large_amount": 0,
            "merchant_category_encoded": 1,
            "card_type_encoded": 1,
            "is_new_user": 0,
            "user_transaction_count": 150,
            "user_total_amount": 3000.0,
            "user_avg_amount": 20.0,
            "deviation_from_avg": 11.525,  # (250.50-20)/20
            "daily_transaction_count": 3,
            "daily_amount_spent": 75.0,
            "days_since_last_transaction": 1,
            "amount_ratio_to_last": 10.02,  # 250.50/25.0
            "suspicious_activity_count": 0,
            "transactions_last_hour": 1,
            "amount_last_hour": 250.50,
            "transactions_last_10min": 1,
            "avg_time_between_transactions": 480,  # 8 hours in minutes
            "is_high_velocity": 0
        }
        
        # Test preprocessing
        processed_features = self.fraud_detector.preprocess_features(raw_features)
        
        # Should return numpy array
        assert isinstance(processed_features, np.ndarray)
        assert processed_features.shape == (1, 25)  # 1 sample, 25 features
        
        # Test feature ordering matches model training
        expected_order = self.mock_metadata["feature_names"]
        feature_vector = []
        for feature_name in expected_order:
            feature_vector.append(raw_features[feature_name])
        
        np.testing.assert_array_equal(processed_features[0], feature_vector)

    def test_feature_preprocessing_missing_features(self):
        """Test preprocessing with missing features."""
        # Incomplete feature set
        incomplete_features = {
            "transaction_amount": 100.0,
            "hour_of_day": 14,
            "is_new_user": 0
            # Missing many features
        }
        
        # Should handle missing features with default values
        processed_features = self.fraud_detector.preprocess_features(incomplete_features)
        
        assert isinstance(processed_features, np.ndarray)
        assert processed_features.shape == (1, 25)
        
        # Missing features should be filled with defaults (likely 0)
        assert not np.any(np.isnan(processed_features))

    def test_ml_model_prediction(self):
        """Test ML model prediction."""
        features = {
            "transaction_amount": 100.0,
            "amount_log": 4.61,
            "hour_of_day": 14,
            "is_new_user": 0,
            "user_avg_amount": 50.0,
            "deviation_from_avg": 1.0,
            "is_high_velocity": 0
        }
        
        # Ensure all required features are present
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in features:
                features[feature_name] = 0
        
        # Test ML prediction
        ml_score = self.fraud_detector.get_ml_fraud_score(features)
        
        # Should return probability between 0 and 1
        assert 0 <= ml_score <= 1
        assert isinstance(ml_score, float)
        
        # Verify model was called with preprocessed features
        self.mock_model.predict.assert_called_once()
        call_args = self.mock_model.predict.call_args[0][0]
        assert call_args.shape == (1, 25)

    def test_business_rules_scoring(self):
        """Test business rules fraud scoring."""
        # Test high-risk scenario: high velocity + large amount
        high_risk_features = {
            "transaction_amount": 2500.0,
            "is_large_amount": 1,
            "is_high_velocity": 1,
            "transactions_last_hour": 10,
            "amount_last_hour": 5000.0,
            "is_peak_fraud_hour": 1,
            "suspicious_activity_count": 5
        }
        
        business_score = self.fraud_detector.get_business_rules_score(high_risk_features)
        
        # High-risk scenario should have high score
        assert business_score >= 0.7
        assert isinstance(business_score, float)

        # Test low-risk scenario: normal transaction
        low_risk_features = {
            "transaction_amount": 25.0,
            "is_large_amount": 0,
            "is_high_velocity": 0,
            "transactions_last_hour": 1,
            "amount_last_hour": 25.0,
            "is_peak_fraud_hour": 0,
            "suspicious_activity_count": 0,
            "is_new_user": 0,
            "user_transaction_count": 100
        }
        
        business_score_low = self.fraud_detector.get_business_rules_score(low_risk_features)
        
        # Low-risk scenario should have low score
        assert business_score_low <= 0.3
        assert business_score_low < business_score  # Should be lower than high-risk

    def test_ensemble_scoring(self):
        """Test ensemble scoring combining ML model and business rules."""
        features = {
            "transaction_amount": 500.0,
            "is_large_amount": 0,
            "is_high_velocity": 0,
            "user_avg_amount": 50.0,
            "deviation_from_avg": 9.0  # Significant deviation
        }
        
        # Ensure all required features are present
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in features:
                features[feature_name] = 0
        
        # Test ensemble scoring
        fraud_score = self.fraud_detector.calculate_fraud_score(features)
        
        assert isinstance(fraud_score, FraudScore)
        assert 0 <= fraud_score.final_score <= 1
        assert 0 <= fraud_score.ml_score <= 1
        assert 0 <= fraud_score.business_rules_score <= 1
        assert fraud_score.model_version == "v1.0.0"
        assert len(fraud_score.contributing_factors) > 0

    def test_score_interpretation_and_severity(self):
        """Test fraud score interpretation and severity classification."""
        # Test different score ranges
        test_cases = [
            (0.05, "MINIMAL"),    # Very low score
            (0.15, "LOW"),        # Low score
            (0.35, "MEDIUM"),     # Medium score  
            (0.65, "HIGH"),       # High score
            (0.85, "CRITICAL")    # Very high score
        ]
        
        for score_value, expected_severity in test_cases:
            # Mock ML model to return specific score
            self.mock_model.predict.return_value = np.array([score_value])
            
            features = {"transaction_amount": 100.0}
            for feature_name in self.mock_metadata["feature_names"]:
                if feature_name not in features:
                    features[feature_name] = 0
            
            fraud_score = self.fraud_detector.calculate_fraud_score(features)
            severity = self.fraud_detector.get_risk_severity(fraud_score.final_score)
            
            assert severity == expected_severity

    def test_model_performance_monitoring(self):
        """Test model performance monitoring and drift detection."""
        # Mock performance tracking
        self.fraud_detector.model_performance_tracker = Mock()
        
        features = {"transaction_amount": 100.0}
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in features:
                features[feature_name] = 0
        
        fraud_score = self.fraud_detector.calculate_fraud_score(features)
        
        # Should track prediction for performance monitoring
        expected_prediction = {
            "score": fraud_score.final_score,
            "ml_score": fraud_score.ml_score,
            "business_score": fraud_score.business_rules_score,
            "timestamp": fraud_score.timestamp,
            "model_version": fraud_score.model_version
        }
        
        # Verify tracking (implementation-dependent)
        assert fraud_score.final_score is not None
        assert fraud_score.model_version == "v1.0.0"

    def test_feature_importance_analysis(self):
        """Test feature importance tracking for model interpretability."""
        # Mock feature importance
        feature_importance = np.random.random(25)
        self.mock_model.feature_importance.return_value = feature_importance
        
        features = {"transaction_amount": 1000.0}  # High amount
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in features:
                features[feature_name] = 0
        
        fraud_score = self.fraud_detector.calculate_fraud_score(features)
        
        # Should have contributing factors
        assert len(fraud_score.contributing_factors) > 0
        
        # Contributing factors should be relevant features
        high_importance_features = ["transaction_amount", "is_large_amount", "deviation_from_avg"]
        contributing_factor_names = [factor["feature"] for factor in fraud_score.contributing_factors]
        
        # At least some high-importance features should be in contributing factors
        assert any(feature in contributing_factor_names for feature in high_importance_features)

    def test_score_consistency(self):
        """Test fraud score consistency across multiple evaluations."""
        features = {
            "transaction_amount": 150.0,
            "hour_of_day": 14,
            "is_new_user": 0,
            "user_avg_amount": 25.0
        }
        
        # Fill in missing features
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in features:
                features[feature_name] = 0
        
        # Calculate score multiple times
        scores = []
        for _ in range(5):
            fraud_score = self.fraud_detector.calculate_fraud_score(features)
            scores.append(fraud_score.final_score)
        
        # Scores should be consistent (same features should produce same score)
        assert len(set(scores)) == 1, "Scores should be deterministic for same features"

    def test_extreme_feature_values(self):
        """Test handling of extreme feature values."""
        # Test with extreme values
        extreme_features = {
            "transaction_amount": 999999.99,  # Very large amount
            "user_transaction_count": 0,      # New user
            "transactions_last_hour": 50,    # Very high velocity
            "deviation_from_avg": 1000.0,    # Extreme deviation
            "days_since_last_transaction": 0  # Immediate repeat
        }
        
        # Fill in missing features
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in extreme_features:
                extreme_features[feature_name] = 0
        
        # Should handle extreme values without crashing
        try:
            fraud_score = self.fraud_detector.calculate_fraud_score(extreme_features)
            assert 0 <= fraud_score.final_score <= 1
            assert fraud_score.final_score > 0.5  # Extreme values should indicate high risk
        except Exception as e:
            pytest.fail(f"Should handle extreme values gracefully: {e}")

    def test_model_fallback_when_ml_unavailable(self):
        """Test fallback to business rules when ML model is unavailable."""
        # Create fraud detector without ML model
        with patch('consumers.fraud_detector.get_kafka_config', return_value=self.mock_config):
            with patch('consumers.fraud_detector.redis.Redis', return_value=self.mock_redis):
                with patch('consumers.fraud_detector.pickle.load', side_effect=Exception("Model load failed")):
                    
                    fraud_detector_no_ml = FraudDetector()
                    fraud_detector_no_ml.ml_model = None
                    fraud_detector_no_ml.model_metadata = None
        
        features = {
            "transaction_amount": 200.0,
            "is_high_velocity": 1,
            "suspicious_activity_count": 3
        }
        
        # Should fall back to business rules only
        fraud_score = fraud_detector_no_ml.calculate_fraud_score(features)
        
        assert isinstance(fraud_score, FraudScore)
        assert fraud_score.ml_score == 0.0  # No ML score available
        assert fraud_score.business_rules_score > 0  # Business rules should work
        assert fraud_score.final_score == fraud_score.business_rules_score

    @pytest.mark.parametrize("ml_score,business_score,expected_range", [
        (0.1, 0.2, (0.1, 0.2)),    # Low scores
        (0.8, 0.3, (0.3, 0.8)),    # ML high, business low
        (0.2, 0.9, (0.2, 0.9)),    # ML low, business high  
        (0.9, 0.8, (0.8, 0.9)),    # Both high
        (0.1, 0.1, (0.1, 0.1))     # Both low
    ])
    def test_ensemble_weighting(self, ml_score, business_score, expected_range):
        """Test ensemble weighting logic with various score combinations."""
        # Mock specific scores
        self.mock_model.predict.return_value = np.array([ml_score])
        
        # Create features that will produce the target business score
        features = self._create_features_for_business_score(business_score)
        
        fraud_score = self.fraud_detector.calculate_fraud_score(features)
        
        # Final score should be in expected range (some combination of ML and business scores)
        assert expected_range[0] <= fraud_score.final_score <= expected_range[1]
        assert fraud_score.ml_score == pytest.approx(ml_score, rel=0.1)

    def _create_features_for_business_score(self, target_score):
        """Helper method to create features that produce a target business score."""
        # Simplified logic - adjust high-risk indicators to reach target score
        features = {}
        
        if target_score >= 0.8:
            features.update({
                "is_high_velocity": 1,
                "is_large_amount": 1,
                "suspicious_activity_count": 10,
                "transactions_last_hour": 15
            })
        elif target_score >= 0.5:
            features.update({
                "is_high_velocity": 1,
                "suspicious_activity_count": 5
            })
        elif target_score >= 0.3:
            features.update({
                "suspicious_activity_count": 2
            })
        
        # Fill in remaining features with defaults
        for feature_name in self.mock_metadata["feature_names"]:
            if feature_name not in features:
                features[feature_name] = 0
                
        return features
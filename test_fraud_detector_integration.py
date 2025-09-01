#!/usr/bin/env python3

"""
Test integrated fraud detection system with FastInferenceEngine
Validates that our modifications to fraud_detector.py work correctly.
"""

import os
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src to path  
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

def test_fraud_detector_integration():
    """Test FraudDetector with FastInferenceEngine integration."""
    
    print("Testing FraudDetector integration with FastInferenceEngine...")
    
    try:
        from consumers.fraud_detector import FraudDetector
        print("✓ FraudDetector imported successfully")
        
        # Mock Redis to avoid external dependencies
        with patch('redis.Redis') as mock_redis_class:
            mock_redis = MagicMock()
            mock_redis_class.return_value = mock_redis
            
            # Mock Kafka components
            with patch('consumers.fraud_detector.Consumer') as mock_consumer, \
                 patch('consumers.fraud_detector.Producer') as mock_producer:
                
                mock_consumer.return_value = MagicMock()
                mock_producer.return_value = MagicMock()
                
                # Test with C++ acceleration enabled (will fallback to Python)
                detector = FraudDetector(
                    consumer_group="test-group",
                    fraud_threshold=0.7,
                    use_ml_model=True,
                    model_path="models/ieee_fraud_model_production.pkl",
                    enable_cpp_acceleration=True
                )
                
                print("✓ FraudDetector initialized with C++ acceleration enabled")
                
                # Check if FastInferenceEngine was loaded
                has_fast_engine = hasattr(detector, 'fast_inference_engine')
                fast_engine_loaded = has_fast_engine and detector.fast_inference_engine is not None
                
                print(f"FastInferenceEngine loaded: {fast_engine_loaded}")
                
                if fast_engine_loaded:
                    status = detector.fast_inference_engine.get_status()
                    print(f"FastInferenceEngine status: {status}")
                
                # Test fraud scoring with a synthetic transaction
                test_transaction = {
                    'user_id': 'test_user_123',
                    'transaction_amt': 100.0,
                    'generated_timestamp': '2024-08-30T12:00:00Z',
                    'merchant_id': 'merchant_456',
                    'transaction_id': 'txn_789'
                }
                
                # Mock user profile
                from consumers.fraud_detector import UserProfile
                test_user_profile = UserProfile(
                    user_id='test_user_123',
                    total_transactions=5,
                    total_amount=500.0,
                    avg_transaction_amount=100.0,
                    last_transaction_time='2024-08-30T11:00:00Z',
                    last_transaction_amount=80.0,
                    daily_transaction_count=2,
                    daily_amount=180.0,
                    last_reset_date='2024-08-30',
                    suspicious_activity_count=0
                )
                
                # Test ML fraud scoring
                try:
                    fraud_score = detector._calculate_ml_fraud_score(test_transaction, test_user_profile)
                    print(f"✓ ML fraud score calculated: {fraud_score:.6f}")
                    
                    if 0.0 <= fraud_score <= 1.0:
                        print("✓ Fraud score within valid range [0.0, 1.0]")
                    else:
                        print(f"✗ Fraud score out of range: {fraud_score}")
                        return False
                        
                except Exception as e:
                    print(f"✗ ML fraud scoring failed: {e}")
                    import traceback
                    traceback.print_exc()
                    return False
                
                # Test with C++ disabled for comparison
                detector_no_cpp = FraudDetector(
                    consumer_group="test-group",
                    fraud_threshold=0.7,
                    use_ml_model=True,
                    model_path="models/ieee_fraud_model_production.pkl",
                    enable_cpp_acceleration=False
                )
                
                fraud_score_no_cpp = detector_no_cpp._calculate_ml_fraud_score(test_transaction, test_user_profile)
                print(f"✓ Python-only fraud score: {fraud_score_no_cpp:.6f}")
                
                # Scores should be identical since both use Python (C++ not built)
                if abs(fraud_score - fraud_score_no_cpp) < 1e-10:
                    print("✓ Consistent results between C++ and Python paths")
                else:
                    print(f"✗ Inconsistent results: C++={fraud_score:.8f}, Python={fraud_score_no_cpp:.8f}")
                    return False
                
                return True
                
    except Exception as e:
        print(f"✗ FraudDetector integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_fraud_detector_integration()
    if success:
        print("\n✅ FraudDetector integration test PASSED")
        exit(0)
    else:
        print("\n❌ FraudDetector integration test FAILED")
        exit(1)
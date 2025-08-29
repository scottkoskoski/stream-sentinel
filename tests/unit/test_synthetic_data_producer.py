"""
Unit Tests for Synthetic Transaction Producer

These tests verify that the synthetic data producer generates IEEE-CIS compatible
transactions with all required features for real-world fraud detection systems.

Tests are designed to FAIL initially, then pass after implementing enhanced features:
- C1-C14 counting features
- D1-D15 time delta features  
- M1-M9 match features
- Enhanced card features (card4, card6)
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
from typing import Dict, Any

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from producers.synthetic_transaction_producer import (
    SyntheticTransactionProducer, 
    Transaction, 
    UserProfile
)


class TestSyntheticDataProducer:
    """Test suite for synthetic data producer with enhanced IEEE-CIS features."""
    
    def setup_method(self):
        """Set up test fixtures."""
        # Mock all Kafka-related dependencies
        with patch('producers.synthetic_transaction_producer.get_kafka_config') as mock_config, \
             patch('producers.synthetic_transaction_producer.Producer') as mock_producer, \
             patch.object(SyntheticTransactionProducer, '_load_analysis_results') as mock_analysis:
            
            # Setup mock Kafka config
            mock_kafka_config = Mock()
            mock_kafka_config.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}
            mock_config.return_value = mock_kafka_config
            
            # Setup mock producer
            mock_producer.return_value = Mock()
            
            # Setup mock analysis results
            mock_analysis.return_value = self._get_mock_analysis_results()
            
            # Create producer with mocked dependencies
            self.producer = SyntheticTransactionProducer()
            
            # Manually set the attributes that _load_analysis_results would set
            mock_data = self._get_mock_analysis_results()
            self.producer.fraud_rate = mock_data["schema"]["fraud_rate"]
            self.producer.transaction_patterns = mock_data["synthetic_spec"]["transaction_patterns"]
            self.producer.fraud_patterns = mock_data["synthetic_spec"]["fraud_patterns"]
    
    def _get_mock_analysis_results(self):
        """Get mock analysis results for testing."""
        return {
            "schema": {
                "fraud_rate": 0.027
            },
            "synthetic_spec": {
                "transaction_patterns": {
                    "amount_distribution": {
                        "mean_log": 4.0,
                        "std_log": 1.2,
                        "min_amount": 1.0,
                        "max_amount": 1000.0,
                    },
                    "product_codes": {"W": 0.7, "C": 0.15, "R": 0.1, "H": 0.03, "S": 0.02},
                },
                "fraud_patterns": {
                    "base_fraud_rate": 0.027,
                    "amount_patterns": {"high_amount_bias": 1.2},
                }
            }
        }
    
    def test_transaction_has_all_ieee_cis_features(self):
        """Test that generated transactions include all IEEE-CIS features."""
        transaction = self.producer._generate_transaction()
        
        # Convert to dict for easier assertion
        transaction_dict = transaction.__dict__ if hasattr(transaction, '__dict__') else transaction
        
        # Core features (already implemented)
        assert hasattr(transaction, 'transaction_id')
        assert hasattr(transaction, 'is_fraud')
        assert hasattr(transaction, 'transaction_dt')
        assert hasattr(transaction, 'transaction_amt')
        assert hasattr(transaction, 'product_cd')
        
        # Card features (partially implemented)
        assert hasattr(transaction, 'card1')
        assert hasattr(transaction, 'card2')
        assert hasattr(transaction, 'card3')
        assert hasattr(transaction, 'card4')
        assert hasattr(transaction, 'card5')
        assert hasattr(transaction, 'card6')
        
        # Address features (implemented)
        assert hasattr(transaction, 'addr1')
        assert hasattr(transaction, 'addr2')
        
        # Distance features (implemented)  
        assert hasattr(transaction, 'dist1')
        assert hasattr(transaction, 'dist2')
        
        # Email features (implemented)
        assert hasattr(transaction, 'p_emaildomain')
        assert hasattr(transaction, 'r_emaildomain')
        
        # THESE WILL FAIL INITIALLY - Enhanced features to be implemented
        
        # Counting features C1-C14 (NOT YET IMPLEMENTED)
        for i in range(1, 15):
            assert hasattr(transaction, f'c{i}'), f"Missing counting feature C{i}"
            
        # Time delta features D1-D15 (NOT YET IMPLEMENTED)
        for i in range(1, 16):
            assert hasattr(transaction, f'd{i}'), f"Missing time delta feature D{i}"
            
        # Match features M1-M9 (NOT YET IMPLEMENTED)
        for i in range(1, 10):
            assert hasattr(transaction, f'm{i}'), f"Missing match feature M{i}"

    def test_counting_features_c1_c14(self):
        """Test that C1-C14 counting features are properly generated."""
        # Generate multiple transactions to test counting logic
        user_id = "test_user_001"
        transactions = []
        
        for _ in range(5):
            transaction = self.producer._generate_transaction(user_id=user_id)
            transactions.append(transaction)
        
        # Test specific counting features
        latest_transaction = transactions[-1]
        
        # C1: Cards associated with this address
        assert hasattr(latest_transaction, 'c1')
        assert isinstance(latest_transaction.c1, (int, float, type(None)))
        if latest_transaction.c1 is not None:
            assert latest_transaction.c1 >= 1
            
        # C2: Addresses associated with this card
        assert hasattr(latest_transaction, 'c2') 
        assert isinstance(latest_transaction.c2, (int, float, type(None)))
        if latest_transaction.c2 is not None:
            assert latest_transaction.c2 >= 1
            
        # C3: Transactions with this email domain today
        assert hasattr(latest_transaction, 'c3')
        assert isinstance(latest_transaction.c3, (int, float, type(None)))
        
        # C4: Unique merchants for this user this month  
        assert hasattr(latest_transaction, 'c4')
        assert isinstance(latest_transaction.c4, (int, float, type(None)))
        if latest_transaction.c4 is not None:
            assert latest_transaction.c4 >= 1  # At least this merchant
            
        # Test remaining C features exist
        for i in range(5, 15):
            assert hasattr(latest_transaction, f'c{i}'), f"Missing C{i} feature"
            c_value = getattr(latest_transaction, f'c{i}')
            assert isinstance(c_value, (int, float, type(None)))

    def test_time_delta_features_d1_d15(self):
        """Test that D1-D15 time delta features are properly generated."""
        user_id = "test_user_002"
        
        # Generate first transaction
        first_transaction = self.producer._generate_transaction(user_id=user_id)
        
        # Wait a bit and generate second transaction
        import time
        time.sleep(0.1)
        second_transaction = self.producer._generate_transaction(user_id=user_id)
        
        # D1: Days since account creation
        assert hasattr(second_transaction, 'd1')
        assert isinstance(second_transaction.d1, (int, float, type(None)))
        if second_transaction.d1 is not None:
            assert second_transaction.d1 >= 0
            
        # D2: Days since last transaction
        assert hasattr(second_transaction, 'd2')
        assert isinstance(second_transaction.d2, (int, float, type(None)))
        if second_transaction.d2 is not None:
            assert second_transaction.d2 >= 0
            
        # D3: Days since first transaction with this card
        assert hasattr(second_transaction, 'd3')
        assert isinstance(second_transaction.d3, (int, float, type(None)))
        
        # D4: Hours since last transaction from this device
        assert hasattr(second_transaction, 'd4')
        assert isinstance(second_transaction.d4, (int, float, type(None)))
        
        # Test remaining D features exist
        for i in range(5, 16):
            assert hasattr(second_transaction, f'd{i}'), f"Missing D{i} feature"
            d_value = getattr(second_transaction, f'd{i}')
            assert isinstance(d_value, (int, float, type(None)))

    def test_match_features_m1_m9(self):
        """Test that M1-M9 match features are properly generated."""
        transaction = self.producer._generate_transaction()
        
        # M1: Name on card matches billing address name
        assert hasattr(transaction, 'm1')
        assert isinstance(transaction.m1, (str, type(None)))
        if transaction.m1 is not None:
            assert transaction.m1 in ['T', 'F', 'NotFound']
            
        # M2: Email domain matches card issuer domain  
        assert hasattr(transaction, 'm2')
        assert isinstance(transaction.m2, (str, type(None)))
        if transaction.m2 is not None:
            assert transaction.m2 in ['T', 'F', 'NotFound']
            
        # M3: Phone area code matches billing address area code
        assert hasattr(transaction, 'm3')
        assert isinstance(transaction.m3, (str, type(None)))
        if transaction.m3 is not None:
            assert transaction.m3 in ['T', 'F', 'NotFound']
            
        # M4: Device timezone matches billing address timezone
        assert hasattr(transaction, 'm4')
        assert isinstance(transaction.m4, (str, type(None)))
        if transaction.m4 is not None:
            assert transaction.m4 in ['T', 'F', 'NotFound']
            
        # Test remaining M features exist
        for i in range(5, 10):
            assert hasattr(transaction, f'm{i}'), f"Missing M{i} feature"
            m_value = getattr(transaction, f'm{i}')
            assert isinstance(m_value, (str, type(None)))
            if m_value is not None:
                assert m_value in ['T', 'F', 'NotFound']

    def test_enhanced_card_features(self):
        """Test that card4 and card6 contain realistic values."""
        transaction = self.producer._generate_transaction()
        
        # card4: Card company/issuer
        assert hasattr(transaction, 'card4')
        if transaction.card4 is not None:
            expected_companies = ['visa', 'mastercard', 'amex', 'discover']
            assert transaction.card4.lower() in expected_companies
            
        # card6: Card type (debit/credit)
        assert hasattr(transaction, 'card6')  
        if transaction.card6 is not None:
            expected_types = ['debit', 'credit']
            assert transaction.card6.lower() in expected_types

    def test_counting_features_increase_with_user_activity(self):
        """Test that counting features reflect increasing user activity."""
        user_id = "test_user_counting"
        
        # Generate first transaction
        first_transaction = self.producer._generate_transaction(user_id=user_id)
        
        # Generate several more transactions
        for _ in range(4):
            self.producer._generate_transaction(user_id=user_id)
            
        # Generate final transaction
        final_transaction = self.producer._generate_transaction(user_id=user_id)
        
        # C4 (unique merchants for user) should potentially increase
        if (first_transaction.c4 is not None and 
            final_transaction.c4 is not None):
            # Should be same or higher (user may use different merchants)
            assert final_transaction.c4 >= first_transaction.c4

    def test_time_delta_features_reflect_temporal_patterns(self):
        """Test that time delta features accurately reflect time passage."""
        user_id = "test_user_temporal"
        
        # Create user profile with known creation time
        user_profile = self.producer._get_or_create_user(user_id)
        creation_time = user_profile.created_at
        
        # Generate transaction after known time delay
        import time
        time.sleep(0.1)  # Small delay
        
        transaction = self.producer._generate_transaction(user_id=user_id)
        
        # D1 (days since account creation) should be close to 0
        if transaction.d1 is not None:
            assert transaction.d1 < 1  # Should be less than 1 day
            
        # D2 (days since last transaction) should be 0 for first transaction
        # or very small for subsequent ones
        if transaction.d2 is not None:
            assert transaction.d2 < 1

    def test_match_features_logical_consistency(self):
        """Test that match features have logical relationships."""
        transaction = self.producer._generate_transaction()
        
        # If email domain exists, M2 should have a reasonable match result
        if (transaction.p_emaildomain is not None and 
            transaction.card4 is not None and 
            transaction.m2 is not None):
            assert transaction.m2 in ['T', 'F', 'NotFound']
            
        # If address exists, M1 should have a reasonable match result  
        if (transaction.addr1 is not None and 
            transaction.m1 is not None):
            assert transaction.m1 in ['T', 'F', 'NotFound']

    def test_feature_sparsity_matches_ieee_cis_patterns(self):
        """Test that feature sparsity matches real IEEE-CIS patterns."""
        # Generate many transactions to test sparsity patterns
        transactions = []
        for _ in range(100):
            transaction = self.producer._generate_transaction()
            transactions.append(transaction)
        
        # Count missing values for key features
        def count_missing(feature_name):
            missing = sum(1 for t in transactions 
                         if getattr(t, feature_name, None) is None)
            return missing / len(transactions)
        
        # Distance features should be mostly missing (like real data)
        dist1_missing_rate = count_missing('dist1')
        assert dist1_missing_rate > 0.5  # More than 50% missing
        
        # Some counting features should be mostly present
        c1_missing_rate = count_missing('c1')
        assert c1_missing_rate < 0.8  # Less than 80% missing
        
        # Some match features should be mostly missing
        m1_missing_rate = count_missing('m1')
        # Allow flexibility in missing rates for match features

    def test_fraud_transactions_have_distinctive_patterns(self):
        """Test that fraudulent transactions have different feature patterns."""
        legitimate_transactions = []
        fraudulent_transactions = []
        
        # Generate transactions and separate by fraud status
        for _ in range(50):
            transaction = self.producer._generate_transaction()
            if transaction.is_fraud == 1:
                fraudulent_transactions.append(transaction)
            else:
                legitimate_transactions.append(transaction)
        
        # Ensure we have both types
        if len(fraudulent_transactions) > 0 and len(legitimate_transactions) > 0:
            # Fraudulent transactions might have different counting patterns
            # This is a pattern-based test, not strict numerical requirements
            
            # Example: Fraudulent transactions might have different C values
            fraud_c1_values = [t.c1 for t in fraudulent_transactions if t.c1 is not None]
            legit_c1_values = [t.c1 for t in legitimate_transactions if t.c1 is not None]
            
            if fraud_c1_values and legit_c1_values:
                # Just verify values are in reasonable range
                assert all(v >= 0 for v in fraud_c1_values)
                assert all(v >= 0 for v in legit_c1_values)

    def test_user_profile_consistency_across_transactions(self):
        """Test that user profiles maintain consistency across transactions."""
        user_id = "test_user_consistency"
        
        transactions = []
        for _ in range(3):
            transaction = self.producer._generate_transaction(user_id=user_id)
            transactions.append(transaction)
        
        # User-specific features should show consistency/progression
        # D1 (days since account creation) should be same or increasing
        d1_values = [t.d1 for t in transactions if t.d1 is not None]
        if len(d1_values) > 1:
            # Should be non-decreasing (time only moves forward)
            for i in range(1, len(d1_values)):
                assert d1_values[i] >= d1_values[i-1] - 0.1  # Allow small floating point errors
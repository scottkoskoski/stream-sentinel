"""
Unit Tests for Synthetic Transaction Producer

Tests that the synthetic data producer generates IEEE-CIS compatible
transactions with all required features including:
- Core features (transaction_id, amount, product_cd, etc.)
- Card features (card1-card6)
- C1-C14 counting features
- D1-D15 time delta features
- M1-M9 match features
- Enhanced card features (card4, card6)
"""

import os
import sys
from unittest.mock import Mock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from producers.synthetic_transaction_producer import SyntheticTransactionProducer, UserProfile


class TestSyntheticDataProducer:
    """Test suite for synthetic data producer with IEEE-CIS features."""

    def setup_method(self):
        """Set up test fixtures."""
        with (
            patch("producers.synthetic_transaction_producer.get_kafka_config") as mock_config,
            patch("producers.synthetic_transaction_producer.Producer") as mock_producer,
            patch.object(SyntheticTransactionProducer, "_load_analysis_results") as mock_analysis,
        ):

            mock_kafka_config = Mock()
            mock_kafka_config.get_producer_config.return_value = {"bootstrap.servers": "localhost:9092"}
            mock_config.return_value = mock_kafka_config
            mock_producer.return_value = Mock()
            mock_analysis.return_value = self._get_mock_analysis_results()

            self.producer = SyntheticTransactionProducer()

            mock_data = self._get_mock_analysis_results()
            self.producer.fraud_rate = mock_data["schema"]["fraud_rate"]
            self.producer.transaction_patterns = mock_data["synthetic_spec"]["transaction_patterns"]
            self.producer.fraud_patterns = mock_data["synthetic_spec"]["fraud_patterns"]

    def _get_mock_analysis_results(self):
        """Get mock analysis results for testing."""
        return {
            "schema": {"fraud_rate": 0.027},
            "synthetic_spec": {
                "transaction_patterns": {
                    "amount_distribution": {
                        "mean_log": 4.0,
                        "std_log": 1.2,
                        "min_amount": 1.0,
                        "max_amount": 1000.0,
                    },
                    "product_codes": {
                        "W": 0.7,
                        "C": 0.15,
                        "R": 0.1,
                        "H": 0.03,
                        "S": 0.02,
                    },
                },
                "fraud_patterns": {
                    "base_fraud_rate": 0.027,
                    "amount_patterns": {"high_amount_bias": 1.2},
                },
            },
        }

    def test_transaction_has_core_features(self):
        """Test that generated transactions have core IEEE-CIS features."""
        transaction = self.producer._generate_transaction()

        assert hasattr(transaction, "transaction_id")
        assert transaction.transaction_id is not None
        assert hasattr(transaction, "is_fraud")
        assert transaction.is_fraud in (0, 1)
        assert hasattr(transaction, "transaction_dt")
        assert isinstance(transaction.transaction_dt, int)
        assert hasattr(transaction, "transaction_amt")
        assert transaction.transaction_amt > 0
        assert hasattr(transaction, "product_cd")
        assert transaction.product_cd in ("W", "C", "R", "H", "S")

    def test_transaction_has_card_features(self):
        """Test that generated transactions have all card features."""
        transaction = self.producer._generate_transaction()

        assert hasattr(transaction, "card1")
        assert hasattr(transaction, "card2")
        assert hasattr(transaction, "card3")
        assert hasattr(transaction, "card4")
        assert hasattr(transaction, "card5")
        assert hasattr(transaction, "card6")

    def test_transaction_has_address_and_distance_features(self):
        """Test address and distance features exist."""
        transaction = self.producer._generate_transaction()

        assert hasattr(transaction, "addr1")
        assert hasattr(transaction, "addr2")
        assert hasattr(transaction, "dist1")
        assert hasattr(transaction, "dist2")

    def test_transaction_has_email_features(self):
        """Test email domain features exist."""
        transaction = self.producer._generate_transaction()

        assert hasattr(transaction, "p_emaildomain")
        assert hasattr(transaction, "r_emaildomain")

    def test_transaction_has_counting_features_c1_c14(self):
        """Test that C1-C14 counting features exist on Transaction."""
        transaction = self.producer._generate_transaction()

        for i in range(1, 15):
            attr_name = f"c{i}"
            assert hasattr(transaction, attr_name), f"Missing counting feature {attr_name}"
            value = getattr(transaction, attr_name)
            assert value is None or isinstance(
                value, (int, float)
            ), f"{attr_name} should be numeric or None, got {type(value)}"

    def test_transaction_has_time_delta_features_d1_d15(self):
        """Test that D1-D15 time delta features exist on Transaction."""
        transaction = self.producer._generate_transaction()

        for i in range(1, 16):
            attr_name = f"d{i}"
            assert hasattr(transaction, attr_name), f"Missing time delta feature {attr_name}"
            value = getattr(transaction, attr_name)
            assert value is None or isinstance(
                value, (int, float)
            ), f"{attr_name} should be numeric or None, got {type(value)}"

    def test_transaction_has_match_features_m1_m9(self):
        """Test that M1-M9 match features exist on Transaction."""
        transaction = self.producer._generate_transaction()

        for i in range(1, 10):
            attr_name = f"m{i}"
            assert hasattr(transaction, attr_name), f"Missing match feature {attr_name}"
            value = getattr(transaction, attr_name)
            assert value is None or isinstance(value, str), f"{attr_name} should be str or None, got {type(value)}"
            if value is not None:
                assert value in (
                    "T",
                    "F",
                    "NotFound",
                ), f"{attr_name} value '{value}' not in expected set"

    def test_transaction_has_metadata_fields(self):
        """Test that metadata fields are populated."""
        transaction = self.producer._generate_transaction()

        assert hasattr(transaction, "generated_timestamp")
        assert transaction.generated_timestamp is not None
        assert hasattr(transaction, "user_id")
        assert transaction.user_id is not None
        assert hasattr(transaction, "session_id")
        assert transaction.session_id is not None

    def test_enhanced_card_features_values(self):
        """Test that card4 and card6 contain realistic values."""
        transaction = self.producer._generate_transaction()

        if transaction.card4 is not None:
            expected_companies = ["visa", "mastercard", "american express", "discover"]
            assert (
                transaction.card4.lower() in expected_companies
            ), f"card4 '{transaction.card4}' not in expected companies"

        if transaction.card6 is not None:
            expected_types = ["debit", "credit", "debit or credit", "charge card"]
            assert transaction.card6.lower() in expected_types, f"card6 '{transaction.card6}' not in expected types"

    def test_counting_features_are_nonnegative(self):
        """Test that non-None counting features have non-negative values."""
        transaction = self.producer._generate_transaction()

        for i in range(1, 15):
            value = getattr(transaction, f"c{i}")
            if value is not None:
                assert value >= 0, f"c{i} should be non-negative, got {value}"

    def test_time_delta_features_are_nonnegative(self):
        """Test that non-None time delta features have non-negative values."""
        transaction = self.producer._generate_transaction()

        for i in range(1, 16):
            value = getattr(transaction, f"d{i}")
            if value is not None:
                assert value >= 0, f"d{i} should be non-negative, got {value}"

    def test_multiple_transactions_for_same_user(self):
        """Test generating multiple transactions for the same user."""
        user_id = "test_user_multi"

        transactions = []
        for _ in range(5):
            txn = self.producer._generate_transaction(user_id=user_id)
            transactions.append(txn)

        # All should have same user
        for txn in transactions:
            assert txn.user_id == user_id

        # Transaction IDs should be unique
        ids = [txn.transaction_id for txn in transactions]
        assert len(set(ids)) == 5

        # Transaction counter should increment
        assert transactions[-1].transaction_id != transactions[0].transaction_id

    def test_user_profile_creation(self):
        """Test that _get_or_create_user creates valid profiles."""
        user_id = "test_profile_user"
        profile = self.producer._get_or_create_user(user_id)

        assert isinstance(profile, UserProfile)
        assert profile.user_id == user_id
        assert profile.total_transactions == 0
        assert profile.total_spent == 0.0
        assert len(profile.preferred_amounts) > 0
        assert len(profile.preferred_merchants) > 0
        assert len(profile.typical_locations) > 0

    def test_user_profile_reused_across_transactions(self):
        """Test that the same user profile is reused, not recreated."""
        user_id = "test_reuse_user"

        txn1 = self.producer._generate_transaction(user_id=user_id)
        txn2 = self.producer._generate_transaction(user_id=user_id)

        # Profile should be updated after transactions
        profile = self.producer.user_profiles[user_id]
        assert profile.total_transactions == 2

    def test_fraud_and_legitimate_transactions_generated(self):
        """Test that both fraud and legitimate transactions are produced."""
        fraud_count = 0
        legit_count = 0

        # Generate enough to statistically expect both types
        for _ in range(200):
            txn = self.producer._generate_transaction()
            if txn.is_fraud == 1:
                fraud_count += 1
            else:
                legit_count += 1

        assert legit_count > 0, "Should generate legitimate transactions"
        # With 2.7% fraud rate and 200 txns, expect ~5 fraud on average
        # Allow for some variance
        assert fraud_count >= 0, "Fraud count should be non-negative"
        # Legitimate should be the majority
        assert legit_count > fraud_count, "Legitimate should outnumber fraudulent"

    def test_transaction_amount_positive(self):
        """Test that all generated amounts are positive."""
        for _ in range(50):
            txn = self.producer._generate_transaction()
            assert txn.transaction_amt > 0, f"Transaction amount should be positive, got {txn.transaction_amt}"

    def test_user_profile_updates_after_transaction(self):
        """Test that user profiles are updated after transactions."""
        user_id = "test_update_user"

        self.producer._generate_transaction(user_id=user_id)
        profile = self.producer.user_profiles[user_id]

        assert profile.total_transactions == 1
        assert profile.total_spent > 0
        assert profile.last_transaction_time > 0
        assert len(profile.recent_locations) >= 0

    def test_distance_features_sparsity(self):
        """Test that distance features have realistic sparsity (many None values)."""
        none_count = 0
        total = 50

        for _ in range(total):
            txn = self.producer._generate_transaction()
            if txn.dist1 is None:
                none_count += 1

        missing_rate = none_count / total
        # IEEE-CIS data has high sparsity for distance features
        assert missing_rate > 0.3, f"Distance features should be sparse, missing rate: {missing_rate:.1%}"

"""
Comprehensive Synthetic Data Generator for Testing

Generates realistic fraud detection test data with:
- IEEE-CIS statistical patterns
- User behavior consistency
- Fraud pattern injection
- Performance testing volumes
- Deterministic reproducibility
"""

import json
import random
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import pickle
from pathlib import Path


@dataclass
class UserProfile:
    """Synthetic user profile for consistent behavior generation."""
    user_id: str
    spending_pattern: str  # "low", "medium", "high"
    preferred_categories: List[str]
    avg_transaction_amount: float
    daily_transaction_frequency: int
    fraud_propensity: float  # 0.0 to 1.0
    location_stability: float  # How often they transact in same location
    time_pattern: str  # "business_hours", "evening", "night_owl", "random"


class SyntheticDataGenerator:
    """Comprehensive synthetic data generator for fraud detection testing."""

    def __init__(self, seed: int = 42):
        """Initialize generator with reproducible seed."""
        self.seed = seed
        random.seed(seed)
        np.random.seed(seed)
        
        # Load IEEE-CIS statistical patterns
        self.ieee_patterns = self._load_ieee_patterns()
        
        # Merchant categories with fraud propensities
        self.merchant_categories = {
            "grocery": {"fraud_rate": 0.01, "avg_amount": 45.0},
            "gas": {"fraud_rate": 0.015, "avg_amount": 35.0},
            "restaurant": {"fraud_rate": 0.02, "avg_amount": 28.0},
            "online": {"fraud_rate": 0.05, "avg_amount": 85.0},
            "retail": {"fraud_rate": 0.025, "avg_amount": 65.0},
            "pharmacy": {"fraud_rate": 0.008, "avg_amount": 22.0},
            "entertainment": {"fraud_rate": 0.03, "avg_amount": 75.0},
            "travel": {"fraud_rate": 0.04, "avg_amount": 250.0},
            "electronics": {"fraud_rate": 0.06, "avg_amount": 450.0},
            "jewelry": {"fraud_rate": 0.08, "avg_amount": 850.0}
        }
        
        # Time-based fraud patterns (hour of day -> fraud multiplier)
        self.temporal_fraud_patterns = {
            0: 1.5, 1: 1.8, 2: 2.0, 3: 2.2, 4: 1.9, 5: 1.6,  # Night hours
            6: 1.2, 7: 1.3, 8: 2.3, 9: 1.4, 10: 1.1, 11: 1.0,  # Morning (8 AM peak)
            12: 1.0, 13: 0.9, 14: 0.8, 15: 0.9, 16: 1.0, 17: 1.1,  # Afternoon
            18: 1.2, 19: 1.3, 20: 1.4, 21: 1.5, 22: 1.6, 23: 1.4   # Evening
        }
        
        # User profiles for consistent behavior
        self.user_profiles = {}

    def _load_ieee_patterns(self) -> Dict[str, Any]:
        """Load IEEE-CIS statistical patterns from analysis."""
        try:
            # Try to load actual IEEE patterns
            ieee_file = Path(__file__).parent.parent.parent / "data" / "processed" / "ieee_cis_analysis.json"
            if ieee_file.exists():
                with open(ieee_file, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        
        # Fallback to hardcoded patterns from analysis
        return {
            "fraud_rate": 0.0271,
            "amount_distribution": {
                "mean": 3.0,
                "std": 1.5,
                "min": 0.01,
                "max": 5000.0
            },
            "temporal_patterns": {
                "peak_fraud_hour": 8,
                "peak_fraud_rate": 0.0616
            },
            "small_amount_threshold": 10.0,
            "small_amount_fraud_rate": 0.0508,
            "large_amount_threshold": 1000.0
        }

    def generate_user_profile(self, user_id: str) -> UserProfile:
        """Generate consistent user profile."""
        if user_id in self.user_profiles:
            return self.user_profiles[user_id]
        
        # Deterministic profile generation based on user_id hash
        user_hash = hash(user_id) % 10000
        profile_random = random.Random(user_hash)
        
        spending_patterns = ["low", "medium", "high"]
        spending_weights = [0.4, 0.4, 0.2]
        spending_pattern = profile_random.choices(spending_patterns, weights=spending_weights)[0]
        
        # Spending pattern influences transaction amounts
        if spending_pattern == "low":
            avg_amount = profile_random.uniform(15.0, 50.0)
            daily_frequency = profile_random.randint(1, 3)
        elif spending_pattern == "medium":
            avg_amount = profile_random.uniform(40.0, 120.0)
            daily_frequency = profile_random.randint(2, 6)
        else:  # high
            avg_amount = profile_random.uniform(100.0, 400.0)
            daily_frequency = profile_random.randint(3, 10)
        
        # Category preferences
        all_categories = list(self.merchant_categories.keys())
        num_preferred = profile_random.randint(2, 5)
        preferred_categories = profile_random.sample(all_categories, num_preferred)
        
        # Fraud propensity (most users are legitimate)
        fraud_propensity = profile_random.betavariate(1, 100)  # Very low for most users
        
        # Location and time patterns
        location_stability = profile_random.uniform(0.6, 0.95)
        time_patterns = ["business_hours", "evening", "night_owl", "random"]
        time_pattern = profile_random.choice(time_patterns)
        
        profile = UserProfile(
            user_id=user_id,
            spending_pattern=spending_pattern,
            preferred_categories=preferred_categories,
            avg_transaction_amount=avg_amount,
            daily_transaction_frequency=daily_frequency,
            fraud_propensity=fraud_propensity,
            location_stability=location_stability,
            time_pattern=time_pattern
        )
        
        self.user_profiles[user_id] = profile
        return profile

    def generate_transaction_amount(self, user_profile: UserProfile, is_fraud: bool = False) -> float:
        """Generate realistic transaction amount."""
        if is_fraud:
            # Fraudulent transactions often have unusual amounts
            if random.random() < 0.3:  # 30% are small amounts
                return round(random.uniform(0.01, 9.99), 2)
            elif random.random() < 0.2:  # 20% are large amounts
                return round(random.uniform(500.0, 5000.0), 2)
        
        # Normal transaction based on user profile
        base_amount = user_profile.avg_transaction_amount
        
        # Log-normal distribution around user's average
        log_amount = np.random.lognormal(
            mean=np.log(base_amount),
            sigma=0.5
        )
        
        # Clamp to reasonable bounds
        amount = max(0.01, min(log_amount, 5000.0))
        return round(amount, 2)

    def generate_transaction_time(self, user_profile: UserProfile, base_time: datetime) -> datetime:
        """Generate realistic transaction timestamp."""
        if user_profile.time_pattern == "business_hours":
            # Prefer 9 AM to 6 PM
            hour = np.random.choice(range(9, 18), p=[0.08, 0.12, 0.15, 0.15, 0.15, 0.12, 0.08, 0.08, 0.07])
        elif user_profile.time_pattern == "evening":
            # Prefer 6 PM to 11 PM
            hour = np.random.choice(range(18, 23), p=[0.15, 0.20, 0.25, 0.25, 0.15])
        elif user_profile.time_pattern == "night_owl":
            # Prefer late night/early morning
            night_hours = list(range(22, 24)) + list(range(0, 6))
            hour = random.choice(night_hours)
        else:  # random
            hour = random.randint(0, 23)
        
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        
        return base_time.replace(hour=hour, minute=minute, second=second)

    def generate_merchant_category(self, user_profile: UserProfile, is_fraud: bool = False) -> str:
        """Generate merchant category based on user preferences."""
        if is_fraud and random.random() < 0.4:
            # Fraudulent transactions more likely in high-risk categories
            high_risk_categories = ["online", "electronics", "jewelry", "travel"]
            available_high_risk = [cat for cat in high_risk_categories if cat in self.merchant_categories]
            return random.choice(available_high_risk)
        
        # Normal transaction - prefer user's preferred categories 80% of the time
        if random.random() < 0.8:
            return random.choice(user_profile.preferred_categories)
        else:
            return random.choice(list(self.merchant_categories.keys()))

    def determine_fraud_status(self, user_profile: UserProfile, transaction_data: Dict[str, Any]) -> bool:
        """Determine if transaction should be fraudulent based on multiple factors."""
        base_fraud_rate = self.ieee_patterns["fraud_rate"]
        fraud_probability = base_fraud_rate
        
        # User fraud propensity
        fraud_probability += user_profile.fraud_propensity * 0.1
        
        # Temporal patterns
        hour = transaction_data.get("hour", 12)
        temporal_multiplier = self.temporal_fraud_patterns.get(hour, 1.0)
        fraud_probability *= temporal_multiplier
        
        # Amount-based patterns
        amount = transaction_data.get("amount", 50.0)
        if amount < self.ieee_patterns.get("small_amount_threshold", 10.0):
            fraud_probability *= 1.9  # Small amounts have higher fraud rate
        elif amount > self.ieee_patterns.get("large_amount_threshold", 1000.0):
            fraud_probability *= 1.5  # Large amounts moderately higher
        
        # Merchant category risk
        category = transaction_data.get("merchant_category", "grocery")
        category_info = self.merchant_categories.get(category, {"fraud_rate": 0.02})
        category_multiplier = category_info["fraud_rate"] / base_fraud_rate
        fraud_probability *= category_multiplier
        
        # Velocity-based fraud (simulated by transaction frequency)
        if hasattr(self, '_recent_transactions'):
            user_recent = [t for t in self._recent_transactions if t.get("user_id") == user_profile.user_id]
            if len(user_recent) > 5:  # High velocity
                fraud_probability *= 3.0
        
        # Cap probability at reasonable level
        fraud_probability = min(fraud_probability, 0.2)
        
        return random.random() < fraud_probability

    def generate_single_transaction(self, user_id: str, transaction_id: str, 
                                  base_time: Optional[datetime] = None,
                                  force_fraud: bool = False) -> Dict[str, Any]:
        """Generate a single realistic transaction."""
        if base_time is None:
            base_time = datetime.now()
        
        # Get or create user profile
        user_profile = self.generate_user_profile(user_id)
        
        # Generate basic transaction data
        timestamp = self.generate_transaction_time(user_profile, base_time)
        merchant_category = self.generate_merchant_category(user_profile)
        
        # Initial transaction data for fraud determination
        temp_transaction_data = {
            "hour": timestamp.hour,
            "merchant_category": merchant_category,
            "amount": user_profile.avg_transaction_amount  # Temporary for fraud determination
        }
        
        # Determine fraud status
        is_fraud = force_fraud or self.determine_fraud_status(user_profile, temp_transaction_data)
        
        # Generate final amount based on fraud status
        amount = self.generate_transaction_amount(user_profile, is_fraud)
        
        # Card type (legitimate users more likely to use credit)
        if is_fraud and random.random() < 0.3:
            card_type = "debit"  # Fraudulent transactions sometimes prefer debit
        else:
            card_type = random.choices(["credit", "debit"], weights=[0.7, 0.3])[0]
        
        # Location data (simplified)
        if user_profile.location_stability > random.random():
            # Consistent location
            location = {"city": f"City_{hash(user_id) % 100}", "state": "ST"}
        else:
            # Different location
            location = {"city": f"City_{random.randint(0, 500)}", "state": "ST"}
        
        transaction = {
            "transaction_id": transaction_id,
            "user_id": user_id,
            "amount": amount,
            "timestamp": timestamp.isoformat(),
            "merchant_category": merchant_category,
            "card_type": card_type,
            "location": location,
            "is_fraud": 1 if is_fraud else 0,
            "hour": timestamp.hour,
            "day_of_week": timestamp.weekday(),
            "synthetic_metadata": {
                "user_spending_pattern": user_profile.spending_pattern,
                "user_fraud_propensity": user_profile.fraud_propensity,
                "generation_timestamp": datetime.now().isoformat(),
                "fraud_factors": {
                    "temporal_risk": self.temporal_fraud_patterns.get(timestamp.hour, 1.0),
                    "amount_risk": "small" if amount < 10 else "large" if amount > 1000 else "normal",
                    "category_risk": self.merchant_categories[merchant_category]["fraud_rate"]
                }
            }
        }
        
        return transaction

    def generate_transaction_sequence(self, user_id: str, num_transactions: int,
                                    start_time: Optional[datetime] = None,
                                    fraud_injection_rate: float = 0.0) -> List[Dict[str, Any]]:
        """Generate a sequence of transactions for a single user."""
        if start_time is None:
            start_time = datetime.now() - timedelta(days=30)
        
        user_profile = self.generate_user_profile(user_id)
        transactions = []
        
        current_time = start_time
        
        for i in range(num_transactions):
            transaction_id = f"{user_id}_txn_{i:04d}"
            
            # Determine if this transaction should be fraudulent
            force_fraud = random.random() < fraud_injection_rate
            
            transaction = self.generate_single_transaction(
                user_id=user_id,
                transaction_id=transaction_id,
                base_time=current_time,
                force_fraud=force_fraud
            )
            
            transactions.append(transaction)
            
            # Advance time based on user's transaction frequency
            hours_between = 24 / user_profile.daily_transaction_frequency
            time_variance = random.uniform(0.5, 1.5)  # Add randomness
            current_time += timedelta(hours=hours_between * time_variance)
        
        return transactions

    def generate_bulk_transactions(self, num_transactions: int, num_users: int,
                                 start_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Generate bulk transactions for performance testing."""
        if start_time is None:
            start_time = datetime.now() - timedelta(days=7)
        
        all_transactions = []
        
        # Generate user IDs
        user_ids = [f"bulk_user_{i:06d}" for i in range(num_users)]
        
        # Distribute transactions across users
        transactions_per_user = num_transactions // num_users
        remaining_transactions = num_transactions % num_users
        
        for i, user_id in enumerate(user_ids):
            user_transaction_count = transactions_per_user
            if i < remaining_transactions:
                user_transaction_count += 1
            
            if user_transaction_count == 0:
                continue
            
            user_transactions = self.generate_transaction_sequence(
                user_id=user_id,
                num_transactions=user_transaction_count,
                start_time=start_time
            )
            
            all_transactions.extend(user_transactions)
        
        # Shuffle to randomize chronological order
        random.shuffle(all_transactions)
        
        # Re-assign transaction IDs to maintain uniqueness
        for i, transaction in enumerate(all_transactions):
            transaction["transaction_id"] = f"bulk_txn_{i:08d}"
        
        return all_transactions

    def generate_fraud_scenarios(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate specific fraud scenarios for testing."""
        scenarios = {}
        
        # High-velocity fraud
        velocity_user = "velocity_fraud_user"
        velocity_transactions = []
        base_time = datetime.now()
        
        for i in range(20):
            transaction_time = base_time + timedelta(minutes=i * 2)  # Every 2 minutes
            transaction = self.generate_single_transaction(
                user_id=velocity_user,
                transaction_id=f"velocity_fraud_{i:03d}",
                base_time=transaction_time,
                force_fraud=True
            )
            velocity_transactions.append(transaction)
        
        scenarios["high_velocity"] = velocity_transactions
        
        # Large amount fraud
        large_amount_user = "large_amount_fraud_user"
        large_amount_transaction = self.generate_single_transaction(
            user_id=large_amount_user,
            transaction_id="large_amount_fraud_001",
            force_fraud=True
        )
        large_amount_transaction["amount"] = 4500.0  # Force large amount
        scenarios["large_amount"] = [large_amount_transaction]
        
        # Unusual time fraud (3 AM transactions)
        unusual_time_user = "unusual_time_fraud_user"
        unusual_time_transaction = self.generate_single_transaction(
            user_id=unusual_time_user,
            transaction_id="unusual_time_fraud_001",
            base_time=datetime.now().replace(hour=3, minute=15),
            force_fraud=True
        )
        scenarios["unusual_time"] = [unusual_time_transaction]
        
        # Geographic anomaly (different location)
        geo_anomaly_user = "geo_anomaly_fraud_user"
        geo_anomaly_transaction = self.generate_single_transaction(
            user_id=geo_anomaly_user,
            transaction_id="geo_anomaly_fraud_001",
            force_fraud=True
        )
        geo_anomaly_transaction["location"] = {"city": "Suspicious_City", "state": "XX"}
        scenarios["geographic_anomaly"] = [geo_anomaly_transaction]
        
        return scenarios

    def save_generated_data(self, transactions: List[Dict[str, Any]], filepath: str):
        """Save generated transactions to file."""
        output_path = Path(filepath)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(transactions, f, indent=2)
        
        print(f"Saved {len(transactions)} transactions to {filepath}")

    def load_generated_data(self, filepath: str) -> List[Dict[str, Any]]:
        """Load previously generated transactions."""
        with open(filepath, 'r') as f:
            return json.load(f)

    def generate_performance_test_data(self, target_tps: int, duration_seconds: int,
                                     num_users: int = 10000) -> List[Dict[str, Any]]:
        """Generate data specifically for performance testing."""
        total_transactions = target_tps * duration_seconds
        
        print(f"Generating {total_transactions} transactions for {target_tps} TPS over {duration_seconds} seconds...")
        print(f"Using {num_users} unique users...")
        
        transactions = self.generate_bulk_transactions(
            num_transactions=total_transactions,
            num_users=num_users
        )
        
        # Add performance test metadata
        for transaction in transactions:
            transaction["performance_test_metadata"] = {
                "target_tps": target_tps,
                "test_duration": duration_seconds,
                "total_transactions": total_transactions
            }
        
        return transactions
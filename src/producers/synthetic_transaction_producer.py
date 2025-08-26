# /stream-sentinel/src/producers/synthetic_transaction_producer.py

"""
Synthetic Transaction Producer for Stream-Sentinel

This module generates realistic financial transaction data based on IEEE-CIS fraud
detection dataset analysis. It creates both legitimate and fraudulent transactions
following statistical patterns learned from real-world data.

Key Features:
- Statistical generation using IEEE-CIS analysis results
- Configurable fraud injection based on temporal and amount patterns
- High-throughput Kafka production for load testing
- Realistic user behavior simulation
- Temporal pattern adherence (hourly/daily variations)

Architecture Concepts Demonstrated:
- High-performance data generation for stream processing
- Statistical modeling for realistic workload simulation
- Kafka producer optimization for fraud detection pipelines
- Configurable load testing infrastructure
"""

import json
import time
import uuid
import random
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import logging
from pathlib import Path

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

# Import our configuration system
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "kafka"))
from config import get_kafka_config


@dataclass
class Transaction:
    """
    Transaction data structure matching IEEE-CIS format.

    This represents a single financial transaction with all the features
    needed for fraud detection analysis.
    """

    transaction_id: str
    is_fraud: int
    transaction_dt: int
    transaction_amt: float
    product_cd: str
    card1: Optional[int]
    card2: Optional[float]
    card3: Optional[float]
    card4: Optional[str]
    card5: Optional[float]
    card6: Optional[str]
    addr1: Optional[float]
    addr2: Optional[float]
    dist1: Optional[float]
    dist2: Optional[float]
    p_emaildomain: Optional[str]
    r_emaildomain: Optional[str]

    # Additional metadata for stream processing
    generated_timestamp: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    fraud_reason: Optional[str] = None


class UserProfile:
    """
    Simulates realistic user spending behavior.

    Each user has consistent patterns that help generate realistic
    transaction sequences and enable fraud detection based on
    deviation from normal behavior.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.created_at = time.time()

        # Generate consistent user characteristics
        self.preferred_amounts = self._generate_preferred_amounts()
        self.preferred_merchants = self._generate_preferred_merchants()
        self.typical_locations = self._generate_typical_locations()
        self.spending_velocity = random.uniform(0.5, 3.0)  # Transactions per hour
        self.risk_profile = random.choice(["low", "medium", "high"])

        # Track user behavior over time
        self.total_transactions = 0
        self.total_spent = 0.0
        self.last_transaction_time = 0
        self.recent_locations = []

    def _generate_preferred_amounts(self) -> List[Tuple[float, float]]:
        """Generate user's preferred spending ranges."""
        # Most users have 2-3 common spending ranges
        ranges = []
        num_ranges = random.randint(2, 4)

        base_amounts = [25, 50, 100, 200, 500]
        for _ in range(num_ranges):
            base = random.choice(base_amounts)
            variation = base * random.uniform(0.2, 0.8)
            ranges.append((base - variation, base + variation))

        return ranges

    def _generate_preferred_merchants(self) -> List[str]:
        """Generate user's preferred merchant categories."""
        all_merchants = ["W", "C", "R", "H", "S"]  # ProductCD values from IEEE-CIS
        num_preferred = random.randint(1, 3)
        return random.sample(all_merchants, num_preferred)

    def _generate_typical_locations(self) -> List[int]:
        """Generate user's typical location patterns."""
        # Users typically shop in 2-5 locations
        num_locations = random.randint(2, 5)
        base_location = random.randint(100, 500)

        locations = [base_location]
        for _ in range(num_locations - 1):
            # Additional locations within reasonable distance
            location = base_location + random.randint(-50, 50)
            locations.append(max(100, location))

        return locations

    def get_typical_amount(self) -> float:
        """Get amount within user's typical spending pattern."""
        if self.preferred_amounts:
            range_choice = random.choice(self.preferred_amounts)
            return random.uniform(range_choice[0], range_choice[1])
        return random.uniform(20, 200)

    def get_typical_merchant(self) -> str:
        """Get merchant from user's preferred categories."""
        if self.preferred_merchants:
            return random.choice(self.preferred_merchants)
        return random.choice(["W", "C", "R", "H", "S"])

    def get_typical_location(self) -> int:
        """Get location from user's typical areas."""
        return random.choice(self.typical_locations)

    def update_after_transaction(self, amount: float, location: int):
        """Update user profile after a transaction."""
        self.total_transactions += 1
        self.total_spent += amount
        self.last_transaction_time = time.time()

        # Track recent locations for fraud detection
        self.recent_locations.append(location)
        if len(self.recent_locations) > 10:
            self.recent_locations.pop(0)


class SyntheticTransactionProducer:
    """
    High-performance synthetic transaction generator for fraud detection testing.

    This producer generates realistic transaction streams based on statistical
    analysis of the IEEE-CIS fraud detection dataset. It supports configurable
    throughput rates and fraud injection patterns.
    """

    def __init__(self, analysis_file: str = "data/processed/ieee_cis_analysis.json"):
        """
        Initialize the synthetic transaction producer.

        Args:
            analysis_file: Path to IEEE-CIS analysis results
        """
        # Setup logging
        self.logger = self._setup_logging()

        # Load Kafka configuration
        self.kafka_config = get_kafka_config()

        # Load analysis results
        self.analysis_data = self._load_analysis_results(analysis_file)

        # Initialize producer
        producer_config = self.kafka_config.get_producer_config("transaction")
        self.producer = Producer(producer_config)

        # Transaction generation state
        self.transaction_counter = 0
        self.start_time = time.time()
        self.user_profiles: Dict[str, UserProfile] = {}
        self.running = False

        # Topic configuration
        self.topic_name = "synthetic-transactions"

        # Statistics tracking
        self.stats = {
            "total_produced": 0,
            "fraud_produced": 0,
            "legitimate_produced": 0,
            "production_rate": 0.0,
            "errors": 0,
        }

        self.logger.info("Synthetic Transaction Producer initialized")

    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration."""
        logger = logging.getLogger("synthetic_producer")

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

        return logger

    def _load_analysis_results(self, analysis_file: str) -> Dict[str, Any]:
        """Load IEEE-CIS analysis results for generation parameters."""
        try:
            with open(analysis_file, "r") as f:
                data = json.load(f)

            self.logger.info(f"Loaded analysis results from {analysis_file}")

            # Extract key parameters for easy access
            results = data["analysis_results"]
            self.fraud_rate = results["schema"]["fraud_rate"]
            self.transaction_patterns = results["synthetic_spec"][
                "transaction_patterns"
            ]
            self.fraud_patterns = results["synthetic_spec"]["fraud_patterns"]

            return results

        except Exception as e:
            self.logger.error(f"Failed to load analysis results: {e}")
            # Use default parameters if analysis file not available
            return self._get_default_parameters()

    def _get_default_parameters(self) -> Dict[str, Any]:
        """Provide default parameters if analysis results unavailable."""
        self.logger.warning("Using default parameters - analysis results not available")

        self.fraud_rate = 0.027  # 2.7% fraud rate
        self.transaction_patterns = {
            "amount_distribution": {
                "mean_log": 4.0,
                "std_log": 1.2,
                "min_amount": 1.0,
                "max_amount": 1000.0,
            },
            "product_codes": {"W": 0.7, "C": 0.15, "R": 0.1, "H": 0.03, "S": 0.02},
        }
        self.fraud_patterns = {
            "base_fraud_rate": 0.027,
            "amount_patterns": {"high_amount_bias": 1.2},
        }

        return {}

    def setup_topic(self) -> bool:
        """Create Kafka topic for synthetic transactions if it doesn't exist."""
        try:
            admin_config = {"bootstrap.servers": self.kafka_config.bootstrap_servers}
            admin_client = AdminClient(admin_config)

            # Check if topic exists
            existing_topics = admin_client.list_topics(timeout=10)
            if self.topic_name in existing_topics.topics:
                self.logger.info(f"Topic '{self.topic_name}' already exists")
                return True

            # Create topic with transaction-optimized settings
            topic_config = self.kafka_config.get_topic_config("transactions")
            new_topic = NewTopic(
                topic=self.topic_name,
                num_partitions=topic_config["num_partitions"],
                replication_factor=topic_config["replication_factor"],
                config={
                    "cleanup.policy": topic_config["cleanup_policy"],
                    "retention.ms": str(topic_config["retention_ms"]),
                    "compression.type": topic_config["compression_type"],
                },
            )

            # Create the topic
            creation_result = admin_client.create_topics([new_topic])

            # Wait for creation
            for topic_name, future in creation_result.items():
                future.result(timeout=10)
                self.logger.info(f"Created topic '{topic_name}' successfully")

            return True

        except Exception as e:
            self.logger.error(f"Failed to setup topic: {e}")
            return False

    def _get_or_create_user(self, user_id: Optional[str] = None) -> UserProfile:
        """Get existing user profile or create new one."""
        if user_id is None:
            # Create new user
            user_id = f"user_{len(self.user_profiles):06d}"

        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = UserProfile(user_id)

        return self.user_profiles[user_id]

    def _generate_transaction_amount(self, is_fraud: bool = False) -> float:
        """Generate realistic transaction amount."""
        if is_fraud:
            # Fraud transactions tend to be slightly higher on average
            bias_multiplier = self.fraud_patterns.get("amount_patterns", {}).get(
                "high_amount_bias", 1.2
            )
            mean_log = (
                self.transaction_patterns["amount_distribution"]["mean_log"]
                * bias_multiplier
            )
        else:
            mean_log = self.transaction_patterns["amount_distribution"]["mean_log"]

        std_log = self.transaction_patterns["amount_distribution"]["std_log"]

        # Generate log-normal amount
        amount = np.random.lognormal(mean=mean_log, sigma=std_log)

        # Clamp to reasonable bounds
        min_amount = self.transaction_patterns["amount_distribution"]["min_amount"]
        max_amount = self.transaction_patterns["amount_distribution"]["max_amount"]

        amount = max(min_amount, min(amount, max_amount))

        # Round to 2 decimal places
        return round(amount, 2)

    def _generate_product_code(self) -> str:
        """Generate product code based on learned distribution."""
        product_codes = self.transaction_patterns.get("product_codes", {"W": 1.0})

        # Weighted random selection
        codes = list(product_codes.keys())
        weights = list(product_codes.values())

        return np.random.choice(codes, p=weights)

    def _generate_card_features(
        self,
    ) -> Tuple[
        Optional[int],
        Optional[float],
        Optional[float],
        Optional[str],
        Optional[float],
        Optional[str],
    ]:
        """Generate card-related features."""
        # card1: Primary card identifier
        card1 = random.randint(1000, 20000)

        # card2: Secondary identifier (sometimes missing)
        card2 = random.randint(100, 600) if random.random() > 0.2 else None

        # card3: Tertiary identifier
        card3 = 150.0  # Most common value in dataset

        # card4: Card network
        card4 = random.choice(["visa", "mastercard", "discover", "american express"])

        # card5: Card category
        card5 = random.randint(100, 200)

        # card6: Card type
        card6 = random.choice(["debit", "credit"])

        return card1, card2, card3, card4, card5, card6

    def _generate_address_features(
        self, user: UserProfile
    ) -> Tuple[Optional[float], Optional[float]]:
        """Generate address-related features."""
        # Use user's typical location with some variation
        base_addr = user.get_typical_location()

        addr1 = base_addr + random.randint(-20, 20)
        addr2 = 87.0 + random.randint(-10, 10)  # Common value from dataset

        return float(addr1), float(addr2)

    def _generate_distance_features(self) -> Tuple[Optional[float], Optional[float]]:
        """Generate distance-related features."""
        # Distance features are often missing in real data
        if random.random() > 0.7:
            dist1 = random.uniform(1, 1000)
            dist2 = None  # Often missing when dist1 is present
        else:
            dist1 = None
            dist2 = None

        return dist1, dist2

    def _generate_email_domains(self) -> Tuple[Optional[str], Optional[str]]:
        """Generate email domain features."""
        common_domains = [
            "gmail.com",
            "yahoo.com",
            "hotmail.com",
            "outlook.com",
            "aol.com",
            "icloud.com",
            None,  # None represents missing
        ]

        p_email = random.choice(common_domains)
        r_email = random.choice(common_domains) if random.random() > 0.8 else None

        return p_email, r_email

    def _determine_if_fraud(
        self, user: UserProfile, amount: float, current_time: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Determine if transaction should be fraudulent based on patterns.

        Returns:
            Tuple of (is_fraud, fraud_reason)
        """
        # Base fraud probability
        fraud_prob = self.fraud_rate

        # Adjust based on amount (small amounts have higher fraud rate)
        if amount < 10:
            fraud_prob *= 1.8
        elif amount > 500:
            fraud_prob *= 0.8

        # Adjust based on time (8 AM has highest fraud rate)
        hour = (current_time // 3600) % 24
        if hour == 8:
            fraud_prob *= 2.3  # Peak fraud hour
        elif 22 <= hour or hour <= 5:
            fraud_prob *= 1.2  # Night hours slightly elevated

        # Adjust based on user risk profile
        if user.risk_profile == "high":
            fraud_prob *= 2.0
        elif user.risk_profile == "low":
            fraud_prob *= 0.5

        # Velocity-based fraud (too many transactions too quickly)
        if user.total_transactions > 10:
            time_since_last = current_time - user.last_transaction_time
            if time_since_last < 300:  # Less than 5 minutes
                fraud_prob *= 3.0
                fraud_reason = "velocity_fraud"
            else:
                fraud_reason = (
                    "pattern_fraud" if random.random() > 0.5 else "amount_fraud"
                )
        else:
            fraud_reason = "new_user_fraud"

        # Cap maximum fraud probability
        fraud_prob = min(fraud_prob, 0.15)  # Max 15% fraud rate

        is_fraud = random.random() < fraud_prob

        return is_fraud, fraud_reason if is_fraud else None

    def _generate_transaction(self, user_id: Optional[str] = None) -> Transaction:
        """Generate a single realistic transaction."""
        # Get or create user profile
        user = self._get_or_create_user(user_id)

        # Generate transaction timing
        current_time = int(time.time())
        transaction_dt = self.transaction_counter * 100 + random.randint(
            0, 99
        )  # Realistic time progression

        # Generate amount (before fraud determination to use in fraud logic)
        amount = self._generate_transaction_amount()

        # Determine if this should be fraud
        is_fraud, fraud_reason = self._determine_if_fraud(user, amount, current_time)

        # Regenerate amount if fraud (apply fraud patterns)
        if is_fraud:
            amount = self._generate_transaction_amount(is_fraud=True)

        # Generate other features
        product_cd = self._generate_product_code()
        card1, card2, card3, card4, card5, card6 = self._generate_card_features()
        addr1, addr2 = self._generate_address_features(user)
        dist1, dist2 = self._generate_distance_features()
        p_email, r_email = self._generate_email_domains()

        # Create transaction
        transaction = Transaction(
            transaction_id=f"T{self.transaction_counter:010d}",
            is_fraud=1 if is_fraud else 0,
            transaction_dt=transaction_dt,
            transaction_amt=amount,
            product_cd=product_cd,
            card1=card1,
            card2=card2,
            card3=card3,
            card4=card4,
            card5=card5,
            card6=card6,
            addr1=addr1,
            addr2=addr2,
            dist1=dist1,
            dist2=dist2,
            p_emaildomain=p_email,
            r_emaildomain=r_email,
            generated_timestamp=datetime.now().isoformat(),
            user_id=user.user_id,
            session_id=f"sess_{user.total_transactions // 5}",  # New session every 5 transactions
            fraud_reason=fraud_reason,
        )

        # Update user profile
        user.update_after_transaction(amount, addr1 or 0)

        # Update counters
        self.transaction_counter += 1

        return transaction

    def _delivery_callback(self, err, msg):
        """Callback for message delivery confirmation."""
        if err is not None:
            self.logger.error(f"Message delivery failed: {err}")
            self.stats["errors"] += 1
        else:
            self.stats["total_produced"] += 1
            # Parse message to update fraud/legitimate stats
            try:
                transaction_data = json.loads(msg.value().decode("utf-8"))
                if transaction_data.get("is_fraud", 0) == 1:
                    self.stats["fraud_produced"] += 1
                else:
                    self.stats["legitimate_produced"] += 1
            except:
                pass  # Skip stats update on parse error

    def produce_transaction(self, transaction: Transaction):
        """Produce a single transaction to Kafka."""
        try:
            # Convert transaction to JSON
            transaction_dict = asdict(transaction)
            message_value = json.dumps(transaction_dict)
            message_key = transaction.transaction_id

            # Produce to Kafka
            self.producer.produce(
                topic=self.topic_name,
                key=message_key,
                value=message_value,
                callback=self._delivery_callback,
            )

        except Exception as e:
            self.logger.error(f"Failed to produce transaction: {e}")
            self.stats["errors"] += 1

    def run_production(
        self,
        target_tps: int = 1000,
        duration_seconds: int = 300,
        user_count: int = 1000,
    ):
        """
        Run transaction production at specified rate.

        Args:
            target_tps: Target transactions per second
            duration_seconds: How long to run production
            user_count: Number of simulated users
        """
        self.logger.info(
            f"Starting production: {target_tps} TPS for {duration_seconds}s with {user_count} users"
        )

        if not self.setup_topic():
            self.logger.error("Failed to setup topic, aborting production")
            return

        self.running = True
        self.start_time = time.time()

        # Pre-create some users for realistic patterns
        user_pool = [f"user_{i:06d}" for i in range(user_count)]

        # Calculate timing parameters
        target_interval = 1.0 / target_tps  # Seconds between transactions

        try:
            end_time = self.start_time + duration_seconds
            last_stats_time = self.start_time

            while self.running and time.time() < end_time:
                batch_start = time.time()

                # Generate and produce transaction
                user_id = random.choice(user_pool)
                transaction = self._generate_transaction(user_id)
                self.produce_transaction(transaction)

                # Periodic flush and stats
                if self.transaction_counter % 100 == 0:
                    self.producer.flush(timeout=1)

                # Print statistics every 10 seconds
                current_time = time.time()
                if current_time - last_stats_time >= 10:
                    self._print_statistics()
                    last_stats_time = current_time

                # Rate limiting
                processing_time = time.time() - batch_start
                sleep_time = target_interval - processing_time
                if sleep_time > 0:
                    time.sleep(sleep_time)

            # Final flush
            self.producer.flush(timeout=30)
            self.running = False

            self.logger.info("Production completed successfully")
            self._print_final_statistics()

        except KeyboardInterrupt:
            self.logger.info("Production interrupted by user")
            self.running = False
            self.producer.flush(timeout=10)
        except Exception as e:
            self.logger.error(f"Production failed: {e}")
            self.running = False

    def _print_statistics(self):
        """Print current production statistics."""
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            actual_tps = self.stats["total_produced"] / elapsed
            fraud_rate = (
                self.stats["fraud_produced"] / max(1, self.stats["total_produced"])
            ) * 100

            self.logger.info(
                f"Stats - Total: {self.stats['total_produced']}, "
                f"TPS: {actual_tps:.1f}, "
                f"Fraud Rate: {fraud_rate:.2f}%, "
                f"Errors: {self.stats['errors']}, "
                f"Users: {len(self.user_profiles)}"
            )

    def _print_final_statistics(self):
        """Print final production statistics."""
        elapsed = time.time() - self.start_time

        self.logger.info("=" * 60)
        self.logger.info("FINAL PRODUCTION STATISTICS")
        self.logger.info("=" * 60)
        self.logger.info(
            f"Total Transactions Produced: {self.stats['total_produced']:,}"
        )
        self.logger.info(f"Fraudulent Transactions: {self.stats['fraud_produced']:,}")
        self.logger.info(
            f"Legitimate Transactions: {self.stats['legitimate_produced']:,}"
        )
        self.logger.info(
            f"Fraud Rate: {(self.stats['fraud_produced'] / max(1, self.stats['total_produced'])) * 100:.3f}%"
        )
        self.logger.info(f"Average TPS: {self.stats['total_produced'] / elapsed:.2f}")
        self.logger.info(f"Users Created: {len(self.user_profiles)}")
        self.logger.info(f"Production Errors: {self.stats['errors']}")
        self.logger.info(f"Duration: {elapsed:.1f} seconds")
        self.logger.info("=" * 60)


def main():
    """Main function for running the synthetic producer."""
    producer = SyntheticTransactionProducer()

    # Configuration
    TARGET_TPS = 2000  # Transactions per second
    DURATION_SECONDS = 180  # 3 minutes for initial test
    USER_COUNT = 500  # Simulated users

    try:
        producer.run_production(
            target_tps=TARGET_TPS,
            duration_seconds=DURATION_SECONDS,
            user_count=USER_COUNT,
        )
    except Exception as e:
        producer.logger.error(f"Production failed: {e}")
        raise


if __name__ == "__main__":
    main()

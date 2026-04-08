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
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kafka.config import get_kafka_config

# Import generation config -- lives alongside this file in src/producers/
# We use importlib to avoid name collision with the kafka config module
# that was just loaded via sys.path manipulation.
_gen_config_path = os.path.join(os.path.dirname(__file__), "config.py")
_spec = importlib.util.spec_from_file_location("gen_config", _gen_config_path)
gen_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_config)

# Schema Registry integration (optional -- system works without it)
try:
    from kafka.schema_utils import get_schema_helper, serialize_message
    SCHEMA_UTILS_AVAILABLE = True
except ImportError:
    SCHEMA_UTILS_AVAILABLE = False


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
    
    # Counting features C1-C14 (entity relationship counts)
    c1: Optional[float] = None  # Cards associated with this address
    c2: Optional[float] = None  # Addresses associated with this card
    c3: Optional[float] = None  # Transactions with this email domain today
    c4: Optional[float] = None  # Unique merchants for this user this month
    c5: Optional[float] = None  # Cards associated with this email domain
    c6: Optional[float] = None  # Addresses associated with this email domain
    c7: Optional[float] = None  # Transactions from this device today
    c8: Optional[float] = None  # Unique email domains for this card
    c9: Optional[float] = None  # Transactions with this card today
    c10: Optional[float] = None # Unique addresses for this card
    c11: Optional[float] = None # Transactions from this IP today
    c12: Optional[float] = None # Unique cards for this user
    c13: Optional[float] = None # Transactions with this product code today
    c14: Optional[float] = None # Days since first transaction with this card
    
    # Time delta features D1-D15 (temporal relationships)
    d1: Optional[float] = None  # Days since account creation
    d2: Optional[float] = None  # Days since last transaction
    d3: Optional[float] = None  # Days since first transaction with this card
    d4: Optional[float] = None  # Hours since last transaction from this device
    d5: Optional[float] = None  # Days since last fraud report on this account
    d6: Optional[float] = None  # Days since card was first seen in system
    d7: Optional[float] = None  # Hours since last transaction with this email
    d8: Optional[float] = None  # Days since first transaction with this merchant
    d9: Optional[float] = None  # Days since last transaction with this amount range
    d10: Optional[float] = None # Hours since last login from this device
    d11: Optional[float] = None # Days since address was first seen
    d12: Optional[float] = None # Hours since last failed transaction
    d13: Optional[float] = None # Days since profile was last updated
    d14: Optional[float] = None # Hours since last successful transaction
    d15: Optional[float] = None # Days since last password change
    
    # Match features M1-M9 (identity verification flags)
    m1: Optional[str] = None    # Name on card matches billing address name
    m2: Optional[str] = None    # Email domain matches card issuer domain
    m3: Optional[str] = None    # Phone area code matches billing address area code
    m4: Optional[str] = None    # Device timezone matches billing address timezone
    m5: Optional[str] = None    # Previous transaction patterns match current behavior
    m6: Optional[str] = None    # IP geolocation matches billing address
    m7: Optional[str] = None    # Card usage pattern matches historical behavior
    m8: Optional[str] = None    # Email domain matches merchant domain
    m9: Optional[str] = None    # Transaction time matches user's typical pattern

    # Additional metadata for stream processing (required fields must not have defaults after optional fields)
    generated_timestamp: Optional[str] = None
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
        
        # Enhanced feature tracking for C/D/M features
        self.entity_tracking = {
            # For counting features (C1-C14)
            "card_addresses": {},      # card -> set of addresses
            "address_cards": {},       # address -> set of cards  
            "email_transactions": {},  # email -> list of transaction times
            "user_merchants": {},      # user -> set of merchants used
            "card_emails": {},         # card -> set of email domains
            "email_addresses": {},     # email -> set of addresses
            "device_transactions": {}, # device -> list of transaction times
            "card_firstseen": {},      # card -> first seen timestamp
            "user_cards": {},          # user -> set of cards used
            
            # For time delta features (D1-D15)  
            "user_created": {},        # user -> creation timestamp
            "user_lasttxn": {},        # user -> last transaction timestamp
            "card_firstuse": {},       # card -> first use timestamp
            "device_lasttxn": {},      # device -> last transaction timestamp
            "user_lastfraud": {},      # user -> last fraud report timestamp
            "email_lasttxn": {},       # email -> last transaction timestamp
            "merchant_firstuse": {},   # merchant -> first use timestamp
            "address_firstseen": {},   # address -> first seen timestamp
        }

        # Topic configuration
        self.topic_name = gen_config.DEFAULT_TOPIC_NAME

        # Statistics tracking
        self.stats = {
            "total_produced": 0,
            "fraud_produced": 0,
            "legitimate_produced": 0,
            "production_rate": 0.0,
            "errors": 0,
        }

        # Schema Registry integration (optional)
        self._schema_helper = None
        if SCHEMA_UTILS_AVAILABLE:
            try:
                self._schema_helper = get_schema_helper()
                if self._schema_helper.is_available:
                    self.logger.info(
                        "Schema Registry available -- producing Avro-validated messages"
                    )
                else:
                    self.logger.info(
                        "Schema Registry not reachable -- producing plain JSON messages"
                    )
            except Exception as e:
                self.logger.warning(f"Schema helper init failed: {e}")
        else:
            self.logger.info(
                "schema_utils not importable -- producing plain JSON messages"
            )

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
        """Provide default parameters from gen_config if analysis results unavailable."""
        self.logger.warning("Using default parameters from gen_config - analysis results not available")

        defaults = gen_config.DEFAULT_IEEE_CIS_ANALYSIS
        results = defaults["analysis_results"]

        self.fraud_rate = results["schema"]["fraud_rate"]
        self.transaction_patterns = results["synthetic_spec"]["transaction_patterns"]
        self.fraud_patterns = results["synthetic_spec"]["fraud_patterns"]

        return results

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
        """Generate card-related features using config distributions."""
        # card1: Primary card identifier
        card1 = random.randint(1000, 20000)

        # card2: Secondary identifier (sometimes missing per IEEE-CIS)
        card2 = random.randint(100, 600) if random.random() > gen_config.CARD2_MISSING_RATE else None

        # card3: Tertiary identifier -- dominant value in dataset
        card3 = gen_config.CARD3_COMMON_VALUE

        # card4: Card network -- weighted by IEEE-CIS distribution
        card4_names = list(gen_config.CARD4_DISTRIBUTION.keys())
        card4_weights = list(gen_config.CARD4_DISTRIBUTION.values())
        card4 = random.choices(card4_names, weights=card4_weights)[0]

        # card5: Card category
        card5 = random.randint(gen_config.CARD5_RANGE[0], gen_config.CARD5_RANGE[1])

        # card6: Card type -- weighted by IEEE-CIS distribution
        card6_names = list(gen_config.CARD6_DISTRIBUTION.keys())
        card6_weights = list(gen_config.CARD6_DISTRIBUTION.values())
        card6 = random.choices(card6_names, weights=card6_weights)[0]

        return card1, card2, card3, card4, card5, card6

    def _generate_address_features(
        self, user: UserProfile
    ) -> Tuple[Optional[float], Optional[float]]:
        """Generate address-related features."""
        base_addr = user.get_typical_location()

        addr1 = base_addr + random.randint(-20, 20)
        addr2 = gen_config.ADDR2_BASE + random.randint(-gen_config.ADDR2_RANGE, gen_config.ADDR2_RANGE)

        return float(addr1), float(addr2)

    def _generate_distance_features(self) -> Tuple[Optional[float], Optional[float]]:
        """Generate distance-related features using IEEE-CIS sparsity rates."""
        dist1 = None
        dist2 = None

        if random.random() < gen_config.DIST1_PRESENT_RATE:
            dist1 = random.uniform(*gen_config.DIST1_RANGE)

        if random.random() < gen_config.DIST2_PRESENT_RATE:
            dist2 = random.uniform(*gen_config.DIST2_RANGE)

        return dist1, dist2

    def _generate_email_domains(self) -> Tuple[Optional[str], Optional[str]]:
        """Generate email domain features using IEEE-CIS distributions."""
        # P_emaildomain: ~14% missing
        if random.random() < gen_config.P_EMAIL_MISSING_RATE:
            p_email = None
        else:
            names = list(gen_config.P_EMAIL_DOMAINS.keys())
            weights = list(gen_config.P_EMAIL_DOMAINS.values())
            p_email = random.choices(names, weights=weights)[0]

        # R_emaildomain: ~77% missing
        if random.random() < gen_config.R_EMAIL_MISSING_RATE:
            r_email = None
        else:
            names = list(gen_config.R_EMAIL_DOMAINS.keys())
            weights = list(gen_config.R_EMAIL_DOMAINS.values())
            r_email = random.choices(names, weights=weights)[0]

        return p_email, r_email

    def _apply_null(self, value: float, feature_name: str, null_rates: Dict[str, float]) -> Optional[float]:
        """Apply configured null rate to a feature value.

        Args:
            value: The computed feature value.
            feature_name: Key into the null_rates dict (e.g. "c1").
            null_rates: Mapping of feature name -> probability of being null.

        Returns:
            The original value or None.
        """
        rate = null_rates.get(feature_name, 0.0)
        if random.random() < rate:
            return None
        return value

    def _generate_counting_features(self, user: UserProfile, card1: int, addr1: float,
                                    p_email: Optional[str], product_cd: str,
                                    current_time: float) -> Dict[str, Optional[float]]:
        """Generate C1-C14 counting features from entity tracking state.

        Every C-feature is derived from actual entity relationship dictionaries
        that accumulate state across transactions. Null rates come from
        gen_config.C_FEATURE_NULL_RATES which mirror the IEEE-CIS dataset.
        """
        null_rates = gen_config.C_FEATURE_NULL_RATES

        # Deterministic device id for this user (stable across calls for same user)
        device_id = f"device_{user.user_id}_{hash(user.user_id) % gen_config.USER_DEVICE_RANGE[1] + 1}"

        current_day = int(current_time // 86400)

        features: Dict[str, Optional[float]] = {}

        # --- C1: Cards associated with this address ---
        addr_cards = self.entity_tracking["address_cards"]
        if addr1 not in addr_cards:
            addr_cards[addr1] = set()
        addr_cards[addr1].add(card1)
        features["c1"] = self._apply_null(float(len(addr_cards[addr1])), "c1", null_rates)

        # --- C2: Addresses associated with this card ---
        card_addrs = self.entity_tracking["card_addresses"]
        if card1 not in card_addrs:
            card_addrs[card1] = set()
        card_addrs[card1].add(addr1)
        features["c2"] = self._apply_null(float(len(card_addrs[card1])), "c2", null_rates)

        # --- C3: Transactions with this email domain today ---
        if p_email:
            email_txns = self.entity_tracking["email_transactions"]
            if p_email not in email_txns:
                email_txns[p_email] = []
            email_txns[p_email].append(current_time)
            today_count = sum(1 for t in email_txns[p_email] if int(t // 86400) == current_day)
            features["c3"] = self._apply_null(float(today_count), "c3", null_rates)
        else:
            features["c3"] = None

        # --- C4: Unique merchants (product codes) for this user ---
        user_merchants = self.entity_tracking["user_merchants"]
        if user.user_id not in user_merchants:
            user_merchants[user.user_id] = set()
        user_merchants[user.user_id].add(product_cd)
        features["c4"] = self._apply_null(float(len(user_merchants[user.user_id])), "c4", null_rates)

        # --- C5: Unique email domains associated with this card ---
        card_emails = self.entity_tracking["card_emails"]
        if card1 not in card_emails:
            card_emails[card1] = set()
        if p_email:
            card_emails[card1].add(p_email)
        features["c5"] = self._apply_null(float(len(card_emails[card1])), "c5", null_rates)

        # --- C6: Addresses associated with this email domain ---
        if p_email:
            email_addrs = self.entity_tracking["email_addresses"]
            if p_email not in email_addrs:
                email_addrs[p_email] = set()
            email_addrs[p_email].add(addr1)
            features["c6"] = self._apply_null(float(len(email_addrs[p_email])), "c6", null_rates)
        else:
            features["c6"] = None

        # --- C7: Transactions from this device today ---
        dev_txns = self.entity_tracking["device_transactions"]
        if device_id not in dev_txns:
            dev_txns[device_id] = []
        dev_txns[device_id].append(current_time)
        today_device = sum(1 for t in dev_txns[device_id] if int(t // 86400) == current_day)
        features["c7"] = self._apply_null(float(today_device), "c7", null_rates)

        # --- C8: Unique email domains for this card (same set as C5) ---
        features["c8"] = self._apply_null(float(len(card_emails.get(card1, set()))), "c8", null_rates)

        # --- C9: Total transactions for this card (proxy: user txn count) ---
        features["c9"] = self._apply_null(float(user.total_transactions + 1), "c9", null_rates)

        # --- C10: Unique addresses for this card ---
        features["c10"] = self._apply_null(float(len(card_addrs.get(card1, set()))), "c10", null_rates)

        # --- C11: Transactions from this IP today ---
        # IP is not explicitly tracked; use device-day count as proxy with
        # slight random scaling (multiple IPs per device, NAT, etc.)
        ip_proxy = today_device + random.randint(0, 3)
        features["c11"] = self._apply_null(float(ip_proxy), "c11", null_rates)

        # --- C12: Unique cards for this user ---
        user_cards = self.entity_tracking["user_cards"]
        if user.user_id not in user_cards:
            user_cards[user.user_id] = set()
        user_cards[user.user_id].add(card1)
        features["c12"] = self._apply_null(float(len(user_cards[user.user_id])), "c12", null_rates)

        # --- C13: Transactions with this product code today ---
        # Track per-product-code daily counts
        pc_key = f"{product_cd}_{current_day}"
        if "product_daily_counts" not in self.entity_tracking:
            self.entity_tracking["product_daily_counts"] = {}
        pc_counts = self.entity_tracking["product_daily_counts"]
        pc_counts[pc_key] = pc_counts.get(pc_key, 0) + 1
        features["c13"] = self._apply_null(float(pc_counts[pc_key]), "c13", null_rates)

        # --- C14: Days since first transaction with this card ---
        card_first = self.entity_tracking["card_firstseen"]
        if card1 not in card_first:
            card_first[card1] = current_time
        days_since = (current_time - card_first[card1]) / 86400.0
        features["c14"] = self._apply_null(float(max(0, days_since)), "c14", null_rates)

        return features

    def _generate_time_delta_features(self, user: UserProfile, card1: int,
                                      p_email: Optional[str], product_cd: str,
                                      addr1: float,
                                      current_time: float) -> Dict[str, Optional[float]]:
        """Generate D1-D15 time delta features from entity tracking state.

        Every D-feature is computed from actual temporal relationships stored
        in entity_tracking. D9-D15 are no longer pure uniform random -- they
        derive from tracked timestamps with realistic distributions.
        Null rates come from gen_config.D_FEATURE_NULL_RATES.
        """
        null_rates = gen_config.D_FEATURE_NULL_RATES
        features: Dict[str, Optional[float]] = {}

        # Deterministic device id (must match the one used in C-features)
        device_id = f"device_{user.user_id}_{hash(user.user_id) % gen_config.USER_DEVICE_RANGE[1] + 1}"

        # --- D1: Days since account creation ---
        user_created = self.entity_tracking["user_created"]
        if user.user_id not in user_created:
            user_created[user.user_id] = user.created_at
        d1_val = (current_time - user_created[user.user_id]) / 86400.0
        features["d1"] = self._apply_null(float(max(0, d1_val)), "d1", null_rates)

        # --- D2: Days since last transaction ---
        user_lasttxn = self.entity_tracking["user_lasttxn"]
        last_txn = user_lasttxn.get(user.user_id, current_time)
        d2_val = (current_time - last_txn) / 86400.0
        features["d2"] = self._apply_null(float(max(0, d2_val)), "d2", null_rates)
        user_lasttxn[user.user_id] = current_time

        # --- D3: Days since first transaction with this card ---
        card_first = self.entity_tracking["card_firstuse"]
        if card1 not in card_first:
            card_first[card1] = current_time
        d3_val = (current_time - card_first[card1]) / 86400.0
        features["d3"] = self._apply_null(float(max(0, d3_val)), "d3", null_rates)

        # --- D4: Hours since last transaction from this device ---
        dev_last = self.entity_tracking["device_lasttxn"]
        last_dev = dev_last.get(device_id, current_time - 3600)
        d4_val = (current_time - last_dev) / 3600.0
        features["d4"] = self._apply_null(float(max(0, d4_val)), "d4", null_rates)
        dev_last[device_id] = current_time

        # --- D5: Days since last fraud report on this account ---
        last_fraud = self.entity_tracking["user_lastfraud"].get(
            user.user_id, current_time - 30 * 86400
        )
        d5_val = (current_time - last_fraud) / 86400.0
        features["d5"] = self._apply_null(float(max(0, d5_val)), "d5", null_rates)

        # --- D6: Days since card was first seen in system ---
        # Use card_firstseen (populated by C-features); fallback to card_firstuse
        card_firstseen = self.entity_tracking["card_firstseen"]
        first_seen = card_firstseen.get(card1, card_first.get(card1, current_time))
        d6_val = (current_time - first_seen) / 86400.0
        features["d6"] = self._apply_null(float(max(0, d6_val)), "d6", null_rates)

        # --- D7: Hours since last transaction with this email ---
        if p_email:
            email_last = self.entity_tracking["email_lasttxn"]
            prev_email = email_last.get(p_email, current_time - 3600)
            d7_val = (current_time - prev_email) / 3600.0
            features["d7"] = self._apply_null(float(max(0, d7_val)), "d7", null_rates)
            email_last[p_email] = current_time
        else:
            features["d7"] = None

        # --- D8: Days since first transaction with this merchant ---
        merch_first = self.entity_tracking["merchant_firstuse"]
        if product_cd not in merch_first:
            merch_first[product_cd] = current_time
        d8_val = (current_time - merch_first[product_cd]) / 86400.0
        features["d8"] = self._apply_null(float(max(0, d8_val)), "d8", null_rates)

        # --- D9: Days since last transaction in this amount range ---
        # Track by amount bucket: <10, 10-100, 100-500, 500+
        if "amount_range_lasttxn" not in self.entity_tracking:
            self.entity_tracking["amount_range_lasttxn"] = {}
        amt = getattr(user, 'total_spent', 0) / max(1, user.total_transactions) if user.total_transactions > 0 else 50.0
        if amt < 10:
            bucket = "small"
        elif amt < 100:
            bucket = "medium"
        elif amt < 500:
            bucket = "large"
        else:
            bucket = "xlarge"
        bucket_key = f"{user.user_id}_{bucket}"
        amt_last = self.entity_tracking["amount_range_lasttxn"].get(bucket_key, current_time - random.uniform(1, 7) * 86400)
        d9_val = (current_time - amt_last) / 86400.0
        features["d9"] = self._apply_null(float(max(0, d9_val)), "d9", null_rates)
        self.entity_tracking["amount_range_lasttxn"][bucket_key] = current_time

        # --- D10: Hours since last login from this device ---
        # Derive from device last txn with a small offset (login happens before txn)
        login_offset = random.uniform(0.1, 2.0)  # Hours between login and transaction
        d10_val = d4_val + login_offset if d4_val is not None else random.uniform(0.5, 12)
        features["d10"] = self._apply_null(float(max(0, d10_val)), "d10", null_rates)

        # --- D11: Days since address was first seen ---
        addr_first = self.entity_tracking["address_firstseen"]
        if addr1 not in addr_first:
            addr_first[addr1] = current_time
        d11_val = (current_time - addr_first[addr1]) / 86400.0
        features["d11"] = self._apply_null(float(max(0, d11_val)), "d11", null_rates)

        # --- D12: Hours since last failed transaction ---
        # Track failed txns (only some users have them)
        if "user_lastfailed" not in self.entity_tracking:
            self.entity_tracking["user_lastfailed"] = {}
        last_failed = self.entity_tracking["user_lastfailed"].get(
            user.user_id, current_time - random.uniform(12, 168) * 3600  # 12h to 7 days ago
        )
        d12_val = (current_time - last_failed) / 3600.0
        features["d12"] = self._apply_null(float(max(0, d12_val)), "d12", null_rates)

        # --- D13: Days since profile was last updated ---
        if "user_profile_updated" not in self.entity_tracking:
            self.entity_tracking["user_profile_updated"] = {}
        prof_updated = self.entity_tracking["user_profile_updated"].get(
            user.user_id, user_created.get(user.user_id, current_time)
        )
        d13_val = (current_time - prof_updated) / 86400.0
        features["d13"] = self._apply_null(float(max(0, d13_val)), "d13", null_rates)

        # --- D14: Hours since last successful transaction ---
        # Very similar to D2 but in hours and counts only successful txns
        d14_val = d2_val * 24  # Convert D2 (days) to hours
        features["d14"] = self._apply_null(float(max(0, d14_val)), "d14", null_rates)

        # --- D15: Days since last password change ---
        if "user_password_changed" not in self.entity_tracking:
            self.entity_tracking["user_password_changed"] = {}
        pw_changed = self.entity_tracking["user_password_changed"].get(
            user.user_id, user_created.get(user.user_id, current_time) - random.uniform(0, 90) * 86400
        )
        d15_val = (current_time - pw_changed) / 86400.0
        features["d15"] = self._apply_null(float(max(0, d15_val)), "d15", null_rates)

        return features

    def _generate_match_features(self, card4: Optional[str], p_email: Optional[str],
                                  addr1: float, user: UserProfile,
                                  current_time: float,
                                  is_fraud: bool = False) -> Dict[str, Optional[str]]:
        """Generate M1-M9 match features using config-driven weight tables.

        Legitimate and fraudulent transactions use separate weight tables
        (gen_config.M_LEGITIMATE_WEIGHTS vs gen_config.M_FRAUD_WEIGHTS)
        so that fraud exhibits correlated identity mismatches rather than
        each M-feature being independently random.

        Null rates come from gen_config.M_FEATURE_NULL_RATES.
        """
        null_rates = gen_config.M_FEATURE_NULL_RATES
        match_options = ["T", "F", "NotFound"]

        # Select weight table based on fraud status
        if is_fraud:
            weight_table = gen_config.M_FRAUD_WEIGHTS
        else:
            weight_table = gen_config.M_LEGITIMATE_WEIGHTS

        features: Dict[str, Optional[str]] = {}

        for feat_name in [f"m{i}" for i in range(1, 10)]:
            # Skip if null by configured rate
            if random.random() < null_rates.get(feat_name, 0.5):
                features[feat_name] = None
                continue

            weights = weight_table[feat_name]

            # Special handling for features that depend on entity availability
            if feat_name == "m2" and (not p_email or not card4):
                features[feat_name] = None
                continue
            if feat_name == "m8" and not p_email:
                features[feat_name] = None
                continue

            # For established users on legitimate txns, boost M5 and M7
            # (behavior/pattern match improves with history)
            if not is_fraud and user.total_transactions > 5 and feat_name in ("m5", "m7"):
                # Increase T weight by 5%, reduce F weight
                t_w, f_w, nf_w = weights
                t_w = min(0.98, t_w + 0.05)
                f_w = max(0.01, f_w - 0.04)
                nf_w = max(0.01, nf_w - 0.01)
                weights = (t_w, f_w, nf_w)

            features[feat_name] = random.choices(match_options, weights=list(weights))[0]

        return features

    def _determine_if_fraud(
        self, user: UserProfile, amount: float, current_time: int
    ) -> Tuple[bool, Optional[str]]:
        """Determine if transaction should be fraudulent.

        Uses config-driven temporal multipliers (peak 2-4 AM), amount
        thresholds, risk profile multipliers, and velocity checks.

        Returns:
            (is_fraud, fraud_reason)  -- fraud_reason is None when not fraud.
        """
        fraud_prob = self.fraud_rate * gen_config.FRAUD_RATE_NORMALIZATION

        # Amount-based adjustment
        if amount < gen_config.SMALL_AMOUNT_THRESHOLD:
            fraud_prob *= gen_config.SMALL_AMOUNT_FRAUD_MULTIPLIER
        elif amount > gen_config.LARGE_AMOUNT_THRESHOLD:
            fraud_prob *= gen_config.LARGE_AMOUNT_FRAUD_MULTIPLIER

        # Temporal adjustment -- use per-hour multiplier from config
        hour = (current_time // 3600) % 24
        fraud_prob *= gen_config.TEMPORAL_FRAUD_MULTIPLIERS.get(hour, 1.0)

        # Risk profile
        fraud_prob *= gen_config.RISK_PROFILE_MULTIPLIERS.get(user.risk_profile, 1.0)

        # Velocity check
        fraud_reason = None
        if user.total_transactions > gen_config.VELOCITY_MIN_TRANSACTIONS:
            time_since_last = current_time - user.last_transaction_time
            if time_since_last < gen_config.VELOCITY_WINDOW_SECONDS:
                fraud_prob *= gen_config.VELOCITY_FRAUD_MULTIPLIER
                fraud_reason = "velocity_fraud"

        # Cap
        fraud_prob = min(fraud_prob, gen_config.MAX_FRAUD_PROBABILITY)

        is_fraud = random.random() < fraud_prob

        if is_fraud and fraud_reason is None:
            # Assign a reason
            if hour in gen_config.PEAK_FRAUD_HOURS:
                fraud_reason = "temporal_fraud"
            elif amount < gen_config.SMALL_AMOUNT_THRESHOLD:
                fraud_reason = "small_amount_fraud"
            elif amount > gen_config.LARGE_AMOUNT_THRESHOLD:
                fraud_reason = "large_amount_fraud"
            elif user.total_transactions <= gen_config.VELOCITY_MIN_TRANSACTIONS:
                fraud_reason = "new_user_fraud"
            else:
                fraud_reason = "pattern_fraud"

        return is_fraud, fraud_reason if is_fraud else None

    def _apply_fraud_correlations(self, counting_features: Dict[str, Optional[float]],
                                    time_delta_features: Dict[str, Optional[float]],
                                    ) -> None:
        """Apply correlated anomaly injection to C and D features for fraud.

        When a transaction is fraudulent, its anomalies should be correlated:
        higher velocity counts, shorter time deltas, etc.  This mutates the
        feature dicts in place.
        """
        full_anomaly = random.random() < gen_config.FRAUD_FULL_ANOMALY_RATE

        # Inflate a random subset of C-features (velocity indicators)
        num_to_inflate = random.randint(
            gen_config.FRAUD_C_INFLATION_MIN,
            gen_config.FRAUD_C_INFLATION_MAX,
        )
        inflatable = [k for k in counting_features if counting_features[k] is not None]
        targets = random.sample(inflatable, min(num_to_inflate, len(inflatable)))
        for key in targets:
            # Multiply the count by 2-5x to simulate velocity burst
            counting_features[key] = counting_features[key] * random.uniform(2.0, 5.0)

        if full_anomaly or random.random() < gen_config.FRAUD_VELOCITY_BOOST_RATE:
            # Compress D2 (time since last txn) -- rapid succession
            if time_delta_features.get("d2") is not None:
                time_delta_features["d2"] = time_delta_features["d2"] * random.uniform(0.01, 0.1)
            # Compress D4 (hours since last device txn)
            if time_delta_features.get("d4") is not None:
                time_delta_features["d4"] = time_delta_features["d4"] * random.uniform(0.01, 0.2)
            # Compress D14 (hours since last successful txn)
            if time_delta_features.get("d14") is not None:
                time_delta_features["d14"] = time_delta_features["d14"] * random.uniform(0.01, 0.15)

        # New accounts used for fraud should have small D1
        if random.random() < 0.30:
            if time_delta_features.get("d1") is not None:
                time_delta_features["d1"] = random.uniform(0, 2)  # 0-2 days old

    def _generate_transaction(self, user_id: Optional[str] = None) -> Transaction:
        """Generate a single realistic transaction.

        For fraudulent transactions, correlated anomalies are injected:
        - Amount, hour, and velocity anomalies fire together (not independently)
        - C-features get inflated counts, D-features get compressed deltas
        - M-features use the fraud weight table (handled in _generate_match_features)
        """
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

        # For fraud: apply correlated anomaly bundle
        if is_fraud:
            amount = self._generate_transaction_amount(is_fraud=True)
            # Optionally shift to peak fraud hours for correlated temporal anomaly
            if random.random() < gen_config.FRAUD_UNUSUAL_HOUR_RATE:
                fraud_hour = random.choice(gen_config.PEAK_FRAUD_HOURS)
                # Adjust current_time to reflect the fraud hour (for D/M feature calc)
                day_start = current_time - (current_time % 86400)
                current_time = day_start + fraud_hour * 3600 + random.randint(0, 3599)

        # Generate other features
        product_cd = self._generate_product_code()
        card1, card2, card3, card4, card5, card6 = self._generate_card_features()
        addr1, addr2 = self._generate_address_features(user)
        dist1, dist2 = self._generate_distance_features()
        p_email, r_email = self._generate_email_domains()

        # Generate enhanced features
        current_time_float = float(current_time)
        counting_features = self._generate_counting_features(user, card1, addr1, p_email, product_cd, current_time_float)
        time_delta_features = self._generate_time_delta_features(user, card1, p_email, product_cd, addr1 or 0.0, current_time_float)
        match_features = self._generate_match_features(card4, p_email, addr1, user, current_time_float, is_fraud=is_fraud)

        # Apply correlated anomalies for fraud transactions
        if is_fraud:
            self._apply_fraud_correlations(counting_features, time_delta_features)

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
            
            # Add enhanced features
            c1=counting_features.get("c1"),
            c2=counting_features.get("c2"),
            c3=counting_features.get("c3"),
            c4=counting_features.get("c4"),
            c5=counting_features.get("c5"),
            c6=counting_features.get("c6"),
            c7=counting_features.get("c7"),
            c8=counting_features.get("c8"),
            c9=counting_features.get("c9"),
            c10=counting_features.get("c10"),
            c11=counting_features.get("c11"),
            c12=counting_features.get("c12"),
            c13=counting_features.get("c13"),
            c14=counting_features.get("c14"),
            
            d1=time_delta_features.get("d1"),
            d2=time_delta_features.get("d2"),
            d3=time_delta_features.get("d3"),
            d4=time_delta_features.get("d4"),
            d5=time_delta_features.get("d5"),
            d6=time_delta_features.get("d6"),
            d7=time_delta_features.get("d7"),
            d8=time_delta_features.get("d8"),
            d9=time_delta_features.get("d9"),
            d10=time_delta_features.get("d10"),
            d11=time_delta_features.get("d11"),
            d12=time_delta_features.get("d12"),
            d13=time_delta_features.get("d13"),
            d14=time_delta_features.get("d14"),
            d15=time_delta_features.get("d15"),
            
            m1=match_features.get("m1"),
            m2=match_features.get("m2"),
            m3=match_features.get("m3"),
            m4=match_features.get("m4"),
            m5=match_features.get("m5"),
            m6=match_features.get("m6"),
            m7=match_features.get("m7"),
            m8=match_features.get("m8"),
            m9=match_features.get("m9"),
            
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
        """Produce a single transaction to Kafka.

        When the Schema Registry is available the message is serialised
        using Avro for schema validation; otherwise plain JSON is used.
        """
        try:
            transaction_dict = asdict(transaction)
            message_key = transaction.transaction_id

            # Use Avro serialization when Schema Registry is reachable
            if (
                self._schema_helper is not None
                and self._schema_helper.is_available
                and SCHEMA_UTILS_AVAILABLE
            ):
                message_value = serialize_message(
                    self._schema_helper,
                    "transaction",
                    transaction_dict,
                    self.topic_name,
                )
            else:
                message_value = json.dumps(transaction_dict).encode("utf-8")

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

    try:
        producer.run_production(
            target_tps=gen_config.DEFAULT_TARGET_TPS,
            duration_seconds=gen_config.DEFAULT_DURATION_SECONDS,
            user_count=gen_config.DEFAULT_USER_COUNT,
        )
    except Exception as e:
        producer.logger.error(f"Production failed: {e}")
        raise


if __name__ == "__main__":
    main()

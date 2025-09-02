"""
Test Data Factory for Realistic Fraud Detection Testing

Generates IEEE-CIS based realistic fraud scenarios using statistical patterns
from real-world fraud detection data. Provides sophisticated scenario generation
for comprehensive integration testing.

Key Features:
- IEEE-CIS pattern adherence with statistical accuracy
- Multi-user behavioral simulation with realistic fraud injection
- Temporal fraud patterns (velocity attacks, unusual timing)
- Amount-based fraud patterns (micro-transactions, large outliers)
- Cross-service data consistency validation scenarios
"""

import json
import random
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Generator
from enum import Enum
from dataclasses import dataclass, field, asdict
import numpy as np
from pathlib import Path
import logging

# Import existing analysis results
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))


class ScenarioType(Enum):
    """Types of fraud detection test scenarios."""
    LEGITIMATE_BASELINE = "legitimate_baseline"    # Pure legitimate transactions
    VELOCITY_ATTACK = "velocity_attack"           # Rapid sequential transactions
    AMOUNT_ANOMALY = "amount_anomaly"             # Unusual transaction amounts
    TEMPORAL_ANOMALY = "temporal_anomaly"         # Unusual timing patterns
    BEHAVIORAL_DRIFT = "behavioral_drift"         # Gradual behavior changes
    MIXED_POPULATION = "mixed_population"         # Realistic mix of legitimate/fraud
    STRESS_TEST = "stress_test"                   # High-volume concurrent processing
    CROSS_SERVICE_STATE = "cross_service_state"   # Multi-service state consistency
    DATA_CORRUPTION = "data_corruption"           # Malformed/corrupted data handling


@dataclass
class UserBehaviorProfile:
    """User behavioral profile for realistic transaction generation."""
    user_id: str
    avg_transaction_amount: float
    transaction_frequency_per_day: float
    preferred_transaction_hours: List[int]
    amount_variance: float = 0.3  # Standard deviation as fraction of mean
    typical_merchant_categories: List[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 = very low risk, 1.0 = very high risk
    account_age_days: int = 365
    geographic_region: str = "US"
    
    def generate_transaction_amount(self) -> float:
        """Generate realistic transaction amount for this user."""
        # Log-normal distribution to match real-world transaction patterns
        sigma = np.log(1 + self.amount_variance)
        amount = np.random.lognormal(
            mean=np.log(self.avg_transaction_amount),
            sigma=sigma
        )
        # Ensure minimum amount and realistic rounding
        return max(0.01, round(amount, 2))
    
    def should_transact_now(self, current_hour: int) -> bool:
        """Determine if user should make transaction at current hour."""
        if not self.preferred_transaction_hours:
            return random.random() < 0.1  # 10% base probability
        
        # Higher probability during preferred hours
        if current_hour in self.preferred_transaction_hours:
            return random.random() < 0.4  # 40% probability during preferred hours
        else:
            return random.random() < 0.05  # 5% probability otherwise


@dataclass
class FraudScenario:
    """Complete fraud detection test scenario specification."""
    scenario_id: str
    scenario_type: ScenarioType
    name: str
    description: str
    
    # User and transaction parameters
    user_profiles: List[UserBehaviorProfile]
    transaction_count: int
    duration_hours: float
    expected_fraud_rate: float
    
    # Pattern parameters
    fraud_injection_rules: Dict[str, Any]
    temporal_patterns: Dict[str, Any]
    
    # Expected outcomes for validation
    expected_alerts: int
    expected_blocked_users: int
    expected_cross_service_records: Dict[str, int]
    
    # Performance expectations
    max_processing_latency_ms: float = 100.0
    min_throughput_tps: float = 100.0
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    complexity_score: float = 1.0  # 1.0 = simple, 5.0 = very complex
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert scenario to dictionary for serialization."""
        result = asdict(self)
        result['scenario_type'] = self.scenario_type.value
        result['created_at'] = self.created_at.isoformat()
        return result


class TestDataFactory:
    """
    Production-grade test data factory for fraud detection systems.
    
    Leverages IEEE-CIS analysis results to generate statistically accurate
    test scenarios that match real-world fraud detection challenges.
    """
    
    def __init__(self, 
                 ieee_analysis_path: Optional[str] = None,
                 random_seed: Optional[int] = None):
        
        self.logger = logging.getLogger(f"{__name__}.TestDataFactory")
        
        # Set random seed for reproducible tests
        if random_seed:
            random.seed(random_seed)
            np.random.seed(random_seed)
            self.logger.info(f"Set random seed: {random_seed}")
        
        # Load IEEE-CIS analysis results
        self.ieee_analysis = self._load_ieee_analysis(ieee_analysis_path)
        
        # Extract key fraud patterns from analysis
        self.fraud_rate_baseline = self.ieee_analysis.get('schema', {}).get('fraud_rate', 0.0271)
        self.amount_statistics = self._extract_amount_statistics()
        self.temporal_patterns = self._extract_temporal_patterns()
        
        # User behavior templates based on IEEE-CIS patterns
        self.user_behavior_templates = self._create_user_behavior_templates()
        
        self.logger.info("TestDataFactory initialized with IEEE-CIS patterns")
    
    def _load_ieee_analysis(self, analysis_path: Optional[str]) -> Dict[str, Any]:
        """Load IEEE-CIS analysis results."""
        if analysis_path is None:
            # Default to processed analysis file
            analysis_path = Path(__file__).parent.parent.parent.parent / "data" / "processed" / "ieee_cis_analysis.json"
        
        analysis_path = Path(analysis_path)
        
        if analysis_path.exists():
            with open(analysis_path, 'r') as f:
                analysis = json.load(f)
                self.logger.info(f"Loaded IEEE-CIS analysis from {analysis_path}")
                return analysis.get('analysis_results', {})
        else:
            self.logger.warning(f"IEEE-CIS analysis not found at {analysis_path}, using defaults")
            return self._get_default_analysis_results()
    
    def _get_default_analysis_results(self) -> Dict[str, Any]:
        """Get default analysis results if analysis file is not available."""
        return {
            'schema': {
                'fraud_rate': 0.0271,
                'total_transactions': 590540,
                'total_features': 394
            },
            'transaction_analysis': {
                'amount_stats': {
                    'mean': 149.84,
                    'std': 431.93,
                    'min': 0.251,
                    'max': 31937.391,
                    'percentiles': {
                        '25': 20.0,
                        '50': 67.49,
                        '75': 154.0,
                        '90': 344.0,
                        '95': 600.0,
                        '99': 2000.0
                    }
                }
            },
            'temporal_analysis': {
                'fraud_by_hour': {
                    '0': 0.0327, '1': 0.0298, '2': 0.0289, '3': 0.0276,
                    '4': 0.0268, '5': 0.0261, '6': 0.0271, '7': 0.0294,
                    '8': 0.0616, '9': 0.0489, '10': 0.0401, '11': 0.0356,
                    '12': 0.0338, '13': 0.0325, '14': 0.0318, '15': 0.0312,
                    '16': 0.0309, '17': 0.0308, '18': 0.0311, '19': 0.0318,
                    '20': 0.0327, '21': 0.0336, '22': 0.0345, '23': 0.0354
                }
            }
        }
    
    def _extract_amount_statistics(self) -> Dict[str, float]:
        """Extract transaction amount statistics from IEEE-CIS analysis."""
        amount_stats = self.ieee_analysis.get('transaction_analysis', {}).get('amount_stats', {})
        
        return {
            'mean': amount_stats.get('mean', 149.84),
            'std': amount_stats.get('std', 431.93),
            'min': amount_stats.get('min', 0.25),
            'max': amount_stats.get('max', 31937.39),
            'p25': amount_stats.get('percentiles', {}).get('25', 20.0),
            'p50': amount_stats.get('percentiles', {}).get('50', 67.49),
            'p75': amount_stats.get('percentiles', {}).get('75', 154.0),
            'p90': amount_stats.get('percentiles', {}).get('90', 344.0),
            'p95': amount_stats.get('percentiles', {}).get('95', 600.0),
            'p99': amount_stats.get('percentiles', {}).get('99', 2000.0)
        }
    
    def _extract_temporal_patterns(self) -> Dict[str, float]:
        """Extract temporal fraud patterns from IEEE-CIS analysis."""
        temporal_analysis = self.ieee_analysis.get('temporal_analysis', {})
        fraud_by_hour = temporal_analysis.get('fraud_by_hour', {})
        
        # Convert string hours to int keys
        return {int(hour): float(rate) for hour, rate in fraud_by_hour.items()}
    
    def _create_user_behavior_templates(self) -> List[Dict[str, Any]]:
        """Create user behavior templates based on IEEE-CIS patterns."""
        templates = [
            # Low-risk legitimate user
            {
                'name': 'conservative_spender',
                'avg_transaction_amount': self.amount_statistics['p25'],
                'transaction_frequency_per_day': 1.2,
                'preferred_hours': [9, 10, 11, 14, 15, 16, 17, 18],
                'amount_variance': 0.2,
                'risk_score': 0.1
            },
            # Medium-volume legitimate user
            {
                'name': 'regular_user',
                'avg_transaction_amount': self.amount_statistics['p50'],
                'transaction_frequency_per_day': 2.5,
                'preferred_hours': [8, 9, 10, 11, 12, 17, 18, 19, 20],
                'amount_variance': 0.3,
                'risk_score': 0.2
            },
            # High-volume legitimate user
            {
                'name': 'power_user',
                'avg_transaction_amount': self.amount_statistics['p75'],
                'transaction_frequency_per_day': 5.0,
                'preferred_hours': [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
                'amount_variance': 0.4,
                'risk_score': 0.3
            },
            # High-risk user (potential fraud)
            {
                'name': 'high_risk_user',
                'avg_transaction_amount': self.amount_statistics['p90'],
                'transaction_frequency_per_day': 8.0,
                'preferred_hours': [0, 1, 2, 3, 8, 22, 23],  # Unusual hours
                'amount_variance': 0.6,
                'risk_score': 0.8
            }
        ]
        
        self.logger.debug(f"Created {len(templates)} user behavior templates")
        return templates
    
    def create_legitimate_baseline_scenario(self, 
                                          transaction_count: int = 1000,
                                          user_count: int = 50,
                                          duration_hours: float = 24.0) -> FraudScenario:
        """
        Create scenario with purely legitimate transactions for baseline testing.
        
        This scenario provides a clean baseline for validating the system
        processes legitimate transactions without false positives.
        """
        scenario_id = f"legitimate_baseline_{int(time.time())}"
        
        # Create diverse user profiles (no high-risk users)
        user_profiles = []
        for i in range(user_count):
            # Use only low to medium risk templates
            template = random.choice(self.user_behavior_templates[:3])
            
            profile = UserBehaviorProfile(
                user_id=f"user_{scenario_id}_{i:04d}",
                avg_transaction_amount=template['avg_transaction_amount'] * random.uniform(0.7, 1.3),
                transaction_frequency_per_day=template['transaction_frequency_per_day'] * random.uniform(0.8, 1.2),
                preferred_transaction_hours=template['preferred_hours'],
                amount_variance=template['amount_variance'],
                risk_score=template['risk_score']
            )
            user_profiles.append(profile)
        
        return FraudScenario(
            scenario_id=scenario_id,
            scenario_type=ScenarioType.LEGITIMATE_BASELINE,
            name="Legitimate Baseline Scenario",
            description="Pure legitimate transactions for baseline system validation",
            user_profiles=user_profiles,
            transaction_count=transaction_count,
            duration_hours=duration_hours,
            expected_fraud_rate=0.0,  # No fraud in baseline
            fraud_injection_rules={},
            temporal_patterns=self.temporal_patterns,
            expected_alerts=0,
            expected_blocked_users=0,
            expected_cross_service_records={
                'postgres_alerts': 0,
                'redis_profiles': user_count,
                'clickhouse_transactions': transaction_count
            },
            complexity_score=1.0
        )
    
    def create_velocity_attack_scenario(self,
                                      attack_user_count: int = 3,
                                      legitimate_user_count: int = 20,
                                      attack_intensity: float = 10.0,
                                      duration_hours: float = 2.0) -> FraudScenario:
        """
        Create velocity attack scenario with rapid sequential transactions.
        
        Simulates attackers making many rapid transactions to test
        velocity-based fraud detection algorithms.
        """
        scenario_id = f"velocity_attack_{int(time.time())}"
        
        user_profiles = []
        
        # Create attack user profiles
        for i in range(attack_user_count):
            profile = UserBehaviorProfile(
                user_id=f"attacker_{scenario_id}_{i:04d}",
                avg_transaction_amount=self.amount_statistics['p25'],  # Often small amounts
                transaction_frequency_per_day=attack_intensity * 24,  # Very high frequency
                preferred_transaction_hours=list(range(24)),  # Any hour
                amount_variance=0.1,  # Consistent amounts
                risk_score=0.9
            )
            user_profiles.append(profile)
        
        # Create legitimate user profiles for contrast
        for i in range(legitimate_user_count):
            template = random.choice(self.user_behavior_templates[:2])  # Low to medium risk
            profile = UserBehaviorProfile(
                user_id=f"legitimate_{scenario_id}_{i:04d}",
                avg_transaction_amount=template['avg_transaction_amount'],
                transaction_frequency_per_day=template['transaction_frequency_per_day'],
                preferred_transaction_hours=template['preferred_hours'],
                amount_variance=template['amount_variance'],
                risk_score=template['risk_score']
            )
            user_profiles.append(profile)
        
        # Calculate expected outcomes
        total_transactions = int(sum(
            profile.transaction_frequency_per_day * duration_hours / 24 
            for profile in user_profiles
        ))
        
        # Attack transactions should be flagged
        attack_transactions = int(sum(
            profile.transaction_frequency_per_day * duration_hours / 24 
            for profile in user_profiles[:attack_user_count]
        ))
        
        return FraudScenario(
            scenario_id=scenario_id,
            scenario_type=ScenarioType.VELOCITY_ATTACK,
            name="Velocity Attack Scenario",
            description=f"Rapid transaction attack with {attack_intensity}x normal velocity",
            user_profiles=user_profiles,
            transaction_count=total_transactions,
            duration_hours=duration_hours,
            expected_fraud_rate=attack_transactions / total_transactions,
            fraud_injection_rules={
                'velocity_threshold_multiplier': attack_intensity,
                'attack_duration_minutes': duration_hours * 60,
                'small_amount_preference': True
            },
            temporal_patterns=self.temporal_patterns,
            expected_alerts=attack_user_count,  # Each attacker should be flagged
            expected_blocked_users=attack_user_count,
            expected_cross_service_records={
                'postgres_alerts': attack_user_count,
                'redis_profiles': len(user_profiles),
                'clickhouse_transactions': total_transactions
            },
            max_processing_latency_ms=50.0,  # Should be fast despite high volume
            complexity_score=3.0
        )
    
    def create_amount_anomaly_scenario(self,
                                     anomaly_user_count: int = 5,
                                     legitimate_user_count: int = 25,
                                     anomaly_multiplier: float = 10.0,
                                     duration_hours: float = 12.0) -> FraudScenario:
        """
        Create scenario with unusual transaction amounts.
        
        Tests detection of transactions that are significantly larger
        or smaller than user's historical patterns.
        """
        scenario_id = f"amount_anomaly_{int(time.time())}"
        
        user_profiles = []
        
        # Create users who will make anomalous transactions
        for i in range(anomaly_user_count):
            template = random.choice(self.user_behavior_templates[:2])  # Start with normal behavior
            
            # Anomalous amount (much larger than typical)
            anomalous_amount = template['avg_transaction_amount'] * anomaly_multiplier
            
            profile = UserBehaviorProfile(
                user_id=f"anomaly_{scenario_id}_{i:04d}",
                avg_transaction_amount=anomalous_amount,
                transaction_frequency_per_day=template['transaction_frequency_per_day'] * 0.5,  # Fewer transactions
                preferred_transaction_hours=template['preferred_hours'],
                amount_variance=0.1,  # Low variance for detectability
                risk_score=0.7
            )
            user_profiles.append(profile)
        
        # Create legitimate users
        for i in range(legitimate_user_count):
            template = random.choice(self.user_behavior_templates[:3])
            profile = UserBehaviorProfile(
                user_id=f"legitimate_{scenario_id}_{i:04d}",
                avg_transaction_amount=template['avg_transaction_amount'],
                transaction_frequency_per_day=template['transaction_frequency_per_day'],
                preferred_transaction_hours=template['preferred_hours'],
                amount_variance=template['amount_variance'],
                risk_score=template['risk_score']
            )
            user_profiles.append(profile)
        
        total_transactions = int(sum(
            profile.transaction_frequency_per_day * duration_hours / 24
            for profile in user_profiles
        ))
        
        anomaly_transactions = int(sum(
            profile.transaction_frequency_per_day * duration_hours / 24
            for profile in user_profiles[:anomaly_user_count]
        ))
        
        return FraudScenario(
            scenario_id=scenario_id,
            scenario_type=ScenarioType.AMOUNT_ANOMALY,
            name="Amount Anomaly Scenario", 
            description=f"Transactions {anomaly_multiplier}x larger than typical amounts",
            user_profiles=user_profiles,
            transaction_count=total_transactions,
            duration_hours=duration_hours,
            expected_fraud_rate=anomaly_transactions / total_transactions,
            fraud_injection_rules={
                'amount_anomaly_multiplier': anomaly_multiplier,
                'anomaly_detection_enabled': True
            },
            temporal_patterns=self.temporal_patterns,
            expected_alerts=anomaly_user_count,
            expected_blocked_users=0,  # Amount anomalies might not auto-block
            expected_cross_service_records={
                'postgres_alerts': anomaly_user_count,
                'redis_profiles': len(user_profiles),
                'clickhouse_transactions': total_transactions
            },
            complexity_score=2.5
        )
    
    def create_mixed_population_scenario(self,
                                       total_users: int = 100,
                                       duration_hours: float = 24.0,
                                       target_fraud_rate: float = 0.03) -> FraudScenario:
        """
        Create realistic mixed scenario matching IEEE-CIS fraud rate.
        
        This scenario most closely matches real-world conditions with
        a realistic mix of legitimate and fraudulent behavior.
        """
        scenario_id = f"mixed_population_{int(time.time())}"
        
        # Calculate fraud user count to match target fraud rate
        # (approximation - actual fraud rate depends on transaction frequencies)
        fraud_user_count = max(1, int(total_users * target_fraud_rate * 5))  # Multiplier for realistic distribution
        legitimate_user_count = total_users - fraud_user_count
        
        user_profiles = []
        
        # Create fraudulent users using high-risk template
        high_risk_template = self.user_behavior_templates[-1]  # High-risk template
        for i in range(fraud_user_count):
            profile = UserBehaviorProfile(
                user_id=f"fraud_{scenario_id}_{i:04d}",
                avg_transaction_amount=high_risk_template['avg_transaction_amount'] * random.uniform(0.5, 2.0),
                transaction_frequency_per_day=high_risk_template['transaction_frequency_per_day'] * random.uniform(0.8, 1.5),
                preferred_transaction_hours=high_risk_template['preferred_hours'],
                amount_variance=high_risk_template['amount_variance'],
                risk_score=random.uniform(0.7, 1.0)
            )
            user_profiles.append(profile)
        
        # Create legitimate users with varied profiles
        for i in range(legitimate_user_count):
            template = random.choice(self.user_behavior_templates[:3])
            profile = UserBehaviorProfile(
                user_id=f"legitimate_{scenario_id}_{i:04d}",
                avg_transaction_amount=template['avg_transaction_amount'] * random.uniform(0.7, 1.3),
                transaction_frequency_per_day=template['transaction_frequency_per_day'] * random.uniform(0.8, 1.2),
                preferred_transaction_hours=template['preferred_hours'],
                amount_variance=template['amount_variance'],
                risk_score=random.uniform(0.0, 0.3)
            )
            user_profiles.append(profile)
        
        # Shuffle to mix legitimate and fraud users
        random.shuffle(user_profiles)
        
        total_transactions = int(sum(
            profile.transaction_frequency_per_day * duration_hours / 24
            for profile in user_profiles
        ))
        
        return FraudScenario(
            scenario_id=scenario_id,
            scenario_type=ScenarioType.MIXED_POPULATION,
            name="Mixed Population Scenario",
            description=f"Realistic mix with {target_fraud_rate:.1%} target fraud rate matching IEEE-CIS",
            user_profiles=user_profiles,
            transaction_count=total_transactions,
            duration_hours=duration_hours,
            expected_fraud_rate=target_fraud_rate,
            fraud_injection_rules={
                'realistic_fraud_patterns': True,
                'temporal_bias_enabled': True,
                'amount_patterns_enabled': True
            },
            temporal_patterns=self.temporal_patterns,
            expected_alerts=fraud_user_count,
            expected_blocked_users=int(fraud_user_count * 0.7),  # Not all fraud users get blocked immediately
            expected_cross_service_records={
                'postgres_alerts': fraud_user_count,
                'redis_profiles': total_users,
                'clickhouse_transactions': total_transactions
            },
            complexity_score=4.0
        )
    
    def create_stress_test_scenario(self,
                                   target_tps: float = 1000.0,
                                   duration_minutes: float = 10.0,
                                   concurrent_users: int = 500) -> FraudScenario:
        """
        Create high-volume stress test scenario.
        
        Tests system performance and stability under high load
        with realistic fraud patterns maintained.
        """
        scenario_id = f"stress_test_{int(time.time())}"
        duration_hours = duration_minutes / 60.0
        
        # Calculate transaction distribution across users
        total_transactions = int(target_tps * duration_minutes * 60)
        transactions_per_user = total_transactions / concurrent_users
        
        user_profiles = []
        
        # Create high-frequency user profiles
        fraud_user_count = int(concurrent_users * self.fraud_rate_baseline)
        
        for i in range(concurrent_users):
            is_fraud_user = i < fraud_user_count
            
            if is_fraud_user:
                template = self.user_behavior_templates[-1]  # High-risk
            else:
                template = random.choice(self.user_behavior_templates[:3])
            
            # High frequency to achieve target TPS
            frequency = transactions_per_user / duration_hours * 24
            
            profile = UserBehaviorProfile(
                user_id=f"stress_{scenario_id}_{i:04d}",
                avg_transaction_amount=template['avg_transaction_amount'] * random.uniform(0.8, 1.2),
                transaction_frequency_per_day=frequency,
                preferred_transaction_hours=list(range(24)),  # Any hour for stress test
                amount_variance=template['amount_variance'],
                risk_score=template['risk_score']
            )
            user_profiles.append(profile)
        
        return FraudScenario(
            scenario_id=scenario_id,
            scenario_type=ScenarioType.STRESS_TEST,
            name=f"Stress Test - {target_tps} TPS",
            description=f"High-volume load test with {concurrent_users} concurrent users at {target_tps} TPS",
            user_profiles=user_profiles,
            transaction_count=total_transactions,
            duration_hours=duration_hours,
            expected_fraud_rate=self.fraud_rate_baseline,
            fraud_injection_rules={
                'maintain_fraud_rate': True,
                'high_volume_optimizations': True
            },
            temporal_patterns=self.temporal_patterns,
            expected_alerts=fraud_user_count,
            expected_blocked_users=int(fraud_user_count * 0.8),
            expected_cross_service_records={
                'postgres_alerts': fraud_user_count,
                'redis_profiles': concurrent_users,
                'clickhouse_transactions': total_transactions
            },
            max_processing_latency_ms=100.0,
            min_throughput_tps=target_tps * 0.95,  # Allow 5% degradation
            complexity_score=5.0
        )
    
    def generate_transactions_for_scenario(self, scenario: FraudScenario) -> Generator[Dict[str, Any], None, None]:
        """
        Generate transaction stream for the given scenario.
        
        Yields transaction dictionaries that can be sent to Kafka
        or used for testing the fraud detection pipeline.
        """
        start_time = datetime.now()
        end_time = start_time + timedelta(hours=scenario.duration_hours)
        
        # Pre-calculate transaction schedule for all users
        scheduled_transactions = []
        
        for profile in scenario.user_profiles:
            user_transaction_count = int(
                profile.transaction_frequency_per_day * scenario.duration_hours / 24
            )
            
            for _ in range(user_transaction_count):
                # Schedule transaction at random time within duration
                transaction_time = start_time + timedelta(
                    seconds=random.uniform(0, scenario.duration_hours * 3600)
                )
                
                # Apply user's preferred hours bias
                if profile.preferred_transaction_hours:
                    preferred_bias = transaction_time.hour in profile.preferred_transaction_hours
                    if not preferred_bias and random.random() < 0.7:
                        # 70% chance to reschedule to preferred hour
                        preferred_hour = random.choice(profile.preferred_transaction_hours)
                        transaction_time = transaction_time.replace(hour=preferred_hour)
                
                scheduled_transactions.append((transaction_time, profile))
        
        # Sort by time
        scheduled_transactions.sort(key=lambda x: x[0])
        
        # Generate transactions in chronological order
        for i, (transaction_time, profile) in enumerate(scheduled_transactions):
            transaction_id = f"{scenario.scenario_id}_{i:06d}"
            
            # Determine if this should be a fraud transaction
            is_fraud = self._determine_fraud_status(profile, transaction_time, scenario)
            
            # Generate transaction amount
            amount = profile.generate_transaction_amount()
            
            # Apply scenario-specific amount modifications
            if scenario.scenario_type == ScenarioType.AMOUNT_ANOMALY and profile.risk_score > 0.6:
                # Make amount anomalous for high-risk users in amount anomaly scenarios
                amount *= scenario.fraud_injection_rules.get('amount_anomaly_multiplier', 5.0)
            
            transaction = {
                'transaction_id': transaction_id,
                'user_id': profile.user_id,
                'amount': round(amount, 2),
                'timestamp': transaction_time.isoformat(),
                'is_fraud': 1 if is_fraud else 0,
                'scenario_id': scenario.scenario_id,
                'scenario_type': scenario.scenario_type.value,
                
                # IEEE-CIS style additional features
                'product_cd': random.choice(['W', 'C', 'H', 'R', 'S']),
                'transaction_hour': transaction_time.hour,
                'transaction_day': transaction_time.weekday(),
                'user_risk_score': profile.risk_score,
                
                # Test validation metadata
                'expected_alert': is_fraud,
                'processing_priority': 'high' if is_fraud else 'normal'
            }
            
            yield transaction
    
    def _determine_fraud_status(self, 
                              profile: UserBehaviorProfile, 
                              transaction_time: datetime, 
                              scenario: FraudScenario) -> bool:
        """Determine if transaction should be marked as fraud."""
        
        # Base probability from user risk score
        fraud_probability = profile.risk_score
        
        # Apply temporal bias (higher fraud rates at unusual hours)
        hour = transaction_time.hour
        if hour in self.temporal_patterns:
            temporal_multiplier = self.temporal_patterns[hour] / self.fraud_rate_baseline
            fraud_probability *= temporal_multiplier
        
        # Apply scenario-specific rules
        if scenario.scenario_type == ScenarioType.LEGITIMATE_BASELINE:
            return False  # Never fraud in baseline
        
        elif scenario.scenario_type == ScenarioType.VELOCITY_ATTACK:
            # High-frequency users are likely fraud
            return profile.transaction_frequency_per_day > 10.0
        
        elif scenario.scenario_type == ScenarioType.AMOUNT_ANOMALY:
            # High-risk users with anomalous amounts are likely fraud
            return profile.risk_score > 0.6
        
        elif scenario.scenario_type == ScenarioType.STRESS_TEST:
            # Maintain baseline fraud rate
            fraud_probability = min(fraud_probability, self.fraud_rate_baseline * 2)
        
        # Random determination based on calculated probability
        return random.random() < min(fraud_probability, 0.8)  # Cap at 80% fraud probability
    
    def get_scenario_summary(self, scenario: FraudScenario) -> Dict[str, Any]:
        """Get comprehensive scenario summary for reporting."""
        legitimate_users = sum(1 for p in scenario.user_profiles if p.risk_score < 0.5)
        high_risk_users = sum(1 for p in scenario.user_profiles if p.risk_score >= 0.5)
        
        return {
            'scenario_id': scenario.scenario_id,
            'scenario_type': scenario.scenario_type.value,
            'name': scenario.name,
            'complexity_score': scenario.complexity_score,
            'user_breakdown': {
                'total_users': len(scenario.user_profiles),
                'legitimate_users': legitimate_users,
                'high_risk_users': high_risk_users
            },
            'transaction_metrics': {
                'total_transactions': scenario.transaction_count,
                'duration_hours': scenario.duration_hours,
                'expected_fraud_rate': scenario.expected_fraud_rate,
                'estimated_tps': scenario.transaction_count / (scenario.duration_hours * 3600)
            },
            'performance_expectations': {
                'max_processing_latency_ms': scenario.max_processing_latency_ms,
                'min_throughput_tps': scenario.min_throughput_tps
            },
            'expected_outcomes': {
                'expected_alerts': scenario.expected_alerts,
                'expected_blocked_users': scenario.expected_blocked_users,
                'cross_service_records': scenario.expected_cross_service_records
            }
        }
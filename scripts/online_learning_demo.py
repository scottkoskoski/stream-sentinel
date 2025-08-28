#!/usr/bin/env python3
# /stream-sentinel/scripts/online_learning_demo.py

"""
Online Learning System Demo Script

This script demonstrates the complete online learning pipeline by:
1. Setting up the system components
2. Generating synthetic feedback data
3. Showing model updates in action
4. Running A/B tests
5. Demonstrating drift detection and response

This is a comprehensive demo showcasing the production-ready capabilities
of the online learning system.
"""

import json
import time
import asyncio
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.ml.online_learning import (
    OnlineLearningConfig,
    get_online_learning_config,
    FeedbackProcessor,
    FeedbackRecord,
    FeedbackLabel,
    FeedbackSource,
    DriftDetector,
    IncrementalLearner,
    ModelRegistry,
    ModelMetadata,
    ModelStatus,
    DeploymentStage,
    ABTestManager,
    OnlineLearningOrchestrator
)


class OnlineLearningDemo:
    """Comprehensive demo of the online learning system."""
    
    def __init__(self):
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize configuration
        self.config = get_online_learning_config()
        self.logger.info("Demo configuration loaded")
        
        # Initialize components
        self.feedback_processor = None
        self.drift_detector = None
        self.incremental_learner = None
        self.model_registry = None
        self.ab_test_manager = None
        
        # Demo data
        self.demo_transactions = []
        self.demo_feedback = []
        
        self.logger.info("OnlineLearningDemo initialized")
    
    async def run_demo(self):
        """Run the complete demo workflow."""
        self.logger.info("Starting Online Learning System Demo")
        
        try:
            # 1. Initialize components
            await self._demo_component_initialization()
            
            # 2. Demonstrate feedback processing
            await self._demo_feedback_processing()
            
            # 3. Demonstrate drift detection
            await self._demo_drift_detection()
            
            # 4. Demonstrate incremental learning
            await self._demo_incremental_learning()
            
            # 5. Demonstrate model registry
            await self._demo_model_registry()
            
            # 6. Demonstrate A/B testing
            await self._demo_ab_testing()
            
            # 7. Show system integration
            await self._demo_system_integration()
            
            self.logger.info("Online Learning System Demo completed successfully!")
            
        except Exception as e:
            self.logger.error(f" Demo failed: {e}")
            raise
    
    async def _demo_component_initialization(self):
        """Demo 1: Component initialization and health checks."""
        self.logger.info("\n" + "="*60)
        self.logger.info("DEMO 1: Component Initialization")
        self.logger.info("="*60)
        
        # Initialize components
        self.feedback_processor = FeedbackProcessor(self.config)
        self.drift_detector = DriftDetector(self.config)
        self.incremental_learner = IncrementalLearner(self.config)
        self.model_registry = ModelRegistry(self.config)
        self.ab_test_manager = ABTestManager(self.config)
        
        # Show component status
        components = [
            ("Feedback Processor", self.feedback_processor),
            ("Drift Detector", self.drift_detector),
            ("Incremental Learner", self.incremental_learner),
            ("Model Registry", self.model_registry),
            ("A/B Test Manager", self.ab_test_manager)
        ]
        
        for name, component in components:
            try:
                # Simple health check - try to call a basic method
                if hasattr(component, 'get_statistics'):
                    stats = component.get_statistics()
                    self.logger.info(f" {name}: Healthy (stats available)")
                elif hasattr(component, 'get_drift_summary'):
                    summary = component.get_drift_summary()
                    self.logger.info(f" {name}: Healthy (summary available)")
                else:
                    self.logger.info(f" {name}: Initialized")
            except Exception as e:
                self.logger.warning(f"  {name}: {e}")
        
        await asyncio.sleep(2)  # Pause for demo effect
    
    async def _demo_feedback_processing(self):
        """Demo 2: Feedback collection and processing."""
        self.logger.info("\n" + "="*60)
        self.logger.info("DEMO 2: Feedback Processing")
        self.logger.info("="*60)
        
        # Generate synthetic feedback records
        feedback_records = self._generate_synthetic_feedback(20)
        
        self.logger.info(f"Generated {len(feedback_records)} synthetic feedback records")
        
        # Add feedback to processor
        for record in feedback_records:
            success = self.feedback_processor.add_feedback(record)
            if success:
                self.logger.debug(f"Added feedback for transaction {record.transaction_id}")
        
        # Process feedback
        processed_feedback = self.feedback_processor.process_pending_feedback()
        self.logger.info(f" Processed {len(processed_feedback)} feedback records")
        
        # Show processing statistics
        stats = self.feedback_processor.get_statistics()
        self.logger.info(f" Feedback Statistics:")
        for key, value in stats.items():
            self.logger.info(f"   {key}: {value}")
        
        # Show quality distribution
        quality_dist = stats.get('quality_distribution', {})
        self.logger.info(f" Quality Distribution: {quality_dist}")
        
        await asyncio.sleep(2)
    
    async def _demo_drift_detection(self):
        """Demo 3: Drift detection and alerting."""
        self.logger.info("\n" + "="*60)
        self.logger.info("DEMO 3: Drift Detection")
        self.logger.info("="*60)
        
        # Set up reference data for drift detection
        self.logger.info("Setting up reference dataset...")
        reference_data = self._generate_reference_dataset()
        reference_labels = np.random.binomial(1, 0.03, len(reference_data))  # 3% fraud rate
        
        self.drift_detector.set_reference_data(reference_data, reference_labels)
        
        # Simulate some predictions to build current window
        self.logger.info("Simulating predictions for drift detection...")
        for i in range(100):
            # Simulate prediction with slight distribution shift
            features = self._generate_shifted_features()
            prediction = np.random.random()
            actual_label = 1 if np.random.random() < 0.05 else 0  # 5% fraud rate (shift!)
            
            self.drift_detector.add_prediction_sample(features, prediction, actual_label)
        
        # Run drift detection
        self.logger.info("Running drift detection analysis...")
        drift_alerts = self.drift_detector.detect_drift(force_check=True)
        
        if drift_alerts:
            self.logger.info(f"ALERT: Detected {len(drift_alerts)} drift alerts:")
            for alert in drift_alerts:
                self.logger.info(f"   - {alert.drift_type.value}: {alert.severity.value} "
                               f"(score: {alert.drift_score:.3f}, p-value: {alert.p_value:.3f})")
        else:
            self.logger.info("No significant drift detected")
        
        # Show drift summary
        drift_summary = self.drift_detector.get_drift_summary()
        self.logger.info(f" Drift Summary: {drift_summary}")
        
        await asyncio.sleep(2)
    
    async def _demo_incremental_learning(self):
        """Demo 4: Incremental model updates."""
        self.logger.info("\n" + "="*60)
        self.logger.info("🧠 DEMO 4: Incremental Learning")
        self.logger.info("="*60)
        
        # Get processed feedback from earlier demo
        training_feedback = self.feedback_processor.get_training_feedback(limit=50)
        
        if not training_feedback:
            self.logger.info("No training feedback available, generating synthetic data...")
            # Generate synthetic training data
            training_feedback = self._generate_synthetic_processed_feedback(50)
        
        self.logger.info(f"Using {len(training_feedback)} feedback records for training")
        
        # Add training batch
        success = self.incremental_learner.add_training_batch(training_feedback)
        if success:
            self.logger.info("Training batch added successfully")
        else:
            self.logger.warning("  Failed to add training batch")
            return
        
        # Perform incremental update
        self.logger.info("Performing incremental model update...")
        update_result = self.incremental_learner.perform_incremental_update(force_update=True)
        
        if update_result and update_result.success:
            self.logger.info(f" Model update completed: {update_result.update_id}")
            self.logger.info(f"   Training time: {update_result.training_time_seconds:.2f}s")
            self.logger.info(f"   Samples used: {update_result.samples_used}")
            
            # Show performance changes
            perf_change = update_result.performance_change
            self.logger.info("Performance Changes:")
            for metric, change in perf_change.items():
                direction = "↑" if change > 0 else "↓" if change < 0 else "→"
                self.logger.info(f"   {metric}: {direction} {change:+.3f}")
        else:
            self.logger.warning("  Model update failed")
        
        # Show learner statistics
        learner_stats = self.incremental_learner.get_update_statistics()
        self.logger.info(f" Learner Statistics: {learner_stats}")
        
        await asyncio.sleep(2)
    
    async def _demo_model_registry(self):
        """Demo 5: Model registry and versioning."""
        self.logger.info("\n" + "="*60)
        self.logger.info("DEMO 5: Model Registry")
        self.logger.info("="*60)
        
        # Create demo model metadata
        model_metadata = ModelMetadata(
            model_id="demo_fraud_model",
            version="2.1.0",
            name="Demo Fraud Detection Model",
            description="Demonstration model for online learning system",
            model_type="lightgbm",
            algorithm="gradient_boosting",
            framework="lightgbm",
            training_data_hash="demo_hash_12345",
            performance_metrics={
                "auc": 0.8723,
                "precision": 0.7845,
                "recall": 0.8234,
                "f1": 0.8032
            },
            validation_metrics={
                "auc": 0.8651,
                "precision": 0.7723,
                "recall": 0.8156,
                "f1": 0.7935
            },
            training_start_time=datetime.now().isoformat(),
            training_end_time=datetime.now().isoformat(),
            training_duration_minutes=15.5,
            training_samples=10000,
            status=ModelStatus.DEVELOPMENT,
            deployment_stage=DeploymentStage.TRAINING
        )
        
        # Create a simple demo model (just a placeholder)
        demo_model = {"type": "demo", "version": "2.1.0", "weights": [0.1, 0.2, 0.3]}
        
        # Register model
        self.logger.info("Registering demo model...")
        success = self.model_registry.register_model(demo_model, model_metadata)
        
        if success:
            self.logger.info("Model registered successfully")
            
            # Deploy to staging
            self.logger.info("Deploying model to staging...")
            deployed = self.model_registry.deploy_model(
                model_metadata.model_id,
                environment="staging",
                traffic_percentage=100.0
            )
            
            if deployed:
                self.logger.info("Model deployed to staging")
            else:
                self.logger.warning("  Staging deployment failed")
        else:
            self.logger.warning("  Model registration failed")
        
        # List models
        models = self.model_registry.list_models()
        self.logger.info(f" Registry contains {len(models)} models")
        
        for model in models[:3]:  # Show first 3
            self.logger.info(f"   - {model.model_id} v{model.version} ({model.status.value})")
        
        # Show registry statistics
        registry_stats = self.model_registry.get_registry_statistics()
        self.logger.info(f" Registry Statistics: {registry_stats}")
        
        await asyncio.sleep(2)
    
    async def _demo_ab_testing(self):
        """Demo 6: A/B testing framework."""
        self.logger.info("\n" + "="*60)
        self.logger.info("🧪 DEMO 6: A/B Testing")
        self.logger.info("="*60)
        
        # Create A/B test experiment
        experiment_id = self.ab_test_manager.create_experiment(
            name="Fraud Model Comparison v2.0 vs v2.1",
            description="Testing new model version against current production model",
            hypothesis="New model v2.1 will improve precision by 5% while maintaining recall",
            control_model=("demo_fraud_model", "2.0.0"),
            treatment_model=("demo_fraud_model", "2.1.0"),
            traffic_split=(0.6, 0.4),
            primary_metric="f1",
            minimum_effect_size=0.05
        )
        
        if experiment_id:
            self.logger.info(f" Created A/B test experiment: {experiment_id}")
            
            # Start the experiment
            started = self.ab_test_manager.start_experiment(experiment_id)
            if started:
                self.logger.info("A/B test experiment started")
                
                # Simulate some test traffic
                self.logger.info("Simulating test traffic...")
                for i in range(200):
                    user_id = f"user_{i % 50}"  # 50 unique users
                    
                    # Assign variant
                    variant_id = self.ab_test_manager.assign_variant(user_id)
                    
                    if variant_id:
                        # Simulate prediction result
                        is_treatment = "treatment" in variant_id
                        
                        # Treatment group has slightly better performance
                        base_performance = 0.7
                        performance_boost = 0.05 if is_treatment else 0.0
                        
                        prediction = np.random.random() * (base_performance + performance_boost)
                        actual_label = 1 if np.random.random() < 0.03 else 0
                        
                        # Record result
                        self.ab_test_manager.record_prediction_result(
                            user_id=user_id,
                            variant_id=variant_id,
                            prediction=prediction,
                            actual_label=actual_label,
                            transaction_amount=np.random.uniform(10, 1000)
                        )
                
                # Get experiment results
                results = self.ab_test_manager.get_experiment_results(experiment_id)
                if results:
                    self.logger.info("A/B Test Results:")
                    self.logger.info(f"   Status: {results['experiment_info']['status']}")
                    self.logger.info(f"   Sample Size: {results['experiment_info']['current_sample_size']}")
                    self.logger.info(f"   P-value: {results['statistical_results']['p_value']:.4f}")
                    self.logger.info(f"   Decision: {results['statistical_results']['decision']}")
                    
                    for variant_info in results['variants']:
                        variant_type = variant_info['variant_type']
                        f1_score = variant_info['performance_metrics']['f1_score']
                        self.logger.info(f"   {variant_type}: F1={f1_score:.3f}")
            else:
                self.logger.warning("  Failed to start A/B test experiment")
        else:
            self.logger.warning("  Failed to create A/B test experiment")
        
        # Show A/B testing statistics
        ab_stats = self.ab_test_manager.get_ab_test_statistics()
        self.logger.info(f" A/B Testing Statistics: {ab_stats}")
        
        await asyncio.sleep(2)
    
    async def _demo_system_integration(self):
        """Demo 7: Complete system integration."""
        self.logger.info("\n" + "="*60)
        self.logger.info("DEMO 7: System Integration")
        self.logger.info("="*60)
        
        # Show system-wide statistics
        self.logger.info("System-wide Statistics:")
        
        # Feedback processing stats
        feedback_stats = self.feedback_processor.get_statistics()
        self.logger.info(f"   Feedback processed: {feedback_stats.get('total_processed', 0)}")
        
        # Drift detection stats
        drift_summary = self.drift_detector.get_drift_summary()
        self.logger.info(f"   Drift alerts (24h): {drift_summary.get('total_alerts_24h', 0)}")
        
        # Model updates
        learner_stats = self.incremental_learner.get_update_statistics()
        self.logger.info(f"   Model updates: {learner_stats.get('total_updates', 0)}")
        
        # Model registry
        registry_stats = self.model_registry.get_registry_statistics()
        self.logger.info(f"   Models registered: {registry_stats.get('total_models', 0)}")
        
        # A/B testing
        ab_stats = self.ab_test_manager.get_ab_test_statistics()
        self.logger.info(f"   A/B tests: {ab_stats.get('total_experiments', 0)}")
        
        # Demonstrate workflow integration
        self.logger.info("\n Demonstrating workflow integration:")
        self.logger.info("1. Feedback → Processing → Training Data")
        self.logger.info("2. Training Data → Model Update → Registry")
        self.logger.info("3. Registry → Deployment → A/B Testing")
        self.logger.info("4. A/B Testing → Performance Monitoring → Drift Detection")
        self.logger.info("5. Drift Detection → Alerts → Automated Retraining")
        
        await asyncio.sleep(2)
    
    def _generate_synthetic_feedback(self, count: int) -> List[FeedbackRecord]:
        """Generate synthetic feedback records for demo."""
        feedback_records = []
        
        for i in range(count):
            # Create realistic feedback
            transaction_id = f"TXN_{1000000 + i}"
            is_fraud = np.random.random() < 0.15  # 15% fraud in feedback
            
            record = FeedbackRecord(
                transaction_id=transaction_id,
                feedback_id=f"FB_{transaction_id}_{int(time.time())}",
                label=FeedbackLabel.FRAUD if is_fraud else FeedbackLabel.LEGITIMATE,
                confidence=np.random.uniform(0.7, 0.95),
                source=FeedbackSource.MANUAL_INVESTIGATION,
                investigator_id=f"investigator_{np.random.randint(1, 6)}",
                timestamp=datetime.now().isoformat(),
                investigation_duration_minutes=np.random.uniform(5, 30),
                original_prediction=np.random.uniform(0.1, 0.9),
                original_features={
                    "amount": np.random.uniform(10, 1000),
                    "hour": np.random.randint(0, 24),
                    "velocity": np.random.uniform(0, 20)
                },
                investigator_experience_score=np.random.uniform(0.8, 1.0)
            )
            
            feedback_records.append(record)
        
        return feedback_records
    
    def _generate_reference_dataset(self) -> pd.DataFrame:
        """Generate reference dataset for drift detection."""
        n_samples = 1000
        
        data = {
            'amount': np.random.lognormal(4, 1.5, n_samples),
            'hour': np.random.randint(0, 24, n_samples),
            'day': np.random.randint(0, 7, n_samples),
            'velocity': np.random.exponential(2, n_samples),
            'user_age_days': np.random.uniform(1, 365, n_samples)
        }
        
        return pd.DataFrame(data)
    
    def _generate_shifted_features(self) -> Dict[str, Any]:
        """Generate features with distribution shift for drift demo."""
        return {
            'amount': max(0, np.random.lognormal(4.2, 1.7)),  # Slightly higher amounts
            'hour': np.random.randint(0, 24),
            'day': np.random.randint(0, 7),
            'velocity': np.random.exponential(2.5),  # Higher velocity
            'user_age_days': np.random.uniform(1, 365)
        }
    
    def _generate_synthetic_processed_feedback(self, count: int) -> List[Any]:
        """Generate synthetic processed feedback for incremental learning."""
        # This is a placeholder - in a real system, this would come from
        # the feedback processor
        return []


async def main():
    """Run the online learning demo."""
    demo = OnlineLearningDemo()
    await demo.run_demo()


if __name__ == "__main__":
    print("""
     Stream-Sentinel Online Learning System Demo
    ==============================================
    
    This demo showcases the complete online learning pipeline:
    • Feedback Processing & Validation
    • Drift Detection & Monitoring  
    • Incremental Model Updates
    • Model Registry & Versioning
    • A/B Testing Framework
    • System Integration
    
    Starting demo...
    """)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user")
    except Exception as e:
        print(f"\n\nDemo failed with error: {e}")
        raise
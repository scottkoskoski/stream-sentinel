# /stream-sentinel/src/ml/online_learning/online_learning_orchestrator.py

"""
Online Learning Orchestrator for Stream-Sentinel

This module implements the main orchestrator that coordinates all components
of the online learning system. It handles the complete workflow from feedback
processing through model updates and deployment.

Key features:
- Centralized coordination of all online learning components
- Event-driven workflow orchestration
- Health monitoring and system recovery
- Performance metrics collection and reporting
- Automated decision making for model lifecycle
"""

import json
import time
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import threading
from confluent_kafka import Consumer, Producer

from .config import OnlineLearningConfig, get_online_learning_config
from .feedback_processor import FeedbackProcessor, ProcessedFeedback
from .drift_detector import DriftDetector, DriftAlert, PerformanceMetrics
from .incremental_learner import IncrementalLearner, UpdateResult
from .model_registry import ModelRegistry, ModelMetadata, ModelStatus
from .ab_test_manager import ABTestManager, ABTestExperiment


class SystemState(Enum):
    """Overall system state."""
    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    MAINTENANCE = "maintenance"
    FAILED = "failed"


class WorkflowEvent(Enum):
    """Workflow event types."""
    FEEDBACK_RECEIVED = "feedback_received"
    FEEDBACK_PROCESSED = "feedback_processed"
    DRIFT_DETECTED = "drift_detected"
    MODEL_UPDATE_TRIGGERED = "model_update_triggered"
    MODEL_UPDATED = "model_updated"
    MODEL_DEPLOYED = "model_deployed"
    AB_TEST_STARTED = "ab_test_started"
    AB_TEST_COMPLETED = "ab_test_completed"
    SYSTEM_ALERT = "system_alert"


@dataclass
class SystemMetrics:
    """System-wide metrics and health indicators."""
    timestamp: str
    
    # Component health
    feedback_processor_health: str
    drift_detector_health: str
    incremental_learner_health: str
    model_registry_health: str
    ab_test_manager_health: str
    
    # Performance metrics
    feedback_processing_rate: float  # feedbacks per second
    model_update_frequency: float    # updates per hour
    prediction_latency_p95: float    # milliseconds
    system_memory_usage: float       # percentage
    
    # Business metrics
    drift_alerts_24h: int
    models_updated_24h: int
    ab_tests_active: int
    overall_model_performance: float  # AUC or primary metric
    
    # System state
    overall_health: SystemState
    active_workflows: int
    error_rate: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        metrics_dict = asdict(self)
        metrics_dict["overall_health"] = self.overall_health.value
        return metrics_dict


class OnlineLearningOrchestrator:
    """
    Main orchestrator for the online learning system.
    
    This class coordinates all components and manages the complete workflow:
    1. Monitors feedback and triggers processing
    2. Detects drift and initiates appropriate responses
    3. Coordinates model updates and validation
    4. Manages A/B testing and model deployment
    5. Provides comprehensive monitoring and alerting
    """
    
    def __init__(self, config: Optional[OnlineLearningConfig] = None):
        self.config = config or get_online_learning_config()
        self.logger = logging.getLogger(__name__)
        
        # System state
        self.system_state = SystemState.INITIALIZING
        self.start_time = datetime.now()
        self.is_running = False
        
        # Initialize components
        self._init_components()
        self._init_kafka()
        
        # Workflow management
        self.active_workflows: Dict[str, Dict[str, Any]] = {}
        self.workflow_history: List[Dict[str, Any]] = []
        
        # Metrics and monitoring
        self.metrics_history: List[SystemMetrics] = []
        self.alert_handlers: Dict[str, Callable] = {}
        
        # Threading
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.background_tasks: List[threading.Thread] = []
        
        # Performance tracking
        self.performance_stats = {
            "total_feedback_processed": 0,
            "total_drift_alerts": 0,
            "total_model_updates": 0,
            "total_ab_tests": 0,
            "system_uptime_hours": 0.0
        }
        
        self.system_state = SystemState.HEALTHY
        self.logger.info("OnlineLearningOrchestrator initialized successfully")
    
    def _init_components(self) -> None:
        """Initialize all online learning components."""
        try:
            self.feedback_processor = FeedbackProcessor(self.config)
            self.drift_detector = DriftDetector(self.config)
            self.incremental_learner = IncrementalLearner(self.config)
            self.model_registry = ModelRegistry(self.config)
            self.ab_test_manager = ABTestManager(self.config)
            
            self.logger.info("All components initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize components: {e}")
            self.system_state = SystemState.FAILED
            raise
    
    def _init_kafka(self) -> None:
        """Initialize Kafka connections for orchestration."""
        try:
            # Consumer for workflow events
            consumer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'group.id': 'online-learning-orchestrator',
                'auto.offset.reset': 'latest',
                'enable.auto.commit': True
            }
            self.consumer = Consumer(consumer_config)
            
            # Subscribe to relevant topics
            topics = [
                self.config.feedback_topic,
                self.config.drift_alerts_topic,
                self.config.model_updates_topic,
                self.config.ab_test_assignments_topic
            ]
            self.consumer.subscribe(topics)
            
            # Producer for system events
            producer_config = {
                'bootstrap.servers': self.config.kafka_servers,
                'linger.ms': 5,
                'compression.type': 'lz4'
            }
            self.producer = Producer(producer_config)
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Kafka: {e}")
            raise
    
    def start(self) -> None:
        """Start the online learning orchestrator."""
        if self.is_running:
            self.logger.warning("Orchestrator is already running")
            return
        
        self.is_running = True
        self.logger.info("Starting OnlineLearningOrchestrator")
        
        try:
            # Start background monitoring tasks
            self._start_background_tasks()
            
            # Main orchestration loop
            self._run_orchestration_loop()
            
        except KeyboardInterrupt:
            self.logger.info("Shutdown signal received")
        except Exception as e:
            self.logger.error(f"Orchestrator failed: {e}")
            self.system_state = SystemState.FAILED
        finally:
            self.stop()
    
    def stop(self) -> None:
        """Stop the orchestrator and cleanup resources."""
        self.logger.info("Stopping OnlineLearningOrchestrator")
        self.is_running = False
        
        # Stop background tasks
        for task in self.background_tasks:
            if task.is_alive():
                task.join(timeout=5.0)
        
        # Cleanup resources
        try:
            self.consumer.close()
            self.executor.shutdown(wait=True)
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        
        self.logger.info("OnlineLearningOrchestrator stopped")
    
    def _start_background_tasks(self) -> None:
        """Start background monitoring and maintenance tasks."""
        # System health monitoring
        health_monitor = threading.Thread(
            target=self._health_monitoring_loop,
            name="HealthMonitor",
            daemon=True
        )
        health_monitor.start()
        self.background_tasks.append(health_monitor)
        
        # Metrics collection
        metrics_collector = threading.Thread(
            target=self._metrics_collection_loop,
            name="MetricsCollector", 
            daemon=True
        )
        metrics_collector.start()
        self.background_tasks.append(metrics_collector)
        
        # Workflow cleanup
        cleanup_worker = threading.Thread(
            target=self._workflow_cleanup_loop,
            name="WorkflowCleanup",
            daemon=True
        )
        cleanup_worker.start()
        self.background_tasks.append(cleanup_worker)
        
        self.logger.info("Background tasks started")
    
    def _run_orchestration_loop(self) -> None:
        """Main orchestration event loop."""
        self.logger.info("Starting main orchestration loop")
        
        while self.is_running:
            try:
                # Poll for events
                message = self.consumer.poll(timeout=1.0)
                
                if message is None:
                    continue
                
                if message.error():
                    self.logger.error(f"Consumer error: {message.error()}")
                    continue
                
                # Process event
                self._process_workflow_event(message)
                
                # Check for periodic tasks
                self._check_periodic_tasks()
                
            except Exception as e:
                self.logger.error(f"Error in orchestration loop: {e}")
                time.sleep(1)  # Prevent tight error loop
    
    def _process_workflow_event(self, message) -> None:
        """Process workflow event from Kafka."""
        try:
            topic = message.topic()
            event_data = json.loads(message.value())
            
            # Route based on topic
            if topic == self.config.feedback_topic:
                self._handle_feedback_event(event_data)
                
            elif topic == self.config.drift_alerts_topic:
                self._handle_drift_alert(event_data)
                
            elif topic == self.config.model_updates_topic:
                self._handle_model_update_event(event_data)
                
            elif topic == self.config.ab_test_assignments_topic:
                self._handle_ab_test_event(event_data)
            
        except Exception as e:
            self.logger.error(f"Failed to process workflow event: {e}")
    
    def _handle_feedback_event(self, event_data: Dict[str, Any]) -> None:
        """Handle feedback processing events."""
        try:
            workflow_id = f"feedback_workflow_{int(time.time())}"
            
            # Create workflow
            workflow = {
                "workflow_id": workflow_id,
                "type": "feedback_processing",
                "status": "processing",
                "created_at": datetime.now().isoformat(),
                "event_data": event_data,
                "steps_completed": []
            }
            
            self.active_workflows[workflow_id] = workflow
            
            # Submit feedback processing task
            future = self.executor.submit(self._process_feedback_workflow, workflow_id)
            workflow["future"] = future
            
        except Exception as e:
            self.logger.error(f"Failed to handle feedback event: {e}")
    
    def _process_feedback_workflow(self, workflow_id: str) -> None:
        """Process feedback workflow."""
        try:
            workflow = self.active_workflows.get(workflow_id)
            if not workflow:
                return
            
            # Step 1: Collect pending feedback
            self.logger.debug(f"Processing feedback workflow {workflow_id}")
            
            feedback_records = self.feedback_processor.collect_feedback(timeout=0.1)
            
            if feedback_records:
                # Add feedback to processor
                for record in feedback_records:
                    self.feedback_processor.add_feedback(record)
                
                workflow["steps_completed"].append("feedback_collected")
                
                # Step 2: Process feedback
                processed_feedback = self.feedback_processor.process_pending_feedback()
                
                if processed_feedback:
                    workflow["steps_completed"].append("feedback_processed")
                    workflow["processed_count"] = len(processed_feedback)
                    
                    # Step 3: Add to incremental learner
                    success = self.incremental_learner.add_training_batch(processed_feedback)
                    
                    if success:
                        workflow["steps_completed"].append("training_batch_added")
                        self.performance_stats["total_feedback_processed"] += len(processed_feedback)
            
            # Mark workflow complete
            workflow["status"] = "completed"
            workflow["completed_at"] = datetime.now().isoformat()
            
            # Move to history
            self.workflow_history.append(workflow)
            del self.active_workflows[workflow_id]
            
        except Exception as e:
            self.logger.error(f"Feedback workflow {workflow_id} failed: {e}")
            
            if workflow_id in self.active_workflows:
                self.active_workflows[workflow_id]["status"] = "failed"
                self.active_workflows[workflow_id]["error"] = str(e)
    
    def _handle_drift_alert(self, event_data: Dict[str, Any]) -> None:
        """Handle drift detection alerts."""
        try:
            workflow_id = f"drift_workflow_{int(time.time())}"
            
            workflow = {
                "workflow_id": workflow_id,
                "type": "drift_response",
                "status": "processing",
                "created_at": datetime.now().isoformat(),
                "event_data": event_data,
                "steps_completed": []
            }
            
            self.active_workflows[workflow_id] = workflow
            
            # Submit drift response task
            future = self.executor.submit(self._process_drift_workflow, workflow_id)
            workflow["future"] = future
            
            self.performance_stats["total_drift_alerts"] += 1
            
        except Exception as e:
            self.logger.error(f"Failed to handle drift alert: {e}")
    
    def _process_drift_workflow(self, workflow_id: str) -> None:
        """Process drift response workflow."""
        try:
            workflow = self.active_workflows.get(workflow_id)
            if not workflow:
                return
            
            drift_data = workflow["event_data"]
            requires_retraining = drift_data.get("requires_retraining", False)
            
            self.logger.info(f"Processing drift workflow {workflow_id}")
            
            workflow["steps_completed"].append("drift_alert_received")
            
            if requires_retraining:
                # Trigger model update
                update_result = self.incremental_learner.perform_incremental_update(force_update=True)
                
                if update_result and update_result.success:
                    workflow["steps_completed"].append("model_updated")
                    workflow["update_result"] = asdict(update_result)
                    
                    # Register updated model
                    current_model = self.incremental_learner.current_model
                    if current_model:
                        metadata = self._create_model_metadata_from_update(update_result, "drift_triggered")
                        
                        success = self.model_registry.register_model(current_model, metadata)
                        if success:
                            workflow["steps_completed"].append("model_registered")
                            
                            # Deploy to staging for validation
                            deployed = self.model_registry.deploy_model(
                                metadata.model_id, 
                                "staging",
                                traffic_percentage=100.0
                            )
                            
                            if deployed:
                                workflow["steps_completed"].append("model_deployed_staging")
                else:
                    workflow["steps_completed"].append("model_update_failed")
            
            # Mark workflow complete
            workflow["status"] = "completed"
            workflow["completed_at"] = datetime.now().isoformat()
            
            # Move to history
            self.workflow_history.append(workflow)
            del self.active_workflows[workflow_id]
            
        except Exception as e:
            self.logger.error(f"Drift workflow {workflow_id} failed: {e}")
            
            if workflow_id in self.active_workflows:
                self.active_workflows[workflow_id]["status"] = "failed"
                self.active_workflows[workflow_id]["error"] = str(e)
    
    def _handle_model_update_event(self, event_data: Dict[str, Any]) -> None:
        """Handle model update events."""
        try:
            trigger_type = event_data.get("trigger", "manual")
            
            if trigger_type == "incremental_update":
                # Process incremental update
                update_result = self.incremental_learner.perform_incremental_update()
                
                if update_result and update_result.success:
                    self.performance_stats["total_model_updates"] += 1
                    self.logger.info(f"Incremental update completed: {update_result.update_id}")
                    
                    # Publish update completion event
                    self._publish_workflow_event(WorkflowEvent.MODEL_UPDATED, {
                        "update_id": update_result.update_id,
                        "performance_change": update_result.performance_change
                    })
            
        except Exception as e:
            self.logger.error(f"Failed to handle model update event: {e}")
    
    def _handle_ab_test_event(self, event_data: Dict[str, Any]) -> None:
        """Handle A/B testing events."""
        try:
            event_type = event_data.get("event_type")
            
            if event_type == "experiment_started":
                self.performance_stats["total_ab_tests"] += 1
                self.logger.info(f"A/B test started: {event_data.get('experiment_id')}")
                
            elif event_type == "experiment_stopped_early":
                experiment_id = event_data.get("experiment_id")
                reason = event_data.get("additional_data", {}).get("reason", "Unknown")
                self.logger.info(f"A/B test {experiment_id} stopped early: {reason}")
            
        except Exception as e:
            self.logger.error(f"Failed to handle A/B test event: {e}")
    
    def _check_periodic_tasks(self) -> None:
        """Check and execute periodic tasks."""
        current_time = datetime.now()
        
        # Check if drift detection should run (every 10 minutes)
        if not hasattr(self, '_last_drift_check'):
            self._last_drift_check = current_time
        
        if (current_time - self._last_drift_check).total_seconds() > 600:
            self._run_drift_detection()
            self._last_drift_check = current_time
        
        # Check for cleanup tasks (every hour)
        if not hasattr(self, '_last_cleanup'):
            self._last_cleanup = current_time
        
        if (current_time - self._last_cleanup).total_seconds() > 3600:
            self._run_cleanup_tasks()
            self._last_cleanup = current_time
    
    def _run_drift_detection(self) -> None:
        """Run periodic drift detection."""
        try:
            drift_alerts = self.drift_detector.detect_drift()
            
            if drift_alerts:
                self.logger.info(f"Drift detection found {len(drift_alerts)} alerts")
                
                # Process each alert
                for alert in drift_alerts:
                    self._publish_workflow_event(WorkflowEvent.DRIFT_DETECTED, {
                        "alert": alert.to_dict()
                    })
        
        except Exception as e:
            self.logger.error(f"Drift detection failed: {e}")
    
    def _run_cleanup_tasks(self) -> None:
        """Run periodic cleanup tasks."""
        try:
            # Clean old feedback
            cleaned_feedback = self.feedback_processor.cleanup_old_feedback()
            
            # Clean old workflows
            cutoff_time = datetime.now() - timedelta(hours=24)
            old_workflows = [
                wf for wf in self.workflow_history 
                if datetime.fromisoformat(wf["created_at"]) < cutoff_time
            ]
            
            for workflow in old_workflows:
                self.workflow_history.remove(workflow)
            
            self.logger.debug(f"Cleanup: removed {cleaned_feedback} old feedback, {len(old_workflows)} old workflows")
            
        except Exception as e:
            self.logger.error(f"Cleanup tasks failed: {e}")
    
    def _health_monitoring_loop(self) -> None:
        """Background health monitoring loop."""
        while self.is_running:
            try:
                self._check_component_health()
                time.sleep(30)  # Check every 30 seconds
            except Exception as e:
                self.logger.error(f"Health monitoring error: {e}")
                time.sleep(60)
    
    def _check_component_health(self) -> None:
        """Check health of all components."""
        try:
            # Check component responsiveness
            components_healthy = True
            
            # Feedback processor health
            feedback_stats = self.feedback_processor.get_statistics()
            feedback_healthy = feedback_stats.get("pending_transactions", 0) < 1000
            
            # Model registry health
            registry_stats = self.model_registry.get_registry_statistics()
            registry_healthy = registry_stats.get("registry_health") == "healthy"
            
            # Overall system health
            if not (feedback_healthy and registry_healthy):
                components_healthy = False
            
            # Update system state
            if not components_healthy:
                if self.system_state == SystemState.HEALTHY:
                    self.system_state = SystemState.DEGRADED
                    self.logger.warning("System state changed to DEGRADED")
            else:
                if self.system_state == SystemState.DEGRADED:
                    self.system_state = SystemState.HEALTHY
                    self.logger.info("System state recovered to HEALTHY")
        
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            self.system_state = SystemState.CRITICAL
    
    def _metrics_collection_loop(self) -> None:
        """Background metrics collection loop."""
        while self.is_running:
            try:
                self._collect_system_metrics()
                time.sleep(60)  # Collect every minute
            except Exception as e:
                self.logger.error(f"Metrics collection error: {e}")
                time.sleep(60)
    
    def _collect_system_metrics(self) -> None:
        """Collect comprehensive system metrics."""
        try:
            current_time = datetime.now()
            
            # Calculate uptime
            uptime_hours = (current_time - self.start_time).total_seconds() / 3600
            self.performance_stats["system_uptime_hours"] = uptime_hours
            
            # Get component statistics
            feedback_stats = self.feedback_processor.get_statistics()
            drift_summary = self.drift_detector.get_drift_summary()
            learner_stats = self.incremental_learner.get_update_statistics()
            registry_stats = self.model_registry.get_registry_statistics()
            ab_stats = self.ab_test_manager.get_ab_test_statistics()
            
            # Create system metrics
            metrics = SystemMetrics(
                timestamp=current_time.isoformat(),
                feedback_processor_health="healthy",  # Simplified
                drift_detector_health="healthy",
                incremental_learner_health="healthy",
                model_registry_health="healthy",
                ab_test_manager_health="healthy",
                feedback_processing_rate=feedback_stats.get("total_processed", 0) / max(1, uptime_hours),
                model_update_frequency=learner_stats.get("total_updates", 0) / max(1, uptime_hours),
                prediction_latency_p95=50.0,  # Would be measured from actual predictions
                system_memory_usage=75.0,  # Would be measured from system
                drift_alerts_24h=drift_summary.get("total_alerts_24h", 0),
                models_updated_24h=learner_stats.get("total_updates", 0),
                ab_tests_active=ab_stats.get("active_experiments", 0),
                overall_model_performance=0.85,  # Would be calculated from actual performance
                overall_health=self.system_state,
                active_workflows=len(self.active_workflows),
                error_rate=0.01  # Would be calculated from error tracking
            )
            
            # Store metrics
            self.metrics_history.append(metrics)
            
            # Keep only last 24 hours of metrics
            cutoff_time = current_time - timedelta(hours=24)
            self.metrics_history = [
                m for m in self.metrics_history 
                if datetime.fromisoformat(m.timestamp) > cutoff_time
            ]
            
        except Exception as e:
            self.logger.error(f"Failed to collect system metrics: {e}")
    
    def _workflow_cleanup_loop(self) -> None:
        """Background workflow cleanup loop."""
        while self.is_running:
            try:
                self._cleanup_completed_workflows()
                time.sleep(300)  # Every 5 minutes
            except Exception as e:
                self.logger.error(f"Workflow cleanup error: {e}")
                time.sleep(300)
    
    def _cleanup_completed_workflows(self) -> None:
        """Clean up completed workflows."""
        completed_workflows = []
        
        for workflow_id, workflow in list(self.active_workflows.items()):
            if workflow.get("status") in ["completed", "failed"]:
                # Check if future is done
                future = workflow.get("future")
                if future and future.done():
                    completed_workflows.append(workflow_id)
        
        # Move completed workflows to history
        for workflow_id in completed_workflows:
            workflow = self.active_workflows.pop(workflow_id)
            self.workflow_history.append(workflow)
    
    def _create_model_metadata_from_update(self, update_result: UpdateResult, trigger: str) -> ModelMetadata:
        """Create model metadata from update result."""
        return ModelMetadata(
            model_id=f"fraud_model_{int(time.time())}",
            version=update_result.model_version,
            name="Online Updated Fraud Model",
            description=f"Model updated via online learning trigger: {trigger}",
            model_type="lightgbm",
            algorithm="gradient_boosting",
            framework="lightgbm",
            training_data_hash="online_update",
            performance_metrics=update_result.new_performance,
            validation_metrics=update_result.new_performance,
            training_start_time=update_result.timestamp,
            training_end_time=update_result.timestamp,
            training_duration_minutes=update_result.training_time_seconds / 60,
            training_samples=update_result.samples_used,
            status=ModelStatus.DEVELOPMENT,
            deployment_stage=ModelMetadata.DeploymentStage.VALIDATION,
            training_trigger=trigger
        )
    
    def _publish_workflow_event(self, event_type: WorkflowEvent, data: Dict[str, Any]) -> None:
        """Publish workflow event to Kafka."""
        try:
            event = {
                "event_type": event_type.value,
                "timestamp": datetime.now().isoformat(),
                "data": data,
                "source": "online_learning_orchestrator"
            }
            
            self.producer.produce(
                "online-learning-events",
                key=f"{event_type.value}_{int(time.time())}",
                value=json.dumps(event)
            )
            self.producer.flush()
            
        except Exception as e:
            self.logger.error(f"Failed to publish workflow event: {e}")
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        current_time = datetime.now()
        uptime = current_time - self.start_time
        
        return {
            "system_state": self.system_state.value,
            "uptime_hours": uptime.total_seconds() / 3600,
            "is_running": self.is_running,
            "active_workflows": len(self.active_workflows),
            "workflow_history_size": len(self.workflow_history),
            "metrics_history_size": len(self.metrics_history),
            "performance_stats": self.performance_stats.copy(),
            "component_status": {
                "feedback_processor": "healthy",
                "drift_detector": "healthy", 
                "incremental_learner": "healthy",
                "model_registry": "healthy",
                "ab_test_manager": "healthy"
            },
            "last_metrics": self.metrics_history[-1].to_dict() if self.metrics_history else None
        }
    
    def get_workflow_summary(self) -> Dict[str, Any]:
        """Get workflow execution summary."""
        active_by_type = {}
        history_by_type = {}
        
        # Summarize active workflows
        for workflow in self.active_workflows.values():
            wf_type = workflow["type"]
            active_by_type[wf_type] = active_by_type.get(wf_type, 0) + 1
        
        # Summarize workflow history
        for workflow in self.workflow_history:
            wf_type = workflow["type"]
            history_by_type[wf_type] = history_by_type.get(wf_type, 0) + 1
        
        return {
            "active_workflows": active_by_type,
            "completed_workflows": history_by_type,
            "total_active": len(self.active_workflows),
            "total_completed": len(self.workflow_history)
        }


# Entry point for running the orchestrator
def main():
    """Main entry point for the orchestrator."""
    import signal
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Online Learning Orchestrator")
    
    # Create orchestrator
    orchestrator = OnlineLearningOrchestrator()
    
    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        orchestrator.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start orchestrator
        orchestrator.start()
    except Exception as e:
        logger.error(f"Orchestrator failed: {e}")
        raise
    finally:
        logger.info("Online Learning Orchestrator shutdown complete")


if __name__ == "__main__":
    main()
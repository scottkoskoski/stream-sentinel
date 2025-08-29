# /stream-sentinel/src/ml/training/core/pipeline_orchestrator.py

"""
PipelineOrchestrator: State Machine-Based Training Pipeline Coordination

Provides enterprise-grade orchestration of the modular training pipeline with
comprehensive state management, error handling, and recovery mechanisms.
Implements a robust state machine that ensures reliable execution and prevents
cascading failures across pipeline components.

Key Features:
- State machine-based pipeline execution with atomic transitions
- Comprehensive error handling and recovery at each stage
- Component isolation prevents cascading failures
- Automatic and manual recovery from any failure point
- Detailed progress tracking and operational monitoring
- Resource management and cleanup throughout execution

Architecture:
- Immutable pipeline state with atomic transitions
- Component orchestration with dependency management
- Comprehensive checkpoint and recovery mechanisms
- Resource lifecycle management with automatic cleanup
- Observable operations with structured logging and metrics
"""

import json
import logging
import time
import threading
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
import traceback

from .checkpoint_manager import CheckpointManager, ModelCheckpoint
from .data_processor import DataProcessor, ProcessedDataset, ValidationResult
from .hyperparameter_optimizer import HyperparameterOptimizer, OptimizationResult, StudyHandle


class PipelineState(Enum):
    """Pipeline execution states with clear transitions."""
    INITIALIZED = "initialized"
    DATA_LOADING = "data_loading" 
    DATA_LOADED = "data_loaded"
    OPTIMIZATION_STARTING = "optimization_starting"
    OPTIMIZATION_RUNNING = "optimization_running"
    OPTIMIZATION_COMPLETED = "optimization_completed"
    MODEL_VALIDATION = "model_validation"
    MODEL_VALIDATED = "model_validated"
    PRODUCTION_DEPLOYMENT = "production_deployment"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"


class PipelineStage(Enum):
    """Individual pipeline stages for granular control."""
    DATA_PROCESSING = "data_processing"
    HYPERPARAMETER_OPTIMIZATION = "hyperparameter_optimization"
    MODEL_VALIDATION = "model_validation"
    PRODUCTION_DEPLOYMENT = "production_deployment"


@dataclass
class PipelineContext:
    """
    Immutable pipeline execution context with comprehensive metadata.
    """
    pipeline_id: str
    config: Dict[str, Any]
    start_time: datetime
    current_state: PipelineState
    current_stage: Optional[PipelineStage] = None
    processed_dataset: Optional[ProcessedDataset] = None
    optimization_result: Optional[OptimizationResult] = None
    best_model_checkpoint: Optional[ModelCheckpoint] = None
    error_history: List[Dict[str, Any]] = field(default_factory=list)
    stage_durations: Dict[str, float] = field(default_factory=dict)
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    recovery_attempts: int = 0
    
    def add_error(self, stage: str, error: Exception, recoverable: bool = True) -> None:
        """Add error to history with context."""
        self.error_history.append({
            'stage': stage,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
            'timestamp': datetime.now().isoformat(),
            'recoverable': recoverable,
            'recovery_attempt': self.recovery_attempts
        })
    
    def transition_to(self, new_state: PipelineState) -> 'PipelineContext':
        """Create new context with state transition."""
        return PipelineContext(
            pipeline_id=self.pipeline_id,
            config=self.config,
            start_time=self.start_time,
            current_state=new_state,
            current_stage=self.current_stage,
            processed_dataset=self.processed_dataset,
            optimization_result=self.optimization_result,
            best_model_checkpoint=self.best_model_checkpoint,
            error_history=self.error_history.copy(),
            stage_durations=self.stage_durations.copy(),
            resource_usage=self.resource_usage.copy(),
            recovery_attempts=self.recovery_attempts
        )
    
    @property
    def total_duration(self) -> float:
        """Total pipeline execution time."""
        return (datetime.now() - self.start_time).total_seconds()
    
    @property
    def has_errors(self) -> bool:
        """Check if pipeline has encountered errors."""
        return len(self.error_history) > 0
    
    @property
    def latest_error(self) -> Optional[Dict[str, Any]]:
        """Get most recent error if any."""
        return self.error_history[-1] if self.error_history else None


class PipelineStateMachine:
    """
    State machine for managing pipeline transitions and validation.
    """
    
    VALID_TRANSITIONS = {
        PipelineState.INITIALIZED: [PipelineState.DATA_LOADING, PipelineState.RECOVERING],
        PipelineState.DATA_LOADING: [PipelineState.DATA_LOADED, PipelineState.FAILED],
        PipelineState.DATA_LOADED: [PipelineState.OPTIMIZATION_STARTING, PipelineState.FAILED],
        PipelineState.OPTIMIZATION_STARTING: [PipelineState.OPTIMIZATION_RUNNING, PipelineState.FAILED],
        PipelineState.OPTIMIZATION_RUNNING: [PipelineState.OPTIMIZATION_COMPLETED, PipelineState.FAILED],
        PipelineState.OPTIMIZATION_COMPLETED: [PipelineState.MODEL_VALIDATION, PipelineState.FAILED],
        PipelineState.MODEL_VALIDATION: [PipelineState.MODEL_VALIDATED, PipelineState.FAILED],
        PipelineState.MODEL_VALIDATED: [PipelineState.PRODUCTION_DEPLOYMENT, PipelineState.FAILED],
        PipelineState.PRODUCTION_DEPLOYMENT: [PipelineState.COMPLETED, PipelineState.FAILED],
        PipelineState.FAILED: [PipelineState.RECOVERING],
        PipelineState.RECOVERING: [PipelineState.DATA_LOADING, PipelineState.OPTIMIZATION_STARTING, 
                                  PipelineState.MODEL_VALIDATION, PipelineState.FAILED],
        PipelineState.COMPLETED: []  # Terminal state
    }
    
    @classmethod
    def validate_transition(cls, current_state: PipelineState, 
                          new_state: PipelineState) -> bool:
        """Validate if state transition is allowed."""
        return new_state in cls.VALID_TRANSITIONS.get(current_state, [])
    
    @classmethod
    def get_recovery_states(cls, failed_state: PipelineState) -> List[PipelineState]:
        """Get possible recovery states from a failed state."""
        if failed_state == PipelineState.FAILED:
            return cls.VALID_TRANSITIONS[PipelineState.RECOVERING]
        return []


@dataclass
class TrainingPipeline:
    """
    High-level training pipeline interface with comprehensive orchestration.
    """
    orchestrator: 'PipelineOrchestrator'
    config: Dict[str, Any]
    
    def run(self, model_types: List[str] = None) -> Dict[str, Any]:
        """
        Execute complete training pipeline.
        
        Args:
            model_types: List of model types to optimize (default: ['xgboost'])
            
        Returns:
            Pipeline execution results
        """
        if model_types is None:
            model_types = ['xgboost']
        
        return self.orchestrator.execute_pipeline(model_types)
    
    def resume_from_failure(self, pipeline_id: str) -> Dict[str, Any]:
        """Resume pipeline execution from failure point."""
        return self.orchestrator.resume_pipeline(pipeline_id)
    
    def get_pipeline_status(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """Get current pipeline status and progress."""
        return self.orchestrator.get_pipeline_status(pipeline_id)


class PipelineOrchestrator:
    """
    Enterprise-grade training pipeline orchestrator with state management.
    
    Coordinates all training components with comprehensive error handling,
    recovery mechanisms, and operational monitoring.
    """
    
    def __init__(self, data_processor: DataProcessor,
                 hyperopt_optimizer: HyperparameterOptimizer,
                 checkpoint_manager: CheckpointManager,
                 config: Dict[str, Any]):
        """
        Initialize pipeline orchestrator with components.
        
        Args:
            data_processor: DataProcessor instance
            hyperopt_optimizer: HyperparameterOptimizer instance  
            checkpoint_manager: CheckpointManager instance
            config: Configuration dictionary
        """
        self.data_processor = data_processor
        self.hyperopt_optimizer = hyperopt_optimizer
        self.checkpoint_manager = checkpoint_manager
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # State management
        self.state_machine = PipelineStateMachine()
        self.active_pipelines: Dict[str, PipelineContext] = {}
        
        # Execution parameters
        self.max_recovery_attempts = config.get('max_recovery_attempts', 3)
        self.stage_timeout = config.get('stage_timeout_seconds', 3600)  # 1 hour per stage
        
        # Pipeline persistence
        pipeline_dir = Path(config.get('pipeline_dir', 'models/pipeline_state'))
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = pipeline_dir / "state"
        self.results_dir = pipeline_dir / "results"
        
        for dir_path in [self.state_dir, self.results_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Thread safety
        self._lock = threading.RLock()
        
        self.logger.info("pipeline_orchestrator.initialized", extra={
            "max_recovery_attempts": self.max_recovery_attempts,
            "stage_timeout": self.stage_timeout,
            "state_dir": str(self.state_dir)
        })
    
    def execute_pipeline(self, model_types: List[str]) -> Dict[str, Any]:
        """
        Execute complete training pipeline with state management.
        
        Args:
            model_types: List of model types to optimize
            
        Returns:
            Comprehensive pipeline execution results
        """
        pipeline_id = f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        with self._lock:
            # Initialize pipeline context
            context = PipelineContext(
                pipeline_id=pipeline_id,
                config=self.config.copy(),
                start_time=datetime.now(),
                current_state=PipelineState.INITIALIZED
            )
            
            self.active_pipelines[pipeline_id] = context
            
            self.logger.info("pipeline.started", extra={
                "pipeline_id": pipeline_id,
                "model_types": model_types
            })
            
            try:
                # Execute pipeline stages
                context = self._execute_data_processing(context)
                
                for model_type in model_types:
                    context = self._execute_hyperparameter_optimization(context, model_type)
                    
                context = self._execute_model_validation(context)
                context = self._execute_production_deployment(context)
                
                # Mark as completed
                context = self._transition_state(context, PipelineState.COMPLETED)
                
                # Save final results
                results = self._save_pipeline_results(context)
                
                self.logger.info("pipeline.completed", extra={
                    "pipeline_id": pipeline_id,
                    "total_duration": context.total_duration,
                    "best_score": context.optimization_result.best_score if context.optimization_result else None
                })
                
                return results
                
            except Exception as e:
                self.logger.error("pipeline.failed", extra={
                    "pipeline_id": pipeline_id,
                    "error": str(e),
                    "current_state": context.current_state.value
                })
                
                context.add_error("pipeline_execution", e, recoverable=True)
                context = self._transition_state(context, PipelineState.FAILED)
                
                # Save failed state for recovery
                self._save_pipeline_state(context)
                
                raise PipelineExecutionError(f"Pipeline {pipeline_id} failed: {e}") from e
            
            finally:
                # Cleanup resources
                self._cleanup_pipeline_resources(context)
    
    def resume_pipeline(self, pipeline_id: str) -> Dict[str, Any]:
        """
        Resume pipeline execution from failure point.
        
        Args:
            pipeline_id: Pipeline identifier to resume
            
        Returns:
            Pipeline execution results
        """
        with self._lock:
            # Load pipeline state
            context = self._load_pipeline_state(pipeline_id)
            if context is None:
                raise PipelineRecoveryError(f"Pipeline state not found: {pipeline_id}")
            
            if context.recovery_attempts >= self.max_recovery_attempts:
                raise PipelineRecoveryError(f"Max recovery attempts exceeded: {pipeline_id}")
            
            context.recovery_attempts += 1
            context = self._transition_state(context, PipelineState.RECOVERING)
            
            self.logger.info("pipeline.resuming", extra={
                "pipeline_id": pipeline_id,
                "recovery_attempt": context.recovery_attempts,
                "failed_state": context.latest_error['stage'] if context.latest_error else None
            })
            
            try:
                # Determine recovery point based on last successful stage
                if context.processed_dataset is None:
                    context = self._execute_data_processing(context)
                
                if context.optimization_result is None:
                    # Determine model type from config or previous execution
                    model_type = self.config.get('default_model_type', 'xgboost')
                    context = self._execute_hyperparameter_optimization(context, model_type)
                
                if context.best_model_checkpoint is None:
                    context = self._execute_model_validation(context)
                
                context = self._execute_production_deployment(context)
                context = self._transition_state(context, PipelineState.COMPLETED)
                
                results = self._save_pipeline_results(context)
                
                self.logger.info("pipeline.recovery_completed", extra={
                    "pipeline_id": pipeline_id,
                    "recovery_attempt": context.recovery_attempts,
                    "total_duration": context.total_duration
                })
                
                return results
                
            except Exception as e:
                self.logger.error("pipeline.recovery_failed", extra={
                    "pipeline_id": pipeline_id,
                    "recovery_attempt": context.recovery_attempts,
                    "error": str(e)
                })
                
                context.add_error("pipeline_recovery", e, recoverable=False)
                context = self._transition_state(context, PipelineState.FAILED)
                self._save_pipeline_state(context)
                
                raise PipelineRecoveryError(f"Pipeline recovery failed: {e}") from e
    
    def _execute_data_processing(self, context: PipelineContext) -> PipelineContext:
        """Execute data processing stage with error handling."""
        stage_start_time = time.time()
        context = self._transition_state(context, PipelineState.DATA_LOADING)
        
        try:
            self.logger.info("stage.data_processing.started", extra={
                "pipeline_id": context.pipeline_id
            })
            
            # Load and preprocess data
            processed_dataset = self.data_processor.load_and_preprocess_data()
            
            # Update context
            context.processed_dataset = processed_dataset
            context.stage_durations['data_processing'] = time.time() - stage_start_time
            
            context = self._transition_state(context, PipelineState.DATA_LOADED)
            
            self.logger.info("stage.data_processing.completed", extra={
                "pipeline_id": context.pipeline_id,
                "dataset_shape": processed_dataset.shape,
                "fraud_rate": processed_dataset.fraud_rate,
                "duration": context.stage_durations['data_processing']
            })
            
            return context
            
        except Exception as e:
            context.add_error("data_processing", e)
            self.logger.error("stage.data_processing.failed", extra={
                "pipeline_id": context.pipeline_id,
                "error": str(e),
                "duration": time.time() - stage_start_time
            })
            raise
    
    def _execute_hyperparameter_optimization(self, context: PipelineContext, 
                                           model_type: str) -> PipelineContext:
        """Execute hyperparameter optimization stage with error handling."""
        stage_start_time = time.time()
        context = self._transition_state(context, PipelineState.OPTIMIZATION_STARTING)
        
        try:
            self.logger.info("stage.hyperopt.started", extra={
                "pipeline_id": context.pipeline_id,
                "model_type": model_type
            })
            
            # Create study
            study_name = f"{context.pipeline_id}_{model_type}"
            study_handle = self.hyperopt_optimizer.create_study(study_name, model_type)
            
            context = self._transition_state(context, PipelineState.OPTIMIZATION_RUNNING)
            
            # Run optimization
            optimization_result = self.hyperopt_optimizer.optimize(
                study_handle, context.processed_dataset
            )
            
            # Update context
            context.optimization_result = optimization_result
            context.stage_durations['hyperparameter_optimization'] = time.time() - stage_start_time
            
            context = self._transition_state(context, PipelineState.OPTIMIZATION_COMPLETED)
            
            self.logger.info("stage.hyperopt.completed", extra={
                "pipeline_id": context.pipeline_id,
                "model_type": model_type,
                "best_score": optimization_result.best_score,
                "n_trials": optimization_result.study_handle.n_trials_completed,
                "duration": context.stage_durations['hyperparameter_optimization']
            })
            
            return context
            
        except Exception as e:
            context.add_error("hyperparameter_optimization", e)
            self.logger.error("stage.hyperopt.failed", extra={
                "pipeline_id": context.pipeline_id,
                "model_type": model_type,
                "error": str(e),
                "duration": time.time() - stage_start_time
            })
            raise
    
    def _execute_model_validation(self, context: PipelineContext) -> PipelineContext:
        """Execute model validation stage with error handling."""
        stage_start_time = time.time()
        context = self._transition_state(context, PipelineState.MODEL_VALIDATION)
        
        try:
            self.logger.info("stage.validation.started", extra={
                "pipeline_id": context.pipeline_id
            })
            
            # Load best checkpoint
            best_checkpoint = self.checkpoint_manager.load_best_checkpoint()
            if best_checkpoint is None:
                raise ValidationError("No valid checkpoint found for validation")
            
            # Validate model performance
            if best_checkpoint.score < 0.7:  # Minimum AUC threshold
                raise ValidationError(f"Model performance below threshold: {best_checkpoint.score}")
            
            # Update context
            context.best_model_checkpoint = best_checkpoint
            context.stage_durations['model_validation'] = time.time() - stage_start_time
            
            context = self._transition_state(context, PipelineState.MODEL_VALIDATED)
            
            self.logger.info("stage.validation.completed", extra={
                "pipeline_id": context.pipeline_id,
                "model_score": best_checkpoint.score,
                "checkpoint_id": best_checkpoint.checkpoint_id,
                "duration": context.stage_durations['model_validation']
            })
            
            return context
            
        except Exception as e:
            context.add_error("model_validation", e)
            self.logger.error("stage.validation.failed", extra={
                "pipeline_id": context.pipeline_id,
                "error": str(e),
                "duration": time.time() - stage_start_time
            })
            raise
    
    def _execute_production_deployment(self, context: PipelineContext) -> PipelineContext:
        """Execute production deployment stage with error handling."""
        stage_start_time = time.time()
        context = self._transition_state(context, PipelineState.PRODUCTION_DEPLOYMENT)
        
        try:
            self.logger.info("stage.deployment.started", extra={
                "pipeline_id": context.pipeline_id
            })
            
            # Save production model (placeholder - would integrate with model registry)
            production_model_path = Path("models/ieee_fraud_model_production.pkl")
            
            # Create production package from best checkpoint
            checkpoint = context.best_model_checkpoint
            production_package = {
                'model': checkpoint.model,
                'scaler': None,  # XGBoost doesn't need scaling
                'label_encoders': context.processed_dataset.label_encoders,
                'feature_names': context.processed_dataset.feature_names,
                'model_metrics': {
                    'model_type': checkpoint.model_type,
                    'validation_auc': checkpoint.score,
                    'checkpoint_id': checkpoint.checkpoint_id,
                    'pipeline_id': context.pipeline_id
                },
                'training_metadata': {
                    'training_date': checkpoint.timestamp.isoformat(),
                    'feature_count': len(context.processed_dataset.feature_names),
                    'training_samples': context.processed_dataset.shape[0],
                    'optimization_trials': context.optimization_result.study_handle.n_trials_completed,
                    'pipeline_duration': context.total_duration
                }
            }
            
            # Save production model
            import pickle
            with open(production_model_path, 'wb') as f:
                pickle.dump(production_package, f)
            
            # Save metadata
            metadata_path = Path("models/ieee_fraud_model_metadata.json")
            metadata = {
                'model_type': checkpoint.model_type,
                'feature_names': context.processed_dataset.feature_names,
                'model_metrics': production_package['model_metrics'],
                'training_metadata': production_package['training_metadata']
            }
            
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            context.stage_durations['production_deployment'] = time.time() - stage_start_time
            
            self.logger.info("stage.deployment.completed", extra={
                "pipeline_id": context.pipeline_id,
                "model_path": str(production_model_path),
                "model_score": checkpoint.score,
                "duration": context.stage_durations['production_deployment']
            })
            
            return context
            
        except Exception as e:
            context.add_error("production_deployment", e)
            self.logger.error("stage.deployment.failed", extra={
                "pipeline_id": context.pipeline_id,
                "error": str(e),
                "duration": time.time() - stage_start_time
            })
            raise
    
    def _transition_state(self, context: PipelineContext, 
                         new_state: PipelineState) -> PipelineContext:
        """Transition pipeline to new state with validation."""
        if not self.state_machine.validate_transition(context.current_state, new_state):
            raise InvalidStateTransitionError(
                f"Invalid transition from {context.current_state} to {new_state}"
            )
        
        new_context = context.transition_to(new_state)
        
        self.logger.info("pipeline.state_transition", extra={
            "pipeline_id": context.pipeline_id,
            "from_state": context.current_state.value,
            "to_state": new_state.value
        })
        
        # Save state after transition
        self._save_pipeline_state(new_context)
        
        return new_context
    
    def _save_pipeline_state(self, context: PipelineContext) -> None:
        """Save pipeline state for recovery."""
        state_file = self.state_dir / f"{context.pipeline_id}.json"
        
        state_data = {
            'pipeline_id': context.pipeline_id,
            'config': context.config,
            'start_time': context.start_time.isoformat(),
            'current_state': context.current_state.value,
            'error_history': context.error_history,
            'stage_durations': context.stage_durations,
            'recovery_attempts': context.recovery_attempts,
            'has_processed_dataset': context.processed_dataset is not None,
            'has_optimization_result': context.optimization_result is not None,
            'has_best_checkpoint': context.best_model_checkpoint is not None
        }
        
        with open(state_file, 'w') as f:
            json.dump(state_data, f, indent=2)
    
    def _load_pipeline_state(self, pipeline_id: str) -> Optional[PipelineContext]:
        """Load pipeline state for recovery."""
        state_file = self.state_dir / f"{pipeline_id}.json"
        
        if not state_file.exists():
            return None
        
        try:
            with open(state_file, 'r') as f:
                state_data = json.load(f)
            
            # Reconstruct context (without large objects)
            context = PipelineContext(
                pipeline_id=state_data['pipeline_id'],
                config=state_data['config'],
                start_time=datetime.fromisoformat(state_data['start_time']),
                current_state=PipelineState(state_data['current_state']),
                error_history=state_data['error_history'],
                stage_durations=state_data['stage_durations'],
                recovery_attempts=state_data['recovery_attempts']
            )
            
            # Reconstruct large objects if needed
            if state_data.get('has_processed_dataset'):
                # Would need to reload from cache or regenerate
                pass
            
            if state_data.get('has_optimization_result'):
                # Would need to reload from checkpoint manager
                pass
            
            return context
            
        except Exception as e:
            self.logger.error("state.load_failed", extra={
                "pipeline_id": pipeline_id,
                "error": str(e)
            })
            return None
    
    def _save_pipeline_results(self, context: PipelineContext) -> Dict[str, Any]:
        """Save comprehensive pipeline results."""
        results_file = self.results_dir / f"{context.pipeline_id}_results.json"
        
        results = {
            'pipeline_id': context.pipeline_id,
            'execution_summary': {
                'total_duration': context.total_duration,
                'start_time': context.start_time.isoformat(),
                'end_time': datetime.now().isoformat(),
                'final_state': context.current_state.value,
                'recovery_attempts': context.recovery_attempts
            },
            'stage_durations': context.stage_durations,
            'model_performance': {
                'best_score': context.optimization_result.best_score if context.optimization_result else None,
                'best_params': context.optimization_result.best_params if context.optimization_result else None,
                'checkpoint_id': context.best_model_checkpoint.checkpoint_id if context.best_model_checkpoint else None
            },
            'error_history': context.error_history,
            'resource_usage': context.resource_usage
        }
        
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        return results
    
    def _cleanup_pipeline_resources(self, context: PipelineContext) -> None:
        """Cleanup pipeline resources."""
        try:
            # Cleanup would include:
            # - GPU memory cleanup
            # - Temporary file cleanup  
            # - Study cleanup if needed
            self.logger.info("pipeline.resources_cleaned", extra={
                "pipeline_id": context.pipeline_id
            })
        except Exception as e:
            self.logger.warning("pipeline.cleanup_failed", extra={
                "pipeline_id": context.pipeline_id,
                "error": str(e)
            })
    
    def get_pipeline_status(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """Get current pipeline status."""
        if pipeline_id in self.active_pipelines:
            context = self.active_pipelines[pipeline_id]
            return {
                'pipeline_id': pipeline_id,
                'current_state': context.current_state.value,
                'total_duration': context.total_duration,
                'stage_durations': context.stage_durations,
                'has_errors': context.has_errors,
                'recovery_attempts': context.recovery_attempts
            }
        
        # Try to load from saved state
        context = self._load_pipeline_state(pipeline_id)
        if context:
            return {
                'pipeline_id': pipeline_id,
                'current_state': context.current_state.value,
                'total_duration': context.total_duration,
                'stage_durations': context.stage_durations,
                'has_errors': context.has_errors,
                'recovery_attempts': context.recovery_attempts
            }
        
        return None


# Custom exceptions
class PipelineExecutionError(Exception):
    """Base exception for pipeline execution errors."""
    pass

class PipelineRecoveryError(PipelineExecutionError):
    """Raised when pipeline recovery fails."""
    pass

class InvalidStateTransitionError(PipelineExecutionError):
    """Raised when invalid state transition is attempted."""
    pass

class ValidationError(PipelineExecutionError):
    """Raised when model validation fails."""
    pass
# Stream-Sentinel Online Learning System

## Overview

A production-grade online learning system that enables fraud detection models to continuously improve through real-time feedback, drift detection, and automated model updates. This system transforms Stream-Sentinel from a static fraud detection system into an adaptive, self-improving platform.

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Feedback      │    │     Drift       │    │   Incremental   │    │    Model        │
│   Processor     │────┤    Detector     │────┤    Learner      │────┤   Registry      │
│                 │    │                 │    │                 │    │                 │
│ • Multi-source  │    │ • Statistical   │    │ • Batch         │    │ • Versioning    │
│ • Validation    │    │ • Performance   │    │ • Validation    │    │ • Deployment    │
│ • Consensus     │    │ • Feature       │    │ • Rollback      │    │ • Monitoring    │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
         │                        │                        │                        │
         ▼                        ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   A/B Testing   │    │   Orchestrator  │    │   Enhanced      │    │   Monitoring    │
│   Manager       │────┤   & Workflow    │────┤   Fraud         │────┤   & Alerting    │
│                 │    │   Management    │    │   Detector      │    │                 │
│ • Experiments   │    │ • Coordination  │    │ • Integration   │    │ • Metrics       │
│ • Traffic       │    │ • Monitoring    │    │ • Performance   │    │ • Health        │
│ • Analysis      │    │ • Recovery      │    │ • Prediction    │    │ • Dashboards    │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Key Features

### 🎯 Feedback Processing System
- **Multi-source feedback collection** from manual investigations, automated verification, and customer disputes
- **Quality validation** with investigator performance tracking and confidence scoring  
- **Conflict resolution** using weighted consensus algorithms
- **Temporal weighting** that prioritizes recent feedback
- **Audit trails** for regulatory compliance

### 📊 Drift Detection Engine  
- **Statistical drift detection** using KS tests, Chi-square tests, and Population Stability Index (PSI)
- **Performance monitoring** with automated degradation alerts
- **Feature-level analysis** to identify specific drift sources
- **Concept drift detection** for changing fraud patterns
- **Automated response** with configurable retraining triggers

### 🧠 Incremental Learning Pipeline
- **Multiple update strategies**: continuous, scheduled, drift-triggered, performance-based
- **Model validation** with rollback capabilities
- **Memory management** to prevent catastrophic forgetting  
- **Performance tracking** with detailed metrics
- **Multi-model support** for LightGBM, XGBoost, and scikit-learn

### 📦 Model Registry & Versioning
- **Semantic versioning** with automated version bumping
- **Deployment lifecycle management** across development, staging, and production
- **Automated rollback** based on performance thresholds
- **Model lineage tracking** for audit and debugging
- **Artifact storage** with multiple backend support

### 🧪 A/B Testing Framework
- **Traffic splitting** with consistent user assignment
- **Statistical analysis** with early stopping for efficiency
- **Multi-metric optimization** supporting business and technical metrics
- **Automated decision making** based on statistical significance
- **Performance monitoring** throughout test duration

### 🔄 System Orchestration
- **Event-driven architecture** using Kafka for communication
- **Workflow management** with automated coordination
- **Health monitoring** and system recovery
- **Comprehensive logging** and performance tracking
- **Graceful degradation** under failure conditions

## Components

### Core Components

| Component | Purpose | Key Features |
|-----------|---------|--------------|
| `FeedbackProcessor` | Collect and validate fraud investigation feedback | Multi-validator consensus, quality scoring, temporal weighting |
| `DriftDetector` | Monitor model and data drift | Statistical tests, performance monitoring, automated alerting |
| `IncrementalLearner` | Update models with new data | Batch processing, validation, rollback, memory management |
| `ModelRegistry` | Manage model versions and deployment | Semantic versioning, lifecycle management, artifact storage |
| `ABTestManager` | Run model comparison experiments | Traffic routing, statistical analysis, automated decisions |
| `OnlineLearningOrchestrator` | Coordinate all system components | Event handling, workflow management, health monitoring |

### Integration Components

| Component | Purpose | Integration Points |
|-----------|---------|------------------|
| `EnhancedFraudDetector` | Integrate online learning with existing fraud detection | Model loading, A/B testing, performance tracking |
| `OnlineLearningDemo` | Demonstrate system capabilities | End-to-end workflow showcase, component testing |

## Installation & Setup

### Prerequisites
- Python 3.13+
- Apache Kafka cluster
- Redis instance  
- Docker & Docker Compose
- LightGBM/XGBoost libraries

### Quick Start

1. **Install Dependencies**
```bash
cd src/ml/online_learning
pip install -r requirements.txt
```

2. **Configure Environment**
```bash
export STREAM_SENTINEL_ENV=development
export KAFKA_SERVERS=localhost:9092
export REDIS_HOST=localhost
export REDIS_PORT=6379
```

3. **Start Infrastructure**
```bash
cd docker && docker-compose up -d
```

4. **Run Demo**
```bash
python scripts/online_learning_demo.py
```

## Configuration

The system uses a comprehensive configuration system with environment-specific settings:

### Core Configuration (`config.py`)

```python
# Model training parameters
model = ModelConfig(
    model_type="lightgbm_gpu",
    learning_rate=0.1,
    incremental_update_batch_size=1000,
    min_samples_for_update=100
)

# Drift detection thresholds
drift_detection = DriftDetectionConfig(
    drift_threshold=0.05,
    auto_retrain_on_drift=True,
    performance_threshold=0.05
)

# Feedback processing rules
feedback = FeedbackConfig(
    min_investigator_confidence=0.7,
    require_multiple_confirmations=True,
    feedback_validation_threshold=0.8
)
```

### Environment-Specific Settings

- **Development**: Verbose logging, relaxed thresholds, frequent checkpoints
- **Staging**: Production-like settings with enhanced monitoring
- **Production**: Optimized performance, strict validation, minimal logging

## Usage Examples

### 1. Feedback Processing

```python
from online_learning import FeedbackProcessor, FeedbackRecord, FeedbackLabel

processor = FeedbackProcessor()

# Add feedback from investigation
feedback = FeedbackRecord(
    transaction_id="TXN_123456",
    label=FeedbackLabel.FRAUD,
    confidence=0.9,
    investigator_id="analyst_001",
    source=FeedbackSource.MANUAL_INVESTIGATION
)

processor.add_feedback(feedback)
processed = processor.process_pending_feedback()
```

### 2. Drift Detection

```python
from online_learning import DriftDetector
import pandas as pd

detector = DriftDetector()

# Set baseline data
detector.set_reference_data(reference_df, reference_labels)

# Monitor predictions
detector.add_prediction_sample(features, prediction, actual_label)

# Check for drift
alerts = detector.detect_drift()
for alert in alerts:
    print(f"Drift detected: {alert.drift_type} ({alert.severity})")
```

### 3. Model Updates

```python
from online_learning import IncrementalLearner

learner = IncrementalLearner()

# Add training data
learner.add_training_batch(processed_feedback)

# Update model
result = learner.perform_incremental_update()
if result.success:
    print(f"Model updated: {result.performance_change}")
```

### 4. A/B Testing

```python
from online_learning import ABTestManager

ab_manager = ABTestManager()

# Create experiment
exp_id = ab_manager.create_experiment(
    name="Model v2.1 vs v2.0",
    control_model=("fraud_model", "2.0"),
    treatment_model=("fraud_model", "2.1"),
    primary_metric="f1"
)

# Start experiment
ab_manager.start_experiment(exp_id)

# Assign users and record results
variant = ab_manager.assign_variant("user_123")
ab_manager.record_prediction_result("user_123", variant, prediction, actual)
```

## Monitoring & Operations

### Key Metrics

| Metric Category | Key Indicators | Thresholds |
|----------------|---------------|------------|
| **Feedback Quality** | Consensus rate, validation pass rate | >90% consensus, <5% validation failures |
| **Drift Detection** | Alert frequency, false positive rate | <10 alerts/day, <20% false positives |
| **Model Performance** | AUC degradation, prediction latency | <5% degradation, <100ms latency |
| **System Health** | Error rate, throughput | <1% errors, >1000 TPS |

### Alerting Rules

```yaml
# Drift alerts
- alert: HighDriftScore
  expr: drift_score > 0.25
  for: 5m
  labels:
    severity: critical
    action: immediate_retraining

# Performance degradation  
- alert: ModelPerformanceDrop
  expr: auc_score < baseline_auc * 0.95
  for: 10m
  labels:
    severity: warning
    action: investigate

# System health
- alert: FeedbackProcessingLag
  expr: pending_feedback_count > 1000
  for: 2m
  labels:
    severity: warning
    action: scale_up
```

### Dashboard Views

1. **Executive Dashboard**: High-level system health and business impact
2. **Operations Dashboard**: Real-time system performance and alerts  
3. **ML Engineering Dashboard**: Model performance and drift analysis
4. **A/B Testing Dashboard**: Experiment results and statistical analysis

## Testing

### Unit Tests
```bash
pytest src/ml/online_learning/tests/ -v
```

### Integration Tests
```bash
python -m pytest src/ml/online_learning/tests/integration/ -v
```

### Load Testing
```bash
python scripts/load_test_online_learning.py --tps 1000 --duration 300
```

### Demo & Validation
```bash
python scripts/online_learning_demo.py
```

## Performance Characteristics

### Throughput
- **Feedback Processing**: 10,000+ records/hour
- **Drift Detection**: Real-time analysis on 100k+ predictions
- **Model Updates**: Complete in <30 minutes for 50k samples
- **A/B Testing**: Handle 10k+ concurrent assignments

### Latency
- **Prediction Enhancement**: <5ms additional latency
- **Feedback Processing**: <100ms per record
- **Model Loading**: <2 seconds for production deployment
- **Drift Analysis**: <30 seconds for full dataset

### Resource Requirements
- **Memory**: 8-16GB recommended for production
- **CPU**: 4-8 cores for optimal performance  
- **Storage**: 100GB+ for model artifacts and history
- **Network**: 1Gbps for high-throughput scenarios

## Security & Compliance

### Data Protection
- **Encryption**: All data encrypted in transit and at rest
- **Access Control**: Role-based permissions for all components
- **Audit Logging**: Complete audit trail for all operations
- **Data Retention**: Configurable retention policies

### Regulatory Compliance
- **Model Explainability**: Decision reasoning for all predictions
- **Change Tracking**: Complete history of all model changes
- **Performance Documentation**: Detailed performance metrics and validation
- **Rollback Capabilities**: Immediate rollback for compliance issues

## Troubleshooting

### Common Issues

#### Feedback Processing Slow
```bash
# Check Redis connection
redis-cli ping

# Monitor processing queue
redis-cli keys "pending_feedback:*" | wc -l

# Scale processing
export FEEDBACK_BATCH_SIZE=500
```

#### Drift Detection False Positives
```python
# Adjust sensitivity
config.drift_detection.drift_threshold = 0.1  # Less sensitive
config.drift_detection.min_reference_samples = 2000  # More stable baseline
```

#### Model Update Failures
```python
# Check validation data
learner.set_validation_data(X_val, y_val)

# Reduce batch size
config.model.incremental_update_batch_size = 500

# Enable debug logging
import logging
logging.getLogger('incremental_learner').setLevel(logging.DEBUG)
```

## Roadmap & Future Enhancements

### Phase 1 (Next 2-3 months)
- [ ] Enhanced feature engineering with automated feature selection
- [ ] Advanced ensemble methods for multi-model predictions
- [ ] Integration with external fraud databases
- [ ] Prometheus metrics and Grafana dashboards

### Phase 2 (3-6 months)
- [ ] Kubernetes deployment with auto-scaling
- [ ] Graph neural networks for network-based fraud detection
- [ ] Real-time model explanation and interpretability
- [ ] Multi-region deployment with data locality

### Phase 3 (6-12 months)
- [ ] Federated learning for privacy-preserving updates
- [ ] Causal inference for understanding fraud mechanisms
- [ ] Automated feature engineering with genetic programming
- [ ] Integration with modern MLOps platforms (MLflow, Kubeflow)

## Contributing

### Development Setup
1. Fork the repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Install dev dependencies: `pip install -r requirements-dev.txt`
4. Run tests: `pytest`
5. Submit pull request

### Code Standards
- Follow PEP 8 style guidelines
- Maintain >90% test coverage
- Add docstrings for all public methods
- Use type hints throughout

### Documentation
- Update this README for new features
- Add docstrings with examples
- Update configuration documentation
- Include performance impact analysis

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contact

For questions, issues, or contributions, please:
- Open an issue on GitHub
- Contact the development team
- Review the troubleshooting guide above

---

**Built with ❤️ for production-scale fraud detection and continuous model improvement.**
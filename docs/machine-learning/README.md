# Machine Learning & Online Learning Pipeline

*This guide covers the comprehensive machine learning system in Stream-Sentinel, including traditional model training, advanced online learning, and production MLOps.*

## ML System Architecture

Stream-Sentinel implements a complete MLOps pipeline with both traditional batch training and advanced online learning capabilities:

```
                    Complete ML Pipeline Architecture

┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Data Sources  │    │  Model Training │    │  Model Serving  │    │ Online Learning │
│                 │    │                 │    │                 │    │                 │
│ • IEEE-CIS      │    │ • LightGBM      │    │ • Real-time     │    │ • Feedback      │
│   Dataset       ├────┤   Training      ├────┤   Inference     ├────┤   Processing    │
│ • Synthetic     │    │ • Feature Eng   │    │ • A/B Testing   │    │ • Drift Monitor │
│   Generation    │    │ • Validation    │    │ • Performance   │    │ • Model Updates │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 🧠 Core ML Components

### 1. Model Training Pipeline (`src/ml/ieee_model_trainer.py`)

**IEEE-CIS Dataset Analysis & Training:**
- **Dataset**: 590,540+ transactions with 394 features
- **Model**: LightGBM with GPU acceleration
- **Performance**: 83.6% test AUC, 88.5% validation AUC
- **Features**: 200+ engineered features with automated selection
- **Training Time**: <1 minute with GPU optimization

**Key Features:**
- Automated hyperparameter tuning with Optuna
- GPU-accelerated training and inference
- Comprehensive feature engineering pipeline
- Model validation and performance analysis
- Production model serialization and metadata

### 2. Feature Engineering System

**Real-time Feature Pipeline:**
- **Behavioral Features**: User spending patterns, velocity analysis
- **Temporal Features**: Time-of-day patterns, transaction frequency
- **Contextual Features**: Device, location, and session information
- **Derived Features**: Ratios, percentiles, and anomaly scores

**Advanced Feature Engineering:**
```python
# Real-time feature calculation
features = {
    'amount_vs_avg_ratio': amount / user_avg,
    'velocity_score': daily_count / elapsed_hours,
    'temporal_anomaly': hour_risk_scores[hour],
    'amount_percentile': calculate_percentile(amount, user_history)
}
```

## Advanced Online Learning System

### 3. Online Learning Pipeline (`src/ml/online_learning/`)

**Complete MLOps Infrastructure:**

#### **Feedback Processing** (`feedback_processor.py`)
- **Multi-source Collection**: Manual investigations, automated verification, customer disputes
- **Quality Validation**: Investigator performance tracking, confidence scoring
- **Conflict Resolution**: Weighted consensus algorithms for multi-validator feedback
- **Temporal Weighting**: Recent feedback prioritized for model updates

#### **Drift Detection** (`drift_detector.py`)
- **Statistical Tests**: Kolmogorov-Smirnov, Chi-square, Population Stability Index
- **Performance Monitoring**: AUC degradation, precision/recall tracking
- **Feature-level Analysis**: Individual feature distribution monitoring
- **Automated Alerting**: Configurable thresholds with response triggers

#### **Incremental Learning** (`incremental_learner.py`)
- **Multiple Strategies**: Continuous, scheduled, drift-triggered, performance-based updates
- **Model Validation**: Performance testing before deployment
- **Rollback Capabilities**: Automatic reversion on validation failures
- **Memory Management**: Prevents catastrophic forgetting with historical data

#### **Model Registry** (`model_registry.py`)
- **Semantic Versioning**: Automated version bumping based on change type
- **Deployment Lifecycle**: Development → Staging → Production pipeline
- **Artifact Management**: Model storage with multiple backend support
- **Audit Trails**: Complete lineage tracking for compliance

#### **A/B Testing Framework** (`ab_test_manager.py`)
- **Traffic Routing**: Consistent user assignment with configurable splits
- **Statistical Analysis**: Significance testing with early stopping
- **Performance Monitoring**: Real-time experiment tracking
- **Automated Decisions**: Winner selection based on statistical criteria

### 4. Enhanced Fraud Detector (`src/consumers/enhanced_fraud_detector.py`)

**Production Integration:**
- **Dynamic Model Loading**: Automatic updates from model registry
- **A/B Test Support**: User assignment and result tracking
- **Performance Monitoring**: Real-time metrics with drift indicators
- **Backward Compatibility**: Seamless integration with existing pipeline

## Performance Metrics & Monitoring

### Model Performance
- **Accuracy**: 85%+ AUC with continuous improvement
- **Latency**: <100ms prediction time including feature engineering
- **Throughput**: 10k+ predictions per second
- **Reliability**: 99.9% uptime with graceful degradation

### Online Learning Performance
- **Model Updates**: Complete in <30 minutes for 50k samples
- **Drift Detection**: Real-time analysis on 100k+ predictions
- **Feedback Processing**: 10k+ records per hour with validation
- **A/B Testing**: Handle 10k+ concurrent user assignments

## Implementation Guides

### Model Training
```bash
# Train new model with IEEE-CIS data
python src/ml/ieee_model_trainer.py

# View model performance
cat models/ieee_fraud_model_metadata.json
```

### Online Learning Demo
```bash
# Run comprehensive demo
python scripts/online_learning_demo.py

# Start enhanced fraud detector
python src/consumers/enhanced_fraud_detector.py
```

### System Integration
```bash
# Start online learning orchestrator
python src/ml/online_learning/online_learning_orchestrator.py

# Monitor system performance
python -c "
from src.ml.online_learning import get_online_learning_config
config = get_online_learning_config()
print('Online learning system ready')
"
```

## Advanced Features

### Drift Detection & Response
- **Multi-dimensional Monitoring**: Data, concept, performance, and feature drift
- **Automated Response**: Model retraining triggered by drift detection
- **Statistical Rigor**: Multiple tests with configurable significance levels
- **Business Impact Analysis**: Cost-benefit analysis of model updates

### Model Lifecycle Management
- **Continuous Deployment**: Automated testing and deployment pipeline
- **Canary Releases**: Gradual rollout with performance monitoring
- **Rollback Automation**: Instant reversion on performance degradation
- **Compliance Integration**: Audit trails and regulatory reporting

### Production Optimization
- **Memory Efficiency**: Optimized data structures and garbage collection
- **CPU/GPU Utilization**: Balanced workload distribution
- **Network Optimization**: Compressed communication and caching
- **Resource Scaling**: Dynamic allocation based on workload

## Related Documentation

- **[Online Learning System](../../src/ml/online_learning/README.md)** - Detailed technical documentation
- **[Fraud Detection Guide](../fraud-detection/README.md)** - Integration with fraud detection pipeline
- **[Project Logs](../project-logs/004-ml-fraud-detection.md)** - Implementation journey
- **[Infrastructure Guide](../infrastructure/README.md)** - Supporting infrastructure

---

**Navigation:** [← Documentation Index](../README.md) | [Online Learning System →](../../src/ml/online_learning/README.md)
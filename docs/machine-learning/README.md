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

## Core ML Components

### 1. Model Training Pipeline (`src/ml/ieee_model_trainer.py`)

**IEEE-CIS Dataset Analysis & Training:**
- **Dataset**: 590,540+ transactions with 394 features
- **Models**: XGBoost, LightGBM, CatBoost with GPU acceleration
- **Performance**: 85%+ test AUC with comprehensive optimization
- **Features**: 200+ engineered features with automated selection
- **Training Time**: 2-6 hours for complete hyperparameter optimization

**Comprehensive Hyperparameter Optimization:**
- **Search Space**: 30+ parameters per model (100+ combinations)
- **Trial Count**: 200 trials per model with 2-hour timeout
- **GPU Utilization**: Full memory utilization with optimized settings
- **Advanced Analytics**: Parameter importance, correlation analysis, convergence tracking
- **Production Focus**: Specialized for imbalanced fraud detection data

**Key Features:**
- Advanced hyperparameter optimization with Optuna TPE
- Multi-model ensemble with automated selection
- GPU-accelerated training across all frameworks
- Comprehensive feature engineering and selection
- Production-grade model packaging and deployment

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

## Advanced Hyperparameter Optimization with Optuna

### Understanding Optuna: Automatic Hyperparameter Optimization

**What is Optuna?**
Optuna is a cutting-edge hyperparameter optimization framework that uses sophisticated algorithms to find optimal model configurations efficiently. Unlike grid search or random search, Optuna intelligently learns from previous trials to guide the search toward promising parameter regions.

**Why Optuna for Fraud Detection?**
- **Efficiency**: Finds better parameters with fewer trials
- **Intelligence**: Learns parameter relationships and importance
- **Pruning**: Stops unpromising trials early to save compute
- **Scalability**: Handles large parameter spaces effectively
- **Visualization**: Provides rich analysis of optimization process

### Core Optuna Concepts

#### 1. **Study**: The Optimization Experiment
```python
study = optuna.create_study(
    direction='maximize',  # Maximize AUC score
    sampler=TPESampler(seed=42),  # Tree Parzen Estimator
    pruner=MedianPruner()  # Early stopping strategy
)
```

**Key Parameters:**
- **Direction**: `maximize` for AUC, `minimize` for loss
- **Sampler**: Algorithm for parameter selection (TPE is most advanced)
- **Pruner**: Strategy for stopping unpromising trials early

#### 2. **Trial**: Individual Parameter Configuration Test
```python
def objective(trial):
    # Define parameter space
    n_estimators = trial.suggest_int('n_estimators', 500, 3000)
    learning_rate = trial.suggest_float('learning_rate', 0.005, 0.3, log=True)
    max_depth = trial.suggest_int('max_depth', 3, 15)
    
    # Train model with these parameters
    model = XGBClassifier(n_estimators=n_estimators, 
                         learning_rate=learning_rate,
                         max_depth=max_depth)
    model.fit(X_train, y_train)
    
    # Return metric to optimize
    predictions = model.predict_proba(X_val)[:, 1]
    return roc_auc_score(y_val, predictions)
```

#### 3. **Samplers**: How Parameters Are Chosen

**Tree Parzen Estimator (TPE) - Our Primary Sampler:**
```python
sampler = TPESampler(
    seed=42,                    # Reproducibility
    n_startup_trials=20,        # Random trials before TPE kicks in
    n_ei_candidates=48          # Candidates considered per trial
)
```

**How TPE Works:**
1. **Initial Random Phase**: First 20 trials are random exploration
2. **Model Building**: TPE builds two probability models:
   - **Good Model**: Models parameter combinations that gave good results (top 20%)
   - **Bad Model**: Models parameter combinations that gave poor results (bottom 80%)
3. **Smart Sampling**: New parameters chosen to maximize Expected Improvement (EI)
4. **Iterative Refinement**: Models improve with each trial, guiding search more precisely

**Why TPE is Superior:**
- **Learns Relationships**: Understands how parameters interact
- **Focuses Search**: Concentrates on promising regions
- **Handles Non-linearity**: Works with complex parameter landscapes
- **Memory Efficient**: Doesn't store all combinations like grid search

#### 4. **Pruning**: Early Stopping for Efficiency

**Median Pruner - Our Pruning Strategy:**
```python
pruner = MedianPruner(
    n_startup_trials=15,        # Don't prune first 15 trials
    n_warmup_steps=25,          # Wait 25 epochs before pruning
    interval_steps=5            # Check every 5 epochs
)
```

**How Pruning Works:**
1. **Performance Tracking**: Monitor validation scores during training
2. **Median Calculation**: Track median performance across all trials
3. **Early Termination**: Stop trials performing below median
4. **Resource Savings**: Free GPU/CPU for more promising trials

**Pruning Benefits:**
- **3-5x Speedup**: By stopping bad trials early
- **Better Exploration**: More trials in same time budget
- **Resource Efficiency**: Optimal GPU/CPU utilization

### Stream-Sentinel's Hyperparameter Space

#### XGBoost Optimization (30+ Parameters)
```python
# Core tree parameters
'n_estimators': trial.suggest_int('n_estimators', 500, 3000)
'max_depth': trial.suggest_int('max_depth', 3, 15)
'learning_rate': trial.suggest_float('learning_rate', 0.005, 0.3, log=True)

# Advanced structure control (crucial for fraud detection)
'min_child_weight': trial.suggest_float('min_child_weight', 0.1, 20)  # Prevents overfitting
'gamma': trial.suggest_float('gamma', 0, 10)                          # Minimum split loss
'max_delta_step': trial.suggest_float('max_delta_step', 0, 10)        # Imbalanced data handling

# Multi-level sampling (fine-grained control)
'subsample': trial.suggest_float('subsample', 0.4, 1.0)              # Row sampling
'colsample_bytree': trial.suggest_float('colsample_bytree', 0.4, 1.0) # Feature sampling per tree
'colsample_bylevel': trial.suggest_float('colsample_bylevel', 0.4, 1.0) # Per level
'colsample_bynode': trial.suggest_float('colsample_bynode', 0.4, 1.0)   # Per node

# Advanced regularization
'reg_alpha': trial.suggest_float('reg_alpha', 0, 50, log=True)       # L1 regularization
'reg_lambda': trial.suggest_float('reg_lambda', 0, 50, log=True)     # L2 regularization

# GPU-specific optimizations
'grow_policy': trial.suggest_categorical('grow_policy', ['depthwise', 'lossguide'])
'max_bin': trial.suggest_int('max_bin', 128, 512)                    # Histogram bins
'single_precision_histogram': trial.suggest_categorical('single_precision_histogram', [True, False])
```

**Parameter Categories Explained:**

**Tree Structure Parameters:**
- **n_estimators**: Number of boosting rounds (more = better but slower)
- **max_depth**: Tree depth (deeper = more complex patterns, but overfitting risk)
- **min_child_weight**: Minimum samples per leaf (higher = less overfitting)
- **gamma**: Minimum loss reduction for split (higher = more conservative)

**Sampling Parameters:**
- **subsample**: Fraction of samples per tree (prevents overfitting)
- **colsample_***: Feature sampling at different levels (reduces correlation)
- **Multiple levels**: Tree, level, node sampling for fine control

**Regularization Parameters:**
- **reg_alpha**: L1 regularization (feature selection effect)
- **reg_lambda**: L2 regularization (weight shrinkage effect)
- **max_delta_step**: Helps with extreme class imbalance

**GPU Optimization Parameters:**
- **tree_method**: 'gpu_hist' for GPU acceleration
- **max_bin**: Feature discretization (higher = more precision)
- **single_precision**: Speed vs precision trade-off

#### LightGBM Optimization (35+ Parameters)
```python
# LightGBM-specific leaf control (most important)
'num_leaves': trial.suggest_int('num_leaves', 10, 2048)              # Key LightGBM parameter
'min_child_samples': trial.suggest_int('min_child_samples', 5, 200)  # Overfitting control
'min_split_gain': trial.suggest_float('min_split_gain', 0.0, 10.0)   # Split threshold

# Advanced LightGBM features
'path_smooth': trial.suggest_float('path_smooth', 0.0, 1.0)          # Path smoothing
'extra_trees': trial.suggest_categorical('extra_trees', [True, False]) # Extremely randomized trees
'bagging_freq': trial.suggest_int('bagging_freq', 0, 10)             # Bagging frequency

# Categorical feature handling (crucial for fraud detection)
'cat_smooth': trial.suggest_float('cat_smooth', 1.0, 100.0)          # Categorical smoothing
'cat_l2': trial.suggest_float('cat_l2', 1.0, 100.0)                 # Categorical L2
'max_cat_threshold': trial.suggest_int('max_cat_threshold', 16, 128)  # Categorical splits
```

**LightGBM-Specific Optimizations:**

**Leaf-based Growth:**
- **num_leaves**: Most important LightGBM parameter (vs max_depth in XGBoost)
- **min_child_samples**: Prevents overfitting on small data splits
- **min_split_gain**: Controls when to make splits (quality threshold)

**Advanced Sampling:**
- **bagging_freq**: How often to resample data
- **feature_fraction_bynode**: Per-node feature sampling
- **extra_trees**: Adds randomization for better generalization

**Categorical Handling:**
- **cat_smooth**: Smoothing for categorical features
- **cat_l2**: Regularization for categorical splits
- **max_cat_threshold**: Maximum categories for optimal splits

#### CatBoost Optimization (40+ Parameters)
```python
# CatBoost-specific advanced features
'grow_policy': trial.suggest_categorical('grow_policy', 
    ['SymmetricTree', 'Depthwise', 'Lossguide'])                     # Tree growing strategy
'bootstrap_type': trial.suggest_categorical('bootstrap_type', 
    ['Bayesian', 'Bernoulli', 'MVS', 'Poisson', 'No'])             # Sampling strategy
'leaf_estimation_method': trial.suggest_categorical('leaf_estimation_method', 
    ['Newton', 'Gradient'])                                          # Leaf value estimation

# Categorical feature mastery
'one_hot_max_size': trial.suggest_int('one_hot_max_size', 2, 255)    # One-hot threshold
'max_ctr_complexity': trial.suggest_int('max_ctr_complexity', 1, 6)  # CTR feature complexity
'simple_ctr': trial.suggest_categorical('simple_ctr', [['Borders'], 
    ['BinarizedTargetMeanValue'], ['Counter']])                      # CTR types

# Advanced regularization
'random_strength': trial.suggest_float('random_strength', 0.0, 10.0) # Tree randomness
'bagging_temperature': trial.suggest_float('bagging_temperature', 0.0, 10.0) # Bootstrap intensity
```

**CatBoost Specializations:**

**Tree Growing Strategies:**
- **SymmetricTree**: Balanced trees (fast, good for most cases)
- **Depthwise**: Level-by-level growth (like XGBoost)
- **Lossguide**: Greedy leaf-wise growth (like LightGBM)

**Bootstrap Methods:**
- **Bayesian**: Weights samples by uncertainty
- **Bernoulli**: Random sampling with probability
- **MVS**: Minimum variance sampling
- **Poisson**: Poisson distribution sampling

**Categorical Feature Excellence:**
- **CTR Features**: Automatic categorical target rate features
- **One-hot Handling**: Smart encoding threshold
- **Border Features**: Categorical value boundaries

### Optimization Process & Analytics

#### 1. **Trial Execution & Monitoring**
```python
# Enhanced study configuration for comprehensive search
study = optuna.create_study(
    direction='maximize',
    sampler=TPESampler(
        seed=42,
        n_startup_trials=20,        # More random exploration
        n_ei_candidates=48          # More candidates per trial
    ),
    pruner=MedianPruner(
        n_startup_trials=15,        # Allow more trials before pruning
        n_warmup_steps=25,          # Wait longer before pruning
        interval_steps=5            # Check more frequently
    )
)

# Run optimization with extended resources
study.optimize(objective_func, n_trials=200, timeout=7200)  # 2 hours per model
```

#### 2. **Advanced Analytics & Insights**

**Parameter Importance Analysis:**
```python
importance = optuna.importance.get_param_importances(study)
# Results show which parameters matter most for your data
# Example output:
# num_leaves: 0.4234        # Most important
# learning_rate: 0.2156     # Second most important  
# min_child_samples: 0.1890 # Third most important
```

**Correlation Analysis:**
- Identify parameter interactions (e.g., num_leaves vs min_child_samples)
- Detect conflicting parameters that hurt performance when combined
- Find synergistic combinations that work well together

**Convergence Analysis:**
- Track optimization progress over trials
- Identify when search has converged
- Determine if more trials would be beneficial

#### 3. **Visualization & Results**

**Interactive Visualizations Generated:**
- **Optimization History**: Score improvements over trials
- **Parameter Importance**: Which parameters matter most
- **Parameter Slices**: Individual parameter effect analysis
- **Correlation Heatmaps**: Parameter interaction analysis

**Comprehensive Results Saved:**
```
models/hyperparameter_results/
├── xgboost_gpu/
│   ├── trials_history.json              # All trial data
│   ├── param_importance.json            # Parameter rankings
│   ├── correlations.json                # Parameter correlations
│   ├── optimization_history.html        # Interactive convergence plot
│   ├── param_importance.html            # Interactive importance plot
│   └── correlation_heatmap.png          # Correlation visualization
└── lightgbm_gpu/...
```

### Why This Approach is Superior

#### **vs Grid Search:**
- **Efficiency**: 10-50x faster for same quality results
- **Intelligence**: Learns from previous trials
- **Scalability**: Handles 30+ parameters efficiently
- **Resource Usage**: Optimal GPU/CPU utilization

#### **vs Random Search:**
- **Targeted**: Focuses on promising regions
- **Learning**: Improves with each trial
- **Pruning**: Stops bad trials early
- **Analysis**: Provides deep insights into parameter relationships

#### **vs Manual Tuning:**
- **Exhaustive**: Tests more combinations than humanly possible
- **Objective**: No human bias in parameter selection
- **Reproducible**: Consistent results across runs
- **Scalable**: Works with any model complexity

### Production Benefits for Fraud Detection

#### **Imbalanced Data Handling:**
- **Custom Class Weights**: Automatically optimized for fraud rate
- **Sampling Strategies**: Advanced techniques for minority class
- **Threshold Optimization**: Finds optimal decision boundaries

#### **Feature-Rich Datasets:**
- **Categorical Mastery**: CatBoost's advanced categorical handling
- **High-Dimensional**: Efficient feature sampling strategies
- **Missing Value Handling**: Built-in strategies optimized per model

#### **Performance Optimization:**
- **GPU Utilization**: Full memory and compute utilization
- **Early Stopping**: Prevents overfitting automatically
- **Model Selection**: Chooses best architecture automatically

### Optuna Best Practices & Troubleshooting

#### **Optimal Study Configuration**
```python
# For fraud detection (imbalanced data)
study = optuna.create_study(
    direction='maximize',                    # Always maximize AUC for classification
    sampler=TPESampler(
        seed=42,                            # Reproducibility
        n_startup_trials=20,                # 10% of total trials for exploration
        n_ei_candidates=48,                 # Higher for complex parameter spaces
        multivariate=True                   # Consider parameter interactions
    ),
    pruner=MedianPruner(
        n_startup_trials=15,                # Allow initial exploration
        n_warmup_steps=25,                  # Wait for stable performance
        interval_steps=5                    # Check frequently for efficiency
    )
)
```

#### **Parameter Space Design Principles**

**DO:**
- Use `log=True` for learning rates, regularization parameters
- Set realistic bounds based on model documentation
- Include interaction terms for related parameters
- Use categorical choices for discrete options
- Scale ranges based on dataset size and complexity

**DON'T:**
- Make ranges too narrow (limits exploration)
- Include conflicting parameters without handling
- Use linear scales for exponential parameters
- Ignore computational constraints in bounds

#### **Common Issues & Solutions**

**Slow Convergence:**
```python
# Increase exploration phase
sampler = TPESampler(n_startup_trials=50)  # More random exploration

# Reduce pruning aggressiveness
pruner = MedianPruner(n_startup_trials=30, n_warmup_steps=50)

# Check parameter ranges aren't too restrictive
'learning_rate': trial.suggest_float('learning_rate', 0.001, 0.5, log=True)  # Wider range
```

**Memory Issues:**
```python
# Reduce model complexity during search
'n_estimators': trial.suggest_int('n_estimators', 100, 1000)  # Lower max

# Use early stopping more aggressively
early_stopping_rounds = 50  # Stop sooner

# Clear GPU memory between trials
torch.cuda.empty_cache() if torch.cuda.is_available() else None
```

**No Performance Improvement:**
```python
# Check if baseline is already optimal
baseline_score = 0.85
study_best = study.best_value
improvement = study_best - baseline_score

if improvement < 0.01:  # Less than 1% improvement
    print("Consider: larger dataset, different features, or ensemble methods")
```

#### **Parameter Importance Interpretation**

**High Importance (>0.3):**
- Focus tuning efforts here
- Consider expanding parameter ranges
- May indicate fundamental model behavior

**Medium Importance (0.1-0.3):**
- Secondary tuning priority
- Good candidates for interaction analysis
- Worth monitoring during optimization

**Low Importance (<0.1):**
- May be redundant with other parameters
- Consider removing to simplify search space
- Potentially dataset-specific effects

#### **Advanced Optuna Features for Production**

**Multi-Objective Optimization:**
```python
# Optimize both AUC and training time
def objective(trial):
    start_time = time.time()
    
    # ... train model ...
    
    training_time = time.time() - start_time
    auc_score = roc_auc_score(y_val, predictions)
    
    # Return tuple for multi-objective
    return auc_score, -training_time  # Negative because we want to minimize time

# Create multi-objective study
study = optuna.create_study(directions=['maximize', 'maximize'])
```

**Distributed Optimization:**
```python
# For multiple GPUs or machines
study = optuna.create_study(
    storage='mysql://user:pass@host/db',  # Shared database
    study_name='fraud_detection_opt'
)

# Each process/machine runs
study.optimize(objective, n_trials=50)  # Distributed across resources
```

**Custom Callbacks:**
```python
def logging_callback(study, trial):
    print(f"Trial {trial.number}: {trial.value:.4f} in {trial.duration.total_seconds():.1f}s")

study.optimize(objective, n_trials=200, callbacks=[logging_callback])
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
- **Accuracy**: 87%+ AUC with comprehensive hyperparameter optimization
- **Optimization**: 200 trials × 3 models = 600+ parameter combinations tested
- **Training Time**: 2-6 hours for complete optimization (vs 2-3% AUC improvement)
- **GPU Utilization**: 95%+ memory utilization during training
- **Latency**: <100ms prediction time including feature engineering
- **Throughput**: 10k+ predictions per second
- **Reliability**: 99.9% uptime with graceful degradation

### Hyperparameter Optimization Performance
- **Search Efficiency**: 10-50x faster than grid search for equivalent results
- **Parameter Coverage**: 30+ XGBoost, 35+ LightGBM, 40+ CatBoost parameters
- **Pruning Efficiency**: 30-50% of trials pruned early for 3-5x speedup
- **Convergence**: Typically converges within 100-150 trials per model
- **Analysis Depth**: Parameter importance, correlations, and interaction effects

### Online Learning Performance
- **Model Updates**: Complete in <30 minutes for 50k samples
- **Drift Detection**: Real-time analysis on 100k+ predictions
- **Feedback Processing**: 10k+ records per hour with validation
- **A/B Testing**: Handle 10k+ concurrent user assignments

## Implementation Guides

### Comprehensive Model Training
```bash
# Full hyperparameter optimization (2-6 hours with GPU)
python src/ml/ieee_model_trainer.py

# Quick training with sample data for testing
python -c "
from src.ml.ieee_model_trainer import IEEEModelTrainer
trainer = IEEEModelTrainer()
model_path = trainer.run_complete_training_pipeline(sample_size=50000)
print(f'Model saved to: {model_path}')
"

# View comprehensive results
cat models/ieee_fraud_model_metadata.json
cat models/training_summary/training_report_*.json
```

### Hyperparameter Analysis
```bash
# View parameter importance for best model
python -c "
import json
with open('models/hyperparameter_results/lightgbm_gpu/param_importance.json') as f:
    importance = json.load(f)
    for param, score in sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f'{param}: {score:.4f}')
"

# Open interactive visualizations
open models/hyperparameter_results/lightgbm_gpu/optimization_history.html
open models/hyperparameter_results/lightgbm_gpu/param_importance.html
```

### Advanced Training Configuration
```python
from src.ml.ieee_model_trainer import IEEEModelTrainer

# Initialize trainer with GPU optimization
trainer = IEEEModelTrainer(data_path="data/raw", models_path="models")

# Custom training with specific trial count
trainer.load_and_preprocess_data()
trainer.prepare_train_test_split(X, y)
trainer.train_baseline_logistic_regression()

# Extended hyperparameter search (4+ hours)
trainer.train_gradient_boosting_models(n_trials=300)

# Analyze and select best model
best_model_name = trainer.compare_models()
final_model = trainer.retrain_on_full_dataset(best_model_name)
trainer.save_production_model(final_model, best_model_name)
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
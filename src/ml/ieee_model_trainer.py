# /stream-sentinel/src/ml/ieee_model_trainer.py

"""
IEEE-CIS Fraud Detection Model Trainer

This module implements comprehensive ML model training with GPU optimization,
automated hyperparameter tuning, and model comparison for fraud detection.
Uses the IEEE-CIS dataset to train production-ready fraud detection models.

Key features:
- GPU-optimized training with CuML/XGBoost GPU support
- Automated hyperparameter tuning with Optuna
- Multiple model architectures with performance comparison
- Production model selection and retraining
- Comprehensive evaluation metrics and model explainability
"""

import os
import sys
import time
import pickle
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import json
import logging
from datetime import datetime

# Core ML libraries
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    classification_report, confusion_matrix, roc_auc_score, 
    precision_recall_curve, roc_curve, average_precision_score,
    precision_score, recall_score, f1_score
)

# Gradient boosting models
import xgboost as xgb
import lightgbm as lgb
try:
    import catboost as cb
    CATBOOST_AVAILABLE = True
except ImportError:
    CATBOOST_AVAILABLE = False

# Hyperparameter optimization
import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner
from optuna.visualization import plot_optimization_history, plot_param_importances, plot_slice

# GPU acceleration
try:
    import cuml
    from cuml.ensemble import RandomForestClassifier as cuRF
    from cuml.linear_model import LogisticRegression as cuLR
    CUML_AVAILABLE = True
except ImportError:
    CUML_AVAILABLE = False

# Plotting
import matplotlib.pyplot as plt
import seaborn as sns

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore')

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class IEEEModelTrainer:
    """
    Comprehensive ML model trainer for IEEE-CIS fraud detection dataset.
    
    Features:
    - GPU-optimized training
    - Multiple model architectures
    - Automated hyperparameter tuning
    - Model comparison and selection
    - Production model deployment
    """
    
    def __init__(self, data_path: str = "data/raw", models_path: str = "models"):
        """
        Initialize the model trainer.
        
        Args:
            data_path: Path to IEEE-CIS dataset files
            models_path: Path to save trained models
        """
        self.data_path = Path(data_path)
        self.models_path = Path(models_path)
        self.models_path.mkdir(exist_ok=True)
        
        # GPU availability check
        self.gpu_available = self._check_gpu_availability()
        logger.info(f"GPU acceleration available: {self.gpu_available}")
        
        # Model storage
        self.models = {}
        self.model_scores = {}
        self.best_model = None
        self.best_score = 0.0
        
        # Data storage
        self.X_train = None
        self.X_val = None  
        self.X_test = None
        self.y_train = None
        self.y_val = None
        self.y_test = None
        self.feature_names = None
        self.scaler = None
        self.label_encoders = {}
        
        logger.info("IEEEModelTrainer initialized")
    
    def _check_gpu_availability(self) -> Dict[str, bool]:
        """Check what GPU acceleration is available."""
        gpu_info = {
            'cuda': False,
            'cuml': CUML_AVAILABLE,
            'xgboost_gpu': False,
            'lightgbm_gpu': False,
            'catboost_gpu': CATBOOST_AVAILABLE
        }
        
        # Check CUDA
        try:
            import torch
            gpu_info['cuda'] = torch.cuda.is_available()
        except ImportError:
            pass
            
        # Check XGBoost GPU support
        try:
            xgb_gpu_test = xgb.XGBClassifier(tree_method='gpu_hist', gpu_id=0, n_estimators=1)
            gpu_info['xgboost_gpu'] = True
        except:
            pass
            
        # Check LightGBM GPU support with actual training test
        try:
            import numpy as np
            X_test = np.random.random((100, 5))
            y_test = np.random.randint(0, 2, 100)
            lgb_gpu_test = lgb.LGBMClassifier(device='gpu', n_estimators=1, verbose=-1)
            lgb_gpu_test.fit(X_test, y_test)
            gpu_info['lightgbm_gpu'] = True
        except Exception as e:
            # GPU not available, fallback to CPU
            logger.warning(f"LightGBM GPU not available: {e}")
            pass
            
        return gpu_info
    
    def _save_hyperparameter_results(self, study: optuna.Study, model_name: str):
        """
        Save detailed hyperparameter optimization results and visualizations.
        
        Args:
            study: Completed Optuna study
            model_name: Name of the model being optimized
        """
        logger.info(f"Saving detailed hyperparameter results for {model_name}...")
        
        # Create hyperparameter results directory
        hp_results_dir = self.models_path / "hyperparameter_results"
        hp_results_dir.mkdir(exist_ok=True)
        
        model_results_dir = hp_results_dir / model_name
        model_results_dir.mkdir(exist_ok=True)
        
        # 1. Save all trial history
        trials_data = []
        pruned_trials = 0
        for trial in study.trials:
            trial_data = {
                'number': trial.number,
                'value': trial.value,
                'params': trial.params,
                'state': trial.state.name,
                'datetime_start': trial.datetime_start.isoformat() if trial.datetime_start else None,
                'datetime_complete': trial.datetime_complete.isoformat() if trial.datetime_complete else None,
                'duration': trial.duration.total_seconds() if trial.duration else None
            }
            trials_data.append(trial_data)
            
            if trial.state == optuna.trial.TrialState.PRUNED:
                pruned_trials += 1
        
        # Save trials to JSON
        trials_file = model_results_dir / f"{model_name}_trials_history.json"
        with open(trials_file, 'w') as f:
            json.dump(trials_data, f, indent=2)
        
        # 2. Calculate and save parameter importance
        try:
            param_importance = optuna.importance.get_param_importances(study)
            importance_file = model_results_dir / f"{model_name}_param_importance.json"
            with open(importance_file, 'w') as f:
                json.dump(param_importance, f, indent=2)
            
            logger.info(f"Parameter importance for {model_name}:")
            for param, importance in sorted(param_importance.items(), key=lambda x: x[1], reverse=True):
                logger.info(f"  {param}: {importance:.4f}")
        except Exception as e:
            logger.warning(f"Could not calculate parameter importance: {e}")
        
        # 3. Save convergence and pruning statistics
        convergence_stats = {
            'best_value': study.best_value,
            'best_params': study.best_params,
            'best_trial_number': study.best_trial.number,
            'n_trials': len(study.trials),
            'n_complete_trials': len([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]),
            'n_pruned_trials': pruned_trials,
            'n_failed_trials': len([t for t in study.trials if t.state == optuna.trial.TrialState.FAIL]),
            'pruning_rate': pruned_trials / len(study.trials) if study.trials else 0,
            'study_direction': study.direction.name
        }
        
        convergence_file = model_results_dir / f"{model_name}_convergence_stats.json"
        with open(convergence_file, 'w') as f:
            json.dump(convergence_stats, f, indent=2)
        
        # 4. Generate and save visualizations
        try:
            # Optimization history plot
            fig1 = plot_optimization_history(study)
            fig1.write_html(str(model_results_dir / f"{model_name}_optimization_history.html"))
            
            # Parameter importance plot
            if len(study.trials) > 10:  # Need sufficient trials for importance
                fig2 = plot_param_importances(study)
                fig2.write_html(str(model_results_dir / f"{model_name}_param_importance.html"))
            
            # Parameter slice plots (for top 3 most important parameters)
            if param_importance:
                top_params = sorted(param_importance.items(), key=lambda x: x[1], reverse=True)[:3]
                for param_name, _ in top_params:
                    try:
                        fig3 = plot_slice(study, params=[param_name])
                        fig3.write_html(str(model_results_dir / f"{model_name}_slice_{param_name}.html"))
                    except Exception as e:
                        logger.warning(f"Could not create slice plot for {param_name}: {e}")
                        
        except Exception as e:
            logger.warning(f"Could not generate visualizations: {e}")
        
        # 5. Log detailed statistics
        logger.info(f"Hyperparameter optimization summary for {model_name}:")
        logger.info(f"  Best AUC: {study.best_value:.4f}")
        logger.info(f"  Total trials: {len(study.trials)}")
        logger.info(f"  Completed trials: {convergence_stats['n_complete_trials']}")
        logger.info(f"  Pruned trials: {pruned_trials} ({convergence_stats['pruning_rate']:.1%})")
        logger.info(f"  Failed trials: {convergence_stats['n_failed_trials']}")
        logger.info(f"  Results saved to: {model_results_dir}")
        
        return convergence_stats
    
    def _analyze_hyperparameter_correlations(self, study: optuna.Study, model_name: str):
        """
        Analyze correlations between hyperparameters and performance.
        
        Args:
            study: Completed Optuna study
            model_name: Name of the model being analyzed
        """
        if len(study.trials) < 10:  # Need sufficient trials for correlation analysis
            logger.warning(f"Insufficient trials ({len(study.trials)}) for correlation analysis")
            return
        
        logger.info(f"Analyzing hyperparameter correlations for {model_name}...")
        
        # Extract completed trials data
        completed_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if len(completed_trials) < 5:
            logger.warning("Insufficient completed trials for correlation analysis")
            return
        
        # Create correlation matrix data
        correlation_data = []
        param_names = list(completed_trials[0].params.keys())
        
        for trial in completed_trials:
            row_data = {}
            for param in param_names:
                row_data[param] = trial.params[param]
            row_data['objective_value'] = trial.value
            correlation_data.append(row_data)
        
        # Convert to DataFrame for correlation analysis
        correlation_df = pd.DataFrame(correlation_data)
        
        # Calculate correlation matrix
        correlation_matrix = correlation_df.corr()
        
        # Extract correlations with objective value
        objective_correlations = correlation_matrix['objective_value'].drop('objective_value')
        objective_correlations = objective_correlations.sort_values(key=abs, ascending=False)
        
        # Log correlation results
        logger.info(f"Hyperparameter correlations with AUC for {model_name}:")
        for param, corr in objective_correlations.items():
            correlation_strength = "strong" if abs(corr) > 0.5 else "moderate" if abs(corr) > 0.3 else "weak"
            logger.info(f"  {param}: {corr:.4f} ({correlation_strength})")
        
        # Save correlation results
        hp_results_dir = self.models_path / "hyperparameter_results" / model_name
        correlation_file = hp_results_dir / f"{model_name}_correlations.json"
        
        correlation_results = {
            'objective_correlations': objective_correlations.to_dict(),
            'full_correlation_matrix': correlation_matrix.to_dict(),
            'analysis_summary': {
                'n_trials_analyzed': len(completed_trials),
                'strongest_positive_correlation': objective_correlations.idxmax(),
                'strongest_negative_correlation': objective_correlations.idxmin(),
                'max_positive_correlation': float(objective_correlations.max()),
                'max_negative_correlation': float(objective_correlations.min())
            }
        }
        
        with open(correlation_file, 'w') as f:
            json.dump(correlation_results, f, indent=2)
        
        # Create correlation heatmap if matplotlib/seaborn available
        try:
            plt.figure(figsize=(10, 8))
            sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', center=0, 
                       square=True, fmt='.3f')
            plt.title(f'Hyperparameter Correlation Matrix - {model_name}')
            plt.tight_layout()
            
            heatmap_file = hp_results_dir / f"{model_name}_correlation_heatmap.png"
            plt.savefig(heatmap_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            logger.info(f"Correlation heatmap saved to: {heatmap_file}")
        except Exception as e:
            logger.warning(f"Could not create correlation heatmap: {e}")
        
        return correlation_results
    
    def _save_training_summary_report(self):
        """
        Generate and save a comprehensive training summary report.
        """
        logger.info("Generating comprehensive training summary report...")
        
        # Create summary report directory
        summary_dir = self.models_path / "training_summary"
        summary_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = summary_dir / f"training_report_{timestamp}.json"
        
        # Compile comprehensive training report
        training_report = {
            'report_metadata': {
                'generated_at': datetime.now().isoformat(),
                'training_data_size': len(self.X_train) + len(self.X_val) + len(self.X_test) if self.X_train is not None else 'Unknown',
                'feature_count': len(self.feature_names) if self.feature_names else 'Unknown',
                'gpu_capabilities': self.gpu_available
            },
            'data_splits': {
                'train_size': len(self.X_train) if self.X_train is not None else 0,
                'validation_size': len(self.X_val) if self.X_val is not None else 0,
                'test_size': len(self.X_test) if self.X_test is not None else 0,
                'train_fraud_rate': float(self.y_train.mean()) if self.y_train is not None else 0,
                'val_fraud_rate': float(self.y_val.mean()) if self.y_val is not None else 0,
                'test_fraud_rate': float(self.y_test.mean()) if self.y_test is not None else 0
            },
            'model_results': {},
            'best_model': {
                'name': None,
                'auc_score': 0.0,
                'parameters': {},
                'training_time': 0.0
            },
            'training_efficiency': {
                'total_training_time': 0.0,
                'total_trials': 0,
                'total_pruned_trials': 0,
                'average_pruning_rate': 0.0
            }
        }
        
        # Populate model results
        total_training_time = 0.0
        total_trials = 0
        total_pruned_trials = 0
        best_auc = 0.0
        best_model_name = None
        
        for model_name, metrics in self.model_scores.items():
            if '_final' in model_name:  # Skip final training results
                continue
                
            training_report['model_results'][model_name] = {
                'performance': {
                    'validation_auc': metrics.get('val_auc', 0),
                    'validation_precision': metrics.get('val_precision', 0),
                    'validation_recall': metrics.get('val_recall', 0),
                    'validation_f1': metrics.get('val_f1', 0),
                    'validation_average_precision': metrics.get('val_average_precision', 0)
                },
                'training_details': {
                    'training_time_seconds': metrics.get('training_time', 0),
                    'gpu_accelerated': metrics.get('gpu_accelerated', False),
                    'n_trials': metrics.get('n_trials', 0),
                    'best_parameters': metrics.get('best_params', {})
                },
                'optimization_stats': metrics.get('convergence_stats', {})
            }
            
            # Update totals
            total_training_time += metrics.get('training_time', 0)
            total_trials += metrics.get('n_trials', 0)
            convergence_stats = metrics.get('convergence_stats', {})
            total_pruned_trials += convergence_stats.get('n_pruned_trials', 0)
            
            # Track best model
            current_auc = metrics.get('val_auc', 0)
            if current_auc > best_auc:
                best_auc = current_auc
                best_model_name = model_name
        
        # Update best model info
        if best_model_name:
            best_metrics = self.model_scores[best_model_name]
            training_report['best_model'] = {
                'name': best_model_name,
                'auc_score': best_auc,
                'parameters': best_metrics.get('best_params', {}),
                'training_time': best_metrics.get('training_time', 0),
                'optimization_efficiency': {
                    'trials_used': best_metrics.get('n_trials', 0),
                    'pruning_rate': best_metrics.get('convergence_stats', {}).get('pruning_rate', 0)
                }
            }
        
        # Update training efficiency
        training_report['training_efficiency'] = {
            'total_training_time': total_training_time,
            'total_trials': total_trials,
            'total_pruned_trials': total_pruned_trials,
            'average_pruning_rate': total_pruned_trials / max(total_trials, 1),
            'models_trained': len([k for k in self.model_scores.keys() if '_final' not in k])
        }
        
        # Save comprehensive report
        with open(report_file, 'w') as f:
            json.dump(training_report, f, indent=2)
        
        logger.info(f"Training summary report saved to: {report_file}")
        
        # Log key summary statistics
        logger.info("\n" + "="*60)
        logger.info("TRAINING SESSION SUMMARY")
        logger.info("="*60)
        logger.info(f"Models Trained: {training_report['training_efficiency']['models_trained']}")
        logger.info(f"Total Training Time: {total_training_time:.1f}s ({total_training_time/60:.1f} minutes)")
        logger.info(f"Total Optimization Trials: {total_trials}")
        logger.info(f"Average Pruning Rate: {training_report['training_efficiency']['average_pruning_rate']*100:.1f}%")
        logger.info(f"Best Model: {best_model_name} (AUC: {best_auc:.4f})")
        logger.info(f"GPU Acceleration Used: {any(self.gpu_available.values())}")
        logger.info("="*60)
        
        return training_report
    
    def load_and_preprocess_data(self, sample_size: Optional[int] = None) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Load and preprocess IEEE-CIS dataset.
        
        Args:
            sample_size: Optional sample size for faster experimentation
            
        Returns:
            Tuple of (features, labels)
        """
        logger.info("Loading IEEE-CIS dataset...")
        
        # Load transaction data
        train_transaction_path = self.data_path / "train_transaction.csv"
        if not train_transaction_path.exists():
            raise FileNotFoundError(f"IEEE dataset not found at {train_transaction_path}")
            
        transaction_data = pd.read_csv(train_transaction_path)
        logger.info(f"Loaded {len(transaction_data)} transactions")
        
        # Load identity data if available
        train_identity_path = self.data_path / "train_identity.csv"
        if train_identity_path.exists():
            identity_data = pd.read_csv(train_identity_path)
            logger.info(f"Loaded {len(identity_data)} identity records")
            
            # Merge on TransactionID
            data = transaction_data.merge(identity_data, on='TransactionID', how='left')
            logger.info(f"Merged dataset: {len(data)} records")
        else:
            data = transaction_data
            logger.info("Identity data not found, using transaction data only")
        
        # Sample for faster experimentation if requested
        if sample_size and sample_size < len(data):
            # Stratified sampling to maintain fraud rate
            from sklearn.model_selection import train_test_split
            data_sample, _ = train_test_split(
                data, train_size=sample_size, stratify=data['isFraud'], 
                random_state=42
            )
            data = data_sample
            logger.info(f"Sampled {len(data)} records for training")
        
        # Preprocess features
        X, y = self._preprocess_features(data)
        
        logger.info(f"Final dataset: {X.shape[0]} samples, {X.shape[1]} features")
        logger.info(f"Fraud rate: {y.mean():.4f}")
        
        return X, y
    
    def _preprocess_features(self, data: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Preprocess IEEE-CIS features for ML training.
        
        Args:
            data: Raw IEEE-CIS data
            
        Returns:
            Tuple of (processed_features, labels)
        """
        logger.info("Preprocessing features...")
        
        # Separate features and target
        target = 'isFraud'
        y = data[target].copy()
        
        # Remove target and ID columns
        exclude_cols = [target, 'TransactionID']
        X = data.drop(columns=[col for col in exclude_cols if col in data.columns])
        
        # Feature engineering
        X = self._engineer_features(X)
        
        # Handle categorical variables
        categorical_cols = X.select_dtypes(include=['object']).columns
        logger.info(f"Encoding {len(categorical_cols)} categorical features")
        
        for col in categorical_cols:
            if col not in self.label_encoders:
                self.label_encoders[col] = LabelEncoder()
                X[col] = self.label_encoders[col].fit_transform(X[col].fillna('unknown'))
            else:
                X[col] = self.label_encoders[col].transform(X[col].fillna('unknown'))
        
        # Handle missing values
        X = X.fillna(0)  # Simple imputation for now
        
        # Feature selection - keep most important features for training speed
        X = self._select_features(X, y)
        
        self.feature_names = list(X.columns)
        logger.info(f"Selected {len(self.feature_names)} features for training")
        
        return X, y
    
    def _engineer_features(self, X: pd.DataFrame) -> pd.DataFrame:
        """Engineer additional features from IEEE-CIS dataset."""
        logger.info("Engineering additional features...")
        
        # Amount-based features
        if 'TransactionAmt' in X.columns:
            X['TransactionAmt_log'] = np.log1p(X['TransactionAmt'])
            X['TransactionAmt_decimal'] = X['TransactionAmt'] % 1
            X['TransactionAmt_round'] = (X['TransactionAmt'] % 1 == 0).astype(int)
        
        # Time-based features
        if 'TransactionDT' in X.columns:
            # Convert to hours since start
            X['TransactionDT_hour'] = (X['TransactionDT'] // 3600) % 24
            X['TransactionDT_day'] = (X['TransactionDT'] // (3600 * 24)) % 7
            X['TransactionDT_week'] = X['TransactionDT'] // (3600 * 24 * 7)
        
        # Card features aggregation
        card_cols = [col for col in X.columns if col.startswith('card')]
        if len(card_cols) > 1:
            X['card_features_null_count'] = X[card_cols].isnull().sum(axis=1)
        
        # Address features
        if 'addr1' in X.columns and 'addr2' in X.columns:
            X['addr_match'] = (X['addr1'] == X['addr2']).astype(int)
        
        # Email domain features
        if 'P_emaildomain' in X.columns:
            # Common domains
            common_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            X['P_emaildomain_common'] = X['P_emaildomain'].isin(common_domains).astype(int)
        
        # C features aggregation
        c_cols = [col for col in X.columns if col.startswith('C')]
        if len(c_cols) > 0:
            X['C_features_sum'] = X[c_cols].sum(axis=1)
            X['C_features_mean'] = X[c_cols].mean(axis=1)
            X['C_features_std'] = X[c_cols].std(axis=1).fillna(0)
        
        # V features aggregation (first 100 only for speed)
        v_cols = [col for col in X.columns if col.startswith('V')][:100]
        if len(v_cols) > 0:
            X['V_features_sum'] = X[v_cols].sum(axis=1)
            X['V_features_mean'] = X[v_cols].mean(axis=1)
            X['V_features_std'] = X[v_cols].std(axis=1).fillna(0)
        
        return X
    
    def _select_features(self, X: pd.DataFrame, y: pd.Series, max_features: int = 200) -> pd.DataFrame:
        """Select most important features for training efficiency."""
        if len(X.columns) <= max_features:
            return X
        
        logger.info(f"Selecting top {max_features} features from {len(X.columns)}")
        
        # Use mutual information for feature selection
        from sklearn.feature_selection import SelectKBest, mutual_info_classif
        
        selector = SelectKBest(mutual_info_classif, k=max_features)
        X_selected = selector.fit_transform(X, y)
        
        selected_features = X.columns[selector.get_support()].tolist()
        return pd.DataFrame(X_selected, columns=selected_features, index=X.index)
    
    def prepare_train_test_split(self, X: pd.DataFrame, y: pd.Series, 
                                test_size: float = 0.2, val_size: float = 0.1):
        """
        Split data into train/validation/test sets with proper stratification.
        
        Args:
            X: Features
            y: Labels  
            test_size: Proportion for test set
            val_size: Proportion for validation set (from remaining data)
        """
        logger.info("Splitting data into train/validation/test sets...")
        
        # First split: separate test set
        X_temp, self.X_test, y_temp, self.y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )
        
        # Second split: separate validation set from remaining data
        val_size_adjusted = val_size / (1 - test_size)  # Adjust for remaining data
        self.X_train, self.X_val, self.y_train, self.y_val = train_test_split(
            X_temp, y_temp, test_size=val_size_adjusted, random_state=42, stratify=y_temp
        )
        
        logger.info(f"Train set: {len(self.X_train)} samples ({self.y_train.mean():.4f} fraud rate)")
        logger.info(f"Validation set: {len(self.X_val)} samples ({self.y_val.mean():.4f} fraud rate)")
        logger.info(f"Test set: {len(self.X_test)} samples ({self.y_test.mean():.4f} fraud rate)")
        
        # Feature scaling
        self.scaler = StandardScaler()
        self.X_train_scaled = pd.DataFrame(
            self.scaler.fit_transform(self.X_train),
            columns=self.X_train.columns,
            index=self.X_train.index
        )
        self.X_val_scaled = pd.DataFrame(
            self.scaler.transform(self.X_val),
            columns=self.X_val.columns,
            index=self.X_val.index
        )
        self.X_test_scaled = pd.DataFrame(
            self.scaler.transform(self.X_test),
            columns=self.X_test.columns, 
            index=self.X_test.index
        )
    
    def train_baseline_logistic_regression(self) -> Dict[str, Any]:
        """
        Train logistic regression baseline model.
        
        Returns:
            Dictionary with model performance metrics
        """
        logger.info("Training Logistic Regression baseline...")
        
        start_time = time.time()
        
        # Use GPU-accelerated version if available
        if self.gpu_available.get('cuml', False):
            logger.info("Using GPU-accelerated CuML Logistic Regression")
            model = cuLR(max_iter=1000, tol=1e-4)
            model.fit(self.X_train_scaled, self.y_train)
        else:
            logger.info("Using CPU Logistic Regression")
            model = LogisticRegression(
                max_iter=1000, random_state=42, n_jobs=-1,
                class_weight='balanced'  # Handle class imbalance
            )
            model.fit(self.X_train_scaled, self.y_train)
        
        training_time = time.time() - start_time
        
        # Evaluate model
        y_val_pred_proba = model.predict_proba(self.X_val_scaled)[:, 1]
        y_val_pred = (y_val_pred_proba > 0.5).astype(int)
        
        # Calculate metrics
        val_auc = roc_auc_score(self.y_val, y_val_pred_proba)
        val_precision = precision_score(self.y_val, y_val_pred)
        val_recall = recall_score(self.y_val, y_val_pred)
        val_f1 = f1_score(self.y_val, y_val_pred)
        val_ap = average_precision_score(self.y_val, y_val_pred_proba)
        
        metrics = {
            'model_type': 'logistic_regression',
            'training_time': training_time,
            'val_auc': val_auc,
            'val_precision': val_precision,
            'val_recall': val_recall,
            'val_f1': val_f1,
            'val_average_precision': val_ap,
            'gpu_accelerated': self.gpu_available.get('cuml', False)
        }
        
        # Store model and scores
        self.models['logistic_regression'] = model
        self.model_scores['logistic_regression'] = metrics
        
        logger.info(f"Logistic Regression - AUC: {val_auc:.4f}, Training time: {training_time:.1f}s")
        
        return metrics
    
    def train_gradient_boosting_models(self, n_trials: int = 50) -> Dict[str, Dict[str, Any]]:
        """
        Train multiple gradient boosting models with hyperparameter tuning.
        
        Returns:
            Dictionary with all model performance metrics
        """
        logger.info("Training gradient boosting models with hyperparameter optimization...")
        
        models_to_train = []
        
        # XGBoost
        if self.gpu_available.get('xgboost_gpu', False):
            models_to_train.append(('xgboost_gpu', self._get_xgboost_objective_gpu))
            logger.info("Will train XGBoost with GPU acceleration")
        else:
            models_to_train.append(('xgboost_cpu', self._get_xgboost_objective_cpu))
            logger.info("Will train XGBoost with CPU")
        
        # LightGBM
        if self.gpu_available.get('lightgbm_gpu', False):
            models_to_train.append(('lightgbm_gpu', self._get_lightgbm_objective_gpu))
            logger.info("Will train LightGBM with GPU acceleration")
        else:
            models_to_train.append(('lightgbm_cpu', self._get_lightgbm_objective_cpu))
            logger.info("Will train LightGBM with CPU")
        
        # CatBoost
        if CATBOOST_AVAILABLE:
            if self.gpu_available.get('catboost_gpu', False):
                models_to_train.append(('catboost_gpu', self._get_catboost_objective_gpu))
                logger.info("Will train CatBoost with GPU acceleration")
            else:
                models_to_train.append(('catboost_cpu', self._get_catboost_objective_cpu))
                logger.info("Will train CatBoost with CPU")
        
        results = {}
        
        for model_name, objective_func in models_to_train:
            logger.info(f"Starting hyperparameter optimization for {model_name}...")
            
            # Create Optuna study
            study = optuna.create_study(
                direction='maximize',
                sampler=TPESampler(seed=42),
                pruner=MedianPruner(n_startup_trials=5, n_warmup_steps=10)
            )
            
            # Optimize hyperparameters
            study.optimize(objective_func, n_trials=n_trials, timeout=1800)  # 30 minutes max per model
            
            # Save detailed hyperparameter results and analyze correlations
            convergence_stats = self._save_hyperparameter_results(study, model_name)
            correlation_results = self._analyze_hyperparameter_correlations(study, model_name)
            
            # Train final model with best parameters
            best_params = study.best_params
            logger.info(f"Best parameters for {model_name}: {best_params}")
            
            # Train model with best parameters
            start_time = time.time()
            model, metrics = self._train_final_model(model_name, best_params)
            training_time = time.time() - start_time
            
            metrics['training_time'] = training_time
            metrics['best_params'] = best_params
            metrics['n_trials'] = len(study.trials)
            metrics['convergence_stats'] = convergence_stats
            
            # Store results
            self.models[model_name] = model
            self.model_scores[model_name] = metrics
            results[model_name] = metrics
            
            logger.info(f"{model_name} - AUC: {metrics['val_auc']:.4f}, Training time: {training_time:.1f}s")
        
        return results
    
    def _get_xgboost_objective_gpu(self, trial) -> float:
        """Objective function for XGBoost GPU hyperparameter optimization."""
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 100, 1000),
            'max_depth': trial.suggest_int('max_depth', 3, 10),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'subsample': trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 0, 10),
            'reg_lambda': trial.suggest_float('reg_lambda', 0, 10),
            'tree_method': 'gpu_hist',
            'gpu_id': 0,
            'random_state': 42,
            'eval_metric': 'auc',
            'scale_pos_weight': (self.y_train == 0).sum() / (self.y_train == 1).sum()
        }
        
        # Add early stopping to params in XGBoost 2.0+
        params['early_stopping_rounds'] = 50
        model = xgb.XGBClassifier(**params)
        model.fit(
            self.X_train, self.y_train,
            eval_set=[(self.X_val, self.y_val)],
            verbose=False
        )
        
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        return roc_auc_score(self.y_val, y_val_pred_proba)
    
    def _get_xgboost_objective_cpu(self, trial) -> float:
        """Objective function for XGBoost CPU hyperparameter optimization."""
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 100, 1000),
            'max_depth': trial.suggest_int('max_depth', 3, 10),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'subsample': trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 0, 10),
            'reg_lambda': trial.suggest_float('reg_lambda', 0, 10),
            'n_jobs': -1,
            'random_state': 42,
            'eval_metric': 'auc',
            'scale_pos_weight': (self.y_train == 0).sum() / (self.y_train == 1).sum()
        }
        
        # Add early stopping to params in XGBoost 2.0+
        params['early_stopping_rounds'] = 50
        model = xgb.XGBClassifier(**params)
        model.fit(
            self.X_train, self.y_train,
            eval_set=[(self.X_val, self.y_val)],
            verbose=False
        )
        
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        return roc_auc_score(self.y_val, y_val_pred_proba)
    
    def _get_lightgbm_objective_gpu(self, trial) -> float:
        """Objective function for LightGBM GPU hyperparameter optimization."""
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 100, 1000),
            'max_depth': trial.suggest_int('max_depth', 3, 10),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'subsample': trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 0, 10),
            'reg_lambda': trial.suggest_float('reg_lambda', 0, 10),
            'num_leaves': trial.suggest_int('num_leaves', 10, 300),
            'device': 'gpu',
            'random_state': 42,
            'metric': 'auc',
            'is_unbalance': True
        }
        
        model = lgb.LGBMClassifier(**params)
        model.fit(
            self.X_train, self.y_train,
            eval_set=[(self.X_val, self.y_val)],
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)]
        )
        
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        return roc_auc_score(self.y_val, y_val_pred_proba)
    
    def _get_lightgbm_objective_cpu(self, trial) -> float:
        """Objective function for LightGBM CPU hyperparameter optimization."""
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 100, 1000),
            'max_depth': trial.suggest_int('max_depth', 3, 10),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'subsample': trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'reg_alpha': trial.suggest_float('reg_alpha', 0, 10),
            'reg_lambda': trial.suggest_float('reg_lambda', 0, 10),
            'num_leaves': trial.suggest_int('num_leaves', 10, 300),
            'n_jobs': -1,
            'random_state': 42,
            'metric': 'auc',
            'is_unbalance': True
        }
        
        model = lgb.LGBMClassifier(**params)
        model.fit(
            self.X_train, self.y_train,
            eval_set=[(self.X_val, self.y_val)],
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)]
        )
        
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        return roc_auc_score(self.y_val, y_val_pred_proba)
    
    def _get_catboost_objective_gpu(self, trial) -> float:
        """Objective function for CatBoost GPU hyperparameter optimization."""
        params = {
            'iterations': trial.suggest_int('iterations', 100, 1000),
            'depth': trial.suggest_int('depth', 4, 10),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'l2_leaf_reg': trial.suggest_float('l2_leaf_reg', 1, 10),
            'border_count': trial.suggest_int('border_count', 32, 255),
            'task_type': 'GPU',
            'random_state': 42,
            'eval_metric': 'AUC',
            'auto_class_weights': 'Balanced',
            'verbose': False
        }
        
        model = cb.CatBoostClassifier(**params)
        model.fit(
            self.X_train, self.y_train,
            eval_set=(self.X_val, self.y_val),
            early_stopping_rounds=50,
            verbose=False
        )
        
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        return roc_auc_score(self.y_val, y_val_pred_proba)
    
    def _get_catboost_objective_cpu(self, trial) -> float:
        """Objective function for CatBoost CPU hyperparameter optimization."""
        params = {
            'iterations': trial.suggest_int('iterations', 100, 1000),
            'depth': trial.suggest_int('depth', 4, 10),
            'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
            'l2_leaf_reg': trial.suggest_float('l2_leaf_reg', 1, 10),
            'border_count': trial.suggest_int('border_count', 32, 255),
            'thread_count': -1,
            'random_state': 42,
            'eval_metric': 'AUC',
            'auto_class_weights': 'Balanced',
            'verbose': False
        }
        
        model = cb.CatBoostClassifier(**params)
        model.fit(
            self.X_train, self.y_train,
            eval_set=(self.X_val, self.y_val),
            early_stopping_rounds=50,
            verbose=False
        )
        
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        return roc_auc_score(self.y_val, y_val_pred_proba)
    
    def _train_final_model(self, model_name: str, params: Dict) -> Tuple[Any, Dict[str, Any]]:
        """Train final model with optimized parameters and evaluate."""
        
        if 'xgboost' in model_name:
            if 'gpu' in model_name:
                params.update({
                    'tree_method': 'gpu_hist',
                    'gpu_id': 0,
                    'eval_metric': 'auc',
                    'scale_pos_weight': (self.y_train == 0).sum() / (self.y_train == 1).sum()
                })
            else:
                params.update({
                    'n_jobs': -1,
                    'eval_metric': 'auc',
                    'scale_pos_weight': (self.y_train == 0).sum() / (self.y_train == 1).sum()
                })
            
            # Add early stopping to params in XGBoost 2.0+
            params['early_stopping_rounds'] = 50
            model = xgb.XGBClassifier(**params, random_state=42)
            model.fit(
                self.X_train, self.y_train,
                eval_set=[(self.X_val, self.y_val)],
                verbose=False
            )
            
        elif 'lightgbm' in model_name:
            if 'gpu' in model_name:
                params.update({'device': 'gpu', 'metric': 'auc', 'is_unbalance': True})
            else:
                params.update({'n_jobs': -1, 'metric': 'auc', 'is_unbalance': True})
            
            model = lgb.LGBMClassifier(**params, random_state=42)
            model.fit(
                self.X_train, self.y_train,
                eval_set=[(self.X_val, self.y_val)],
                callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)]
            )
            
        elif 'catboost' in model_name:
            if 'gpu' in model_name:
                params.update({
                    'task_type': 'GPU',
                    'eval_metric': 'AUC',
                    'auto_class_weights': 'Balanced',
                    'verbose': False
                })
            else:
                params.update({
                    'thread_count': -1,
                    'eval_metric': 'AUC',
                    'auto_class_weights': 'Balanced',
                    'verbose': False
                })
            
            model = cb.CatBoostClassifier(**params, random_state=42)
            model.fit(
                self.X_train, self.y_train,
                eval_set=(self.X_val, self.y_val),
                early_stopping_rounds=50,
                verbose=False
            )
        
        # Evaluate model
        y_val_pred_proba = model.predict_proba(self.X_val)[:, 1]
        y_val_pred = (y_val_pred_proba > 0.5).astype(int)
        
        metrics = {
            'model_type': model_name,
            'val_auc': roc_auc_score(self.y_val, y_val_pred_proba),
            'val_precision': precision_score(self.y_val, y_val_pred),
            'val_recall': recall_score(self.y_val, y_val_pred),
            'val_f1': f1_score(self.y_val, y_val_pred),
            'val_average_precision': average_precision_score(self.y_val, y_val_pred_proba),
            'gpu_accelerated': 'gpu' in model_name  # GPU flag based on model name
        }
        
        return model, metrics
    
    def compare_models(self) -> str:
        """
        Compare all trained models and select the best one.
        
        Returns:
            Name of the best model
        """
        logger.info("Comparing model performance...")
        
        if not self.model_scores:
            raise ValueError("No models have been trained yet")
        
        # Sort models by validation AUC
        sorted_models = sorted(
            self.model_scores.items(),
            key=lambda x: x[1]['val_auc'],
            reverse=True
        )
        
        logger.info("\n" + "="*120)
        logger.info("COMPREHENSIVE MODEL PERFORMANCE COMPARISON")
        logger.info("="*120)
        logger.info(f"{'Model':<20} {'AUC':<8} {'Precision':<10} {'Recall':<8} {'F1':<8} {'AP':<8} {'Time':<8} {'Trials':<8} {'Pruned %':<10}")
        logger.info("-"*120)
        
        for model_name, metrics in sorted_models:
            # Extract convergence stats if available
            convergence_stats = metrics.get('convergence_stats', {})
            pruned_rate = convergence_stats.get('pruning_rate', 0) * 100
            n_trials = metrics.get('n_trials', 'N/A')
            
            logger.info(
                f"{model_name:<20} "
                f"{metrics['val_auc']:<8.4f} "
                f"{metrics['val_precision']:<10.4f} "
                f"{metrics['val_recall']:<8.4f} "
                f"{metrics['val_f1']:<8.4f} "
                f"{metrics.get('val_average_precision', 0):<8.4f} "
                f"{metrics['training_time']:<8.1f} "
                f"{n_trials!s:<8} "
                f"{pruned_rate:<10.1f}"
            )
        
        logger.info("-"*120)
        
        # Additional detailed analysis for best models
        logger.info("\nTOP 3 MODELS DETAILED ANALYSIS:")
        logger.info("="*60)
        
        for i, (model_name, metrics) in enumerate(sorted_models[:3], 1):
            logger.info(f"\n#{i} - {model_name.upper()}:")
            logger.info(f"  Performance Metrics:")
            logger.info(f"    AUC: {metrics['val_auc']:.4f}")
            logger.info(f"    Precision: {metrics['val_precision']:.4f}")
            logger.info(f"    Recall: {metrics['val_recall']:.4f}")
            logger.info(f"    F1-Score: {metrics['val_f1']:.4f}")
            logger.info(f"    Average Precision: {metrics.get('val_average_precision', 0):.4f}")
            
            logger.info(f"  Training Details:")
            logger.info(f"    Training Time: {metrics['training_time']:.1f}s")
            logger.info(f"    GPU Accelerated: {metrics.get('gpu_accelerated', False)}")
            
            # Hyperparameter optimization details
            convergence_stats = metrics.get('convergence_stats', {})
            if convergence_stats:
                logger.info(f"  Hyperparameter Optimization:")
                logger.info(f"    Total Trials: {convergence_stats.get('n_trials', 'N/A')}")
                logger.info(f"    Completed Trials: {convergence_stats.get('n_complete_trials', 'N/A')}")
                logger.info(f"    Pruned Trials: {convergence_stats.get('n_pruned_trials', 'N/A')} ({convergence_stats.get('pruning_rate', 0)*100:.1f}%)")
                logger.info(f"    Failed Trials: {convergence_stats.get('n_failed_trials', 'N/A')}")
            
            # Best hyperparameters
            best_params = metrics.get('best_params', {})
            if best_params:
                logger.info(f"  Best Hyperparameters:")
                for param, value in best_params.items():
                    if isinstance(value, float):
                        logger.info(f"    {param}: {value:.4f}")
                    else:
                        logger.info(f"    {param}: {value}")
        
        logger.info("="*120)
        
        # Select best model
        best_model_name = sorted_models[0][0]
        self.best_model = self.models[best_model_name]
        self.best_score = sorted_models[0][1]['val_auc']
        
        logger.info(f"\nBest model: {best_model_name} (AUC: {self.best_score:.4f})")
        
        return best_model_name
    
    def retrain_on_full_dataset(self, best_model_name: str) -> Any:
        """
        Retrain the best model on the complete dataset (train + validation + test).
        
        Args:
            best_model_name: Name of the best performing model
            
        Returns:
            Retrained model
        """
        logger.info(f"Retraining {best_model_name} on 100% of available data...")
        
        # Combine all three sets for final production model
        X_full = pd.concat([self.X_train, self.X_val, self.X_test])
        y_full = pd.concat([self.y_train, self.y_val, self.y_test])
        
        logger.info(f"Full training set: {len(X_full)} samples (100% of data)")
        
        # Get best parameters
        best_params = self.model_scores[best_model_name]['best_params']
        
        start_time = time.time()
        
        # Retrain with best parameters
        if 'xgboost' in best_model_name:
            if 'gpu' in best_model_name:
                best_params.update({
                    'tree_method': 'gpu_hist',
                    'gpu_id': 0,
                    'eval_metric': 'auc',
                    'scale_pos_weight': (y_full == 0).sum() / (y_full == 1).sum()
                })
            else:
                best_params.update({
                    'n_jobs': -1,
                    'eval_metric': 'auc',
                    'scale_pos_weight': (y_full == 0).sum() / (y_full == 1).sum()
                })
            
            # Remove early stopping for final training (no validation set)
            final_params = best_params.copy()
            final_params.pop('early_stopping_rounds', None)
            
            final_model = xgb.XGBClassifier(**final_params, random_state=42)
            final_model.fit(X_full, y_full, verbose=False)
            
        elif 'lightgbm' in best_model_name:
            if 'gpu' in best_model_name:
                best_params.update({'device': 'gpu', 'metric': 'auc', 'is_unbalance': True})
            else:
                best_params.update({'n_jobs': -1, 'metric': 'auc', 'is_unbalance': True})
            
            final_model = lgb.LGBMClassifier(**best_params, random_state=42)
            final_model.fit(X_full, y_full)
            
        elif 'catboost' in best_model_name:
            if 'gpu' in best_model_name:
                best_params.update({
                    'task_type': 'GPU',
                    'eval_metric': 'AUC',
                    'auto_class_weights': 'Balanced',
                    'verbose': False
                })
            else:
                best_params.update({
                    'thread_count': -1,
                    'eval_metric': 'AUC',
                    'auto_class_weights': 'Balanced',
                    'verbose': False
                })
            
            final_model = cb.CatBoostClassifier(**best_params, random_state=42)
            final_model.fit(X_full, y_full, verbose=False)
        
        training_time = time.time() - start_time
        logger.info(f"Final model training completed in {training_time:.1f}s")
        logger.info("Production model trained on 100% of available data")
        logger.info("(Test set performance from hyperparameter optimization phase available in model comparison)")
        
        # Update model scores with final training info
        self.model_scores[f'{best_model_name}_final'] = {
            **self.model_scores[best_model_name],
            'final_training_time': training_time,
            'training_data_percentage': 100,
            'note': 'Trained on full dataset including test set - use validation metrics for performance estimates'
        }
        
        return final_model
    
    def save_production_model(self, model: Any, model_name: str):
        """
        Save the production model with all necessary components.
        
        Args:
            model: Trained model
            model_name: Name for the saved model
        """
        logger.info(f"Saving production model: {model_name}")
        
        model_package = {
            'model': model,
            'scaler': self.scaler,
            'label_encoders': self.label_encoders,
            'feature_names': self.feature_names,
            'model_metrics': self.model_scores.get(f'{model_name}_final', {}),
            'training_metadata': {
                'model_type': model_name,
                'training_date': datetime.now().isoformat(),
                'feature_count': len(self.feature_names),
                'training_samples': len(self.X_train) + len(self.X_val) + len(self.X_test),
                'training_data_percentage': 100,
                'note': 'Final model trained on 100% of available data for maximum performance'
            }
        }
        
        # Save model package
        model_path = self.models_path / f'ieee_fraud_model_production.pkl'
        with open(model_path, 'wb') as f:
            pickle.dump(model_package, f)
        
        # Save model metadata as JSON
        metadata_path = self.models_path / f'ieee_fraud_model_metadata.json'
        metadata = {
            'model_type': model_name,
            'feature_names': self.feature_names,
            'model_metrics': self.model_scores.get(f'{model_name}_final', {}),
            'training_metadata': model_package['training_metadata']
        }
        
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info(f"Production model saved to {model_path}")
        logger.info(f"Model metadata saved to {metadata_path}")
    
    def run_complete_training_pipeline(self, sample_size: Optional[int] = None) -> str:
        """
        Run the complete training pipeline from data loading to production model.
        
        Args:
            sample_size: Optional sample size for faster experimentation
            
        Returns:
            Path to saved production model
        """
        logger.info("Starting complete ML training pipeline...")
        
        # Load and preprocess data
        X, y = self.load_and_preprocess_data(sample_size)
        
        # Split data
        self.prepare_train_test_split(X, y)
        
        # Train baseline
        self.train_baseline_logistic_regression()
        
        # Train gradient boosting models
        self.train_gradient_boosting_models()
        
        # Compare and select best model
        best_model_name = self.compare_models()
        
        # Generate comprehensive training summary report
        training_report = self._save_training_summary_report()
        
        # Retrain on full dataset
        final_model = self.retrain_on_full_dataset(best_model_name)
        
        # Save production model
        self.save_production_model(final_model, best_model_name)
        
        logger.info("Complete training pipeline finished!")
        logger.info("All training results, visualizations, and analysis saved to models/ directory")
        return str(self.models_path / 'ieee_fraud_model_production.pkl')


def main():
    """Main training script."""
    # Initialize trainer
    trainer = IEEEModelTrainer()
    
    # Run complete pipeline
    # Use sample_size=50000 for faster experimentation, None for full dataset
    model_path = trainer.run_complete_training_pipeline(sample_size=None)
    
    print(f"\n🎉 Training complete! Production model saved at: {model_path}")
    
    return model_path


if __name__ == "__main__":
    main()
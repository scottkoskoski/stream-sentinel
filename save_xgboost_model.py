#!/usr/bin/env python3
"""
Save the superior XGBoost model (0.9697 AUC) from hyperparameter optimization results.
This is a focused script to address the critical issue of the missing production model.
"""

import json
import pickle
import numpy as np
import pandas as pd
import xgboost as xgb
from pathlib import Path
from datetime import datetime
from sklearn.preprocessing import LabelEncoder
from sklearn.feature_selection import SelectKBest, mutual_info_classif

def load_best_params():
    """Load the best XGBoost parameters."""
    convergence_file = Path("models/hyperparameter_results/xgboost_gpu/xgboost_gpu_convergence_stats.json")
    
    with open(convergence_file, 'r') as f:
        convergence_stats = json.load(f)
    
    return convergence_stats['best_params'], convergence_stats['best_value']

def load_and_preprocess_data():
    """Load and preprocess the IEEE-CIS data."""
    print("Loading IEEE-CIS data...")
    
    # Load transaction data
    data = pd.read_csv("data/raw/train_transaction.csv")
    
    # Load identity data if available
    identity_path = Path("data/raw/train_identity.csv")
    if identity_path.exists():
        identity_data = pd.read_csv(identity_path)
        data = data.merge(identity_data, on='TransactionID', how='left')
    
    print(f"Loaded {len(data)} transactions")
    
    # Separate features and target
    y = data['isFraud'].copy()
    X = data.drop(columns=['isFraud', 'TransactionID'])
    
    # Basic feature engineering
    if 'TransactionAmt' in X.columns:
        X['TransactionAmt_log'] = np.log1p(X['TransactionAmt'])
        X['TransactionAmt_decimal'] = X['TransactionAmt'] % 1
    
    # Handle categorical variables
    label_encoders = {}
    categorical_cols = X.select_dtypes(include=['object']).columns
    
    for col in categorical_cols:
        label_encoders[col] = LabelEncoder()
        X[col] = label_encoders[col].fit_transform(X[col].fillna('unknown'))
    
    # Handle missing values
    X = X.fillna(0)
    
    # Feature selection (top 200 features)
    if len(X.columns) > 200:
        print(f"Selecting top 200 features from {len(X.columns)}")
        
        # Convert all to numeric
        X = X.apply(pd.to_numeric, errors='coerce').fillna(0)
        
        selector = SelectKBest(mutual_info_classif, k=200)
        X_selected = selector.fit_transform(X, y)
        feature_names = [X.columns[i] for i in np.where(selector.get_support())[0]]
        
        X = pd.DataFrame(X_selected, columns=feature_names, index=X.index)
    else:
        feature_names = list(X.columns)
    
    print(f"Final dataset: {len(feature_names)} features")
    return X, y, feature_names, label_encoders

def train_and_save_model():
    """Train and save the XGBoost model."""
    print("Starting XGBoost model training and saving...")
    
    # Load best parameters
    best_params, best_auc = load_best_params()
    print(f"Best hyperparameter AUC: {best_auc:.6f}")
    
    # Load and preprocess data
    X, y, feature_names, label_encoders = load_and_preprocess_data()
    
    # Prepare XGBoost parameters
    xgb_params = best_params.copy()
    xgb_params.update({
        'tree_method': 'gpu_hist',
        'gpu_id': 0,
        'eval_metric': 'auc',
        'scale_pos_weight': (y == 0).sum() / (y == 1).sum(),
        'random_state': 42
    })
    
    # Remove early stopping for final training
    xgb_params.pop('early_stopping_rounds', None)
    
    print("Training XGBoost model...")
    start_time = datetime.now()
    
    model = xgb.XGBClassifier(**xgb_params)
    model.fit(X, y, verbose=False)
    
    training_time = (datetime.now() - start_time).total_seconds()
    print(f"Training completed in {training_time:.1f} seconds")
    
    # Create model package
    model_package = {
        'model': model,
        'scaler': None,  # XGBoost doesn't need scaling
        'label_encoders': label_encoders,
        'feature_names': feature_names,
        'model_metrics': {
            'model_type': 'xgboost_gpu',
            'hyperopt_auc': best_auc,
            'training_time': training_time,
            'gpu_accelerated': True,
            'best_params': best_params
        },
        'training_metadata': {
            'model_type': 'xgboost_gpu',
            'training_date': datetime.now().isoformat(),
            'feature_count': len(feature_names),
            'training_samples': len(X),
            'note': f'Superior model from hyperparameter optimization - {best_auc:.6f} AUC'
        }
    }
    
    # Save model
    model_path = Path("models/ieee_fraud_model_production.pkl")
    with open(model_path, 'wb') as f:
        pickle.dump(model_package, f)
    
    # Save metadata  
    metadata = {
        'model_type': 'xgboost_gpu',
        'feature_names': feature_names,
        'model_metrics': model_package['model_metrics'],
        'training_metadata': model_package['training_metadata']
    }
    
    metadata_path = Path("models/ieee_fraud_model_metadata.json")
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"Model saved to: {model_path}")
    print(f"Metadata saved to: {metadata_path}")
    
    # Verify model works
    print("Verifying model...")
    test_features = X.iloc[:5]
    predictions = model.predict_proba(test_features)[:, 1]
    print(f"Test predictions: {predictions}")
    
    print(f"SUCCESS: XGBoost model (AUC: {best_auc:.6f}) saved as production model")
    return model_path

if __name__ == "__main__":
    try:
        model_path = train_and_save_model()
        print(f"Model saved at: {model_path}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
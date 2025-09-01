#!/usr/bin/env python3

import sys
import os
import pickle
import time

# Test basic functionality step by step
print("Testing profiler components...")

# Test 1: Basic imports
try:
    import psutil
    import numpy as np
    print("✓ Basic imports successful")
except Exception as e:
    print(f"✗ Import error: {e}")
    sys.exit(1)

# Test 2: Model loading
model_path = "models/ieee_fraud_model_production.pkl"
try:
    with open(model_path, 'rb') as f:
        model = pickle.load(f)
    print(f"✓ Model loaded from {model_path}")
except Exception as e:
    print(f"✗ Model loading error: {e}")
    sys.exit(1)

# Test 3: Simple inference
try:
    # Generate a simple test feature vector
    test_features = [1.0] * 20  # 20 features matching our expected input
    
    start_time = time.perf_counter()
    prediction = model.predict_proba([test_features])[0][1]
    end_time = time.perf_counter()
    
    inference_time = (end_time - start_time) * 1000  # ms
    
    print(f"✓ Single inference successful")
    print(f"  - Prediction: {prediction:.4f}")
    print(f"  - Inference time: {inference_time:.2f}ms")
    
except Exception as e:
    print(f"✗ Inference error: {e}")
    sys.exit(1)

print("✓ All tests passed - profiler should work correctly")
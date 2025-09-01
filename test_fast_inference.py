#!/usr/bin/env python3

"""
Test FastInferenceEngine integration to validate architecture
"""

import os
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

def test_fast_inference():
    """Test FastInferenceEngine with correct path resolution."""
    
    # Correct model path
    model_path = "models/ieee_fraud_model_production.pkl"
    
    if not os.path.exists(model_path):
        print(f"Model file not found: {model_path}")
        return False
    
    print("Testing FastInferenceEngine integration...")
    
    try:
        from inference.fast_inference import FastInferenceEngine
        
        print("✓ FastInferenceEngine imported successfully")
        
        # Test with C++ disabled first to validate Python path
        engine = FastInferenceEngine(model_path, enable_cpp=False)
        status = engine.get_status()
        
        print(f"Engine status (Python only): {status}")
        
        # Test prediction with correct feature count (model expects 200 features)
        test_features = [1.0] * 200  # Match the model's expected feature count
        prob, info = engine.predict_fraud_probability(test_features)
        
        print(f"Python prediction: {prob:.4f}")
        print(f"Performance info: {info}")
        
        if info['success'] and info['engine'] == 'python':
            print("✓ Python inference path working correctly")
        else:
            print("✗ Python inference path failed")
            return False
        
        # Test with C++ enabled (will fallback to Python since C++ not built)
        engine_cpp = FastInferenceEngine(model_path, enable_cpp=True)
        status_cpp = engine_cpp.get_status()
        
        print(f"Engine status (C++ enabled): {status_cpp}")
        
        prob_cpp, info_cpp = engine_cpp.predict_fraud_probability(test_features)
        print(f"C++ fallback prediction: {prob_cpp:.4f}")
        print(f"C++ fallback info: {info_cpp}")
        
        # Verify predictions are identical (both should use Python)
        if abs(prob - prob_cpp) < 1e-10:
            print("✓ Consistent results between Python and C++ fallback paths")
        else:
            print(f"✗ Inconsistent results: Python={prob:.6f}, C++={prob_cpp:.6f}")
            return False
            
        return True
        
    except Exception as e:
        print(f"✗ FastInferenceEngine test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_fast_inference()
    if success:
        print("\n✅ FastInferenceEngine integration test PASSED")
        exit(0)
    else:
        print("\n❌ FastInferenceEngine integration test FAILED") 
        exit(1)
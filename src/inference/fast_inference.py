#!/usr/bin/env python3

"""
Fast ML Inference Module for Stream-Sentinel Fraud Detection

Provides optional C++ accelerated inference as drop-in replacement for Python XGBoost.
Falls back gracefully to Python implementation if C++ wrapper unavailable.

Integration point: fraud_detector.py:_calculate_ml_fraud_score()
"""

import pickle
import logging
import time
from typing import List, Optional, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class FastInferenceEngine:
    """
    High-performance ML inference engine with C++ acceleration.
    
    Provides seamless drop-in replacement for Python XGBoost inference
    with automatic fallback to ensure system reliability.
    """
    
    def __init__(self, model_path: str, enable_cpp: bool = True):
        """
        Initialize inference engine with optional C++ acceleration.
        
        Args:
            model_path: Path to XGBoost model file
            enable_cpp: Enable C++ wrapper if available (default: True)
        """
        self.model_path = model_path
        self.enable_cpp = enable_cpp
        self.python_model = None
        self.cpp_wrapper = None
        self.using_cpp = False
        
        self._load_models()
    
    def _load_models(self) -> None:
        """Load Python model and optionally C++ wrapper."""
        
        # Always load Python model as fallback
        try:
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
                
            # Extract the actual model from dictionary structure if needed
            if isinstance(model_data, dict):
                self.python_model = model_data.get('model')
                if self.python_model is None:
                    raise ValueError("Model dictionary doesn't contain 'model' key")
                logger.info(f"Extracted XGBoost model from dict structure: {type(self.python_model)}")
            else:
                # Fallback for simple model pickle
                self.python_model = model_data
                logger.info(f"Loaded simple XGBoost model: {type(self.python_model)}")
                
            logger.info(f"Python XGBoost model loaded successfully from {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to load Python model: {e}")
            raise
        
        # Try to load C++ wrapper if enabled
        if self.enable_cpp:
            try:
                import os
                import sys
                
                # Set up environment for C++ wrapper
                cpp_dir = Path(__file__).parent / "cpp"
                # Derive xgboost lib dir from the installed package location,
                # or allow override via XGBOOST_LIB_DIR environment variable
                xgboost_lib_dir = os.environ.get("XGBOOST_LIB_DIR", "")
                if not xgboost_lib_dir:
                    try:
                        import xgboost as _xgb
                        xgboost_lib_dir = str(Path(_xgb.__file__).parent / "lib")
                    except ImportError:
                        xgboost_lib_dir = ""
                
                # Add C++ extension to Python path
                if str(cpp_dir) not in sys.path:
                    sys.path.insert(0, str(cpp_dir))
                
                # Set LD_LIBRARY_PATH for XGBoost shared library
                current_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
                if xgboost_lib_dir not in current_ld_path:
                    os.environ['LD_LIBRARY_PATH'] = f"{xgboost_lib_dir}:{current_ld_path}"
                
                import simple_xgboost_cpp
                self.cpp_wrapper = simple_xgboost_cpp.SimpleXGBoostWrapper()
                
                # Use C++ compatible model file
                cpp_model_path = self.model_path.replace('.pkl', '_cpp.json')
                if not Path(cpp_model_path).exists():
                    # Try alternative naming
                    cpp_model_path = self.model_path.replace('_production.pkl', '_cpp.json')
                    
                if Path(cpp_model_path).exists():
                    if self.cpp_wrapper.load_model(cpp_model_path):
                        self.using_cpp = True
                        logger.info(f"C++ XGBoost wrapper loaded successfully using model: {cpp_model_path}")
                    else:
                        error = self.cpp_wrapper.get_last_error()
                        logger.warning(f"C++ wrapper failed to load model: {error}")
                        logger.warning("Falling back to Python implementation")
                        self.cpp_wrapper = None
                else:
                    logger.warning(f"C++ model file not found: {cpp_model_path}")
                    logger.warning("Run export_model_for_cpp.py to create C++ compatible model")
                    logger.warning("Falling back to Python implementation")
                    self.cpp_wrapper = None
                    
            except ImportError as e:
                logger.info(f"C++ wrapper not available: {e}")
            except Exception as e:
                logger.warning(f"C++ wrapper initialization failed: {e}")
                logger.warning("Falling back to Python implementation")
                self.cpp_wrapper = None
    
    def predict_fraud_probability(self, features: List[float]) -> Tuple[float, dict]:
        """
        Predict fraud probability with performance metrics.
        
        Args:
            features: Feature vector for inference
            
        Returns:
            Tuple of (fraud_probability, performance_info)
        """
        start_time = time.perf_counter()
        
        # Try C++ inference first if available
        if self.using_cpp and self.cpp_wrapper:
            try:
                probability = self.cpp_wrapper.predict(features)
                
                if probability >= 0.0:  # Valid prediction
                    inference_time = (time.perf_counter() - start_time) * 1000  # ms
                    
                    return probability, {
                        'engine': 'cpp',
                        'inference_time_ms': inference_time,
                        'success': True
                    }
                else:
                    # C++ prediction failed, log error and fall back
                    error = self.cpp_wrapper.get_last_error()
                    logger.warning(f"C++ inference failed: {error}, falling back to Python")
                    
            except Exception as e:
                logger.warning(f"C++ inference error: {e}, falling back to Python")
        
        # Python fallback inference
        try:
            # This mirrors the exact call in fraud_detector.py
            probability = self.python_model.predict_proba([features])[0][1]
            inference_time = (time.perf_counter() - start_time) * 1000  # ms
            
            return float(probability), {
                'engine': 'python',
                'inference_time_ms': inference_time,
                'success': True
            }
            
        except Exception as e:
            inference_time = (time.perf_counter() - start_time) * 1000  # ms
            logger.error(f"Both C++ and Python inference failed: {e}")
            
            return 0.5, {  # Default to medium risk on failure
                'engine': 'error',
                'inference_time_ms': inference_time,
                'success': False,
                'error': str(e)
            }
    
    def get_status(self) -> dict:
        """Get current engine status and configuration."""
        return {
            'model_path': self.model_path,
            'using_cpp': self.using_cpp,
            'cpp_available': self.cpp_wrapper is not None,
            'python_available': self.python_model is not None,
            'enable_cpp': self.enable_cpp
        }


# Convenience function for existing fraud_detector.py integration
def create_fast_inference_engine(model_path: str, enable_cpp: bool = True) -> FastInferenceEngine:
    """
    Factory function to create FastInferenceEngine.
    
    This function can be imported and used as a drop-in replacement
    in fraud_detector.py without major code changes.
    """
    return FastInferenceEngine(model_path, enable_cpp)


# Legacy compatibility wrapper
class CompatibleMLModel:
    """
    Wrapper that provides exactly the same interface as the current
    Python XGBoost model used in fraud_detector.py
    """
    
    def __init__(self, model_path: str):
        self.engine = FastInferenceEngine(model_path)
    
    def predict_proba(self, features_list: List[List[float]]) -> List[List[float]]:
        """
        Provide identical interface to Python XGBoost predict_proba.
        
        This allows direct replacement in fraud_detector.py with minimal changes.
        """
        results = []
        
        for features in features_list:
            prob, _ = self.engine.predict_fraud_probability(features)
            # Return in same format as XGBoost: [[prob_class_0, prob_class_1]]
            results.append([1.0 - prob, prob])
        
        return results


if __name__ == "__main__":
    # Simple test when run directly
    import os
    import sys
    
    model_path = "../../models/ieee_fraud_model_production.pkl"
    
    if not os.path.exists(model_path):
        print(f"Model file not found: {model_path}")
        sys.exit(1)
    
    print("Testing FastInferenceEngine...")
    
    engine = FastInferenceEngine(model_path)
    print(f"Engine status: {engine.get_status()}")
    
    # Test with dummy features
    test_features = [1.0] * 20
    prob, info = engine.predict_fraud_probability(test_features)
    
    print(f"Test prediction: {prob:.4f}")
    print(f"Performance info: {info}")
    print("FastInferenceEngine test complete")
#!/usr/bin/env python3

import sys
import os

# Try to run the profiler and capture any errors
print("Running ML inference profiler for baseline measurement...")

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'benchmarks'))
    from ml_inference_profiler import MLInferenceProfiler
    
    profiler = MLInferenceProfiler()
    
    if profiler.model is None:
        print("ERROR: Model could not be loaded")
        sys.exit(1)
    
    print("Model loaded successfully, running baseline profiling...")
    
    # Run a quick test first
    test_features = [1.0] * 20
    result = profiler.profile_single_inference(test_features)
    
    if 'error' in result:
        print(f"Error in inference: {result['error']}")
        sys.exit(1)
    
    print(f"Single inference test successful: {result['measurement']['wall_time_ms']:.2f}ms")
    
    # Run full baseline report
    report = profiler.generate_baseline_report()
    
    if 'error' in report:
        print(f"Error generating baseline: {report['error']}")
        sys.exit(1)
    
    # Save report
    baseline_file = "ml_inference_baseline.json"
    profiler.save_baseline_report(baseline_file)
    
    print("\nBaseline profiling completed successfully!")
    
except Exception as e:
    print(f"Profiler execution failed: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
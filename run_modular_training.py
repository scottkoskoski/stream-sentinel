#!/usr/bin/env python3
"""
Execute the superior modular training pipeline on IEEE-CIS dataset.

This script uses the production-grade modular training architecture
instead of the deprecated monolithic ieee_model_trainer.py
"""

import sys
import json
from pathlib import Path

# Add src to path for imports
sys.path.append('src')

from ml.training import create_training_pipeline


def main():
    """Execute the modular training pipeline."""
    print("🚀 Executing MODULAR training pipeline (superior architecture)")
    print("📊 Using IEEE-CIS dataset for production-grade fraud detection model")
    
    try:
        # Create the modular pipeline with production environment
        print("⚙️  Initializing modular training components...")
        pipeline = create_training_pipeline(environment="production")
        
        # Execute the complete pipeline
        print("🔄 Running complete modular pipeline...")
        result = pipeline.run(model_types=['xgboost'])
        
        # Display results
        print("\n✅ MODULAR PIPELINE EXECUTION COMPLETE")
        print("=" * 50)
        
        # Extract key metrics
        if "model_performance" in result:
            best_score = result["model_performance"].get("best_score", "N/A")
            print(f"🎯 Best Model AUC Score: {best_score}")
            
            if isinstance(best_score, (int, float)) and best_score >= 0.96:
                print("🌟 SUCCESS: AUC >= 0.96 threshold maintained!")
            else:
                print("⚠️  AUC below 0.96 threshold - requires investigation")
        
        # Execution summary
        if "execution_summary" in result:
            summary = result["execution_summary"]
            print(f"📋 Pipeline State: {summary.get('final_state', 'Unknown')}")
            print(f"⏱️  Total Duration: {summary.get('total_duration', 'N/A')}s")
        
        # Stage durations
        if "stage_durations" in result:
            print("\n📊 Stage Performance:")
            for stage, duration in result["stage_durations"].items():
                print(f"  {stage}: {duration:.2f}s")
        
        # Save results
        results_file = Path("models/modular_training_results.json")
        results_file.parent.mkdir(exist_ok=True)
        
        with open(results_file, 'w') as f:
            json.dump(result, f, indent=2, default=str)
        
        print(f"\n💾 Results saved to: {results_file}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ MODULAR PIPELINE FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
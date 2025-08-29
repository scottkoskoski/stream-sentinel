#!/usr/bin/env python3
"""
Simple ONNX Export Test

Quick validation of the ONNX export functionality without complex benchmarking.
"""

import sys
import numpy as np
from pathlib import Path

# Add src to path
sys.path.append('src')

from ml.serving.model_export import ModelExporter, ExportConfig
from ml.training.core.checkpoint_manager import CheckpointManager

def test_simple_export():
    """Test basic ONNX export functionality."""
    print("🧪 Simple ONNX Export Test")
    print("=" * 50)
    
    # Load trained model
    print("📦 Loading trained model...")
    checkpoint_manager = CheckpointManager({
        "checkpoint_dir": "models/checkpoints",
        "retention_hours": 168
    })
    
    best_checkpoint = checkpoint_manager.load_best_checkpoint()
    if best_checkpoint is None:
        print("❌ No trained model found")
        return False
    
    print(f"✅ Loaded model: Trial {best_checkpoint.trial_number}, AUC = {best_checkpoint.score:.4f}")
    
    # Generate simple validation data
    print("🔢 Generating validation data...")
    np.random.seed(42)
    X_validation = np.random.randn(200, 200).astype(np.float32)
    print(f"✅ Generated data shape: {X_validation.shape}")
    
    # Configure export with basic settings
    print("⚙️  Configuring export...")
    export_config = ExportConfig(
        target_opset=14,
        accuracy_tolerance=1e-6,
        validation_samples=100,  # Minimum required
        output_dir="models/onnx_test",
        model_name="simple_test",
        generate_test_cases=False,  # Skip to speed up
        enable_graph_optimization=False  # Skip to speed up
    )
    
    # Attempt export
    print("🔄 Attempting ONNX export...")
    try:
        exporter = ModelExporter(export_config)
        onnx_path, export_result = exporter.export_to_onnx(
            best_checkpoint.model,
            model_name="simple_test",
            X_validation=X_validation
        )
        
        print("✅ Export successful!")
        print(f"📁 ONNX model saved to: {onnx_path}")
        print(f"🎯 Max absolute error: {export_result.max_absolute_error:.2e}")
        print(f"📊 Correlation: {export_result.correlation_coefficient:.6f}")
        print(f"🤝 Decision agreement: {export_result.decision_agreement_rate:.4f}")
        
        # Validate file exists
        if Path(onnx_path).exists():
            file_size = Path(onnx_path).stat().st_size / (1024 * 1024)
            print(f"📦 File size: {file_size:.1f}MB")
            
        success = export_result.conversion_successful and export_result.accuracy_validated
        print(f"🏆 Overall success: {'✅ YES' if success else '❌ NO'}")
        
        return success
        
    except Exception as e:
        print(f"❌ Export failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_simple_export()
    exit(0 if success else 1)
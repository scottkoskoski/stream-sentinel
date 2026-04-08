# /stream-sentinel/src/ml/features/__init__.py

"""
Feature Engineering Module for Stream-Sentinel

Provides unified feature computation for both offline training (batch)
and online inference (streaming) contexts.
"""

from .feature_engineer import FeatureEngineer, FeatureConfig

__all__ = ["FeatureEngineer", "FeatureConfig"]

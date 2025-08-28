# Data Analysis Pipeline

*This guide explains Stream-Sentinel's data analysis pipeline, from IEEE-CIS dataset exploration to synthetic transaction generation.*

## Coming Soon

This section will cover:

- **IEEE-CIS Dataset Analysis**: Statistical exploration and pattern identification
- **Synthetic Data Generation**: Realistic transaction generation for testing
- **Data Quality**: Validation and consistency checking
- **Performance Testing**: Load generation and system validation

## Current Implementation

The data analysis pipeline is currently implemented in:
- **`src/data/analysis/ieee_cis_analyzer.py`** - Dataset analysis and statistics
- **`src/producers/synthetic_transaction_producer.py`** - High-throughput data generation
- **`data/processed/ieee_cis_analysis.json`** - Analysis results and patterns

## Key Insights

Current analysis reveals:
- **590,540+ transactions** with 394 features analyzed
- **2.71% fraud rate** matching real-world financial data
- **Peak fraud at 8:00 AM** (6.16% vs 2.71% baseline)
- **Small transactions (<$10)** show highest fraud rates (5.08%)

---

**Navigation:** [← Documentation Index](../README.md) | [IEEE Analysis Results](../../data/processed/ieee_cis_analysis.json)
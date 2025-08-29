# Performance Benchmark Report: ieee_fraud_onnx

**Test Date**: 2025-08-29 18:13:53

## Single-Threaded Performance

- **Mean Latency**: 552.52ms
- **P95 Latency**: 574.83ms
- **P99 Latency**: 599.65ms
- **Throughput**: 1.5 predictions/second
- **Error Rate**: 0.0000

## Concurrent Performance

### 2 Threads
- **Mean Latency**: 1022.98ms
- **Throughput**: 1.7 predictions/second
- **Error Rate**: 0.0000

### 4 Threads
- **Mean Latency**: 1819.82ms
- **Throughput**: 1.7 predictions/second
- **Error Rate**: 0.0000

### 8 Threads
- **Mean Latency**: 3589.14ms
- **Throughput**: 1.6 predictions/second
- **Error Rate**: 0.0000

## Load Test Results

- **Target Throughput**: 500 RPS
- **Achieved Throughput**: 1.4 RPS
- **Mean Latency**: 1977.14ms
- **P95 Latency**: 2829.19ms
- **Error Rate**: 0.0000

## Stress Test Results

- **Stress Test Passed**: ❌ No
- **Achieved Throughput**: 1.5 RPS
- **Mean Latency**: 2057.10ms
- **Error Rate**: 0.0000

## Performance Comparison

- **Latency Improvement**: 0.10x
- **Throughput Improvement**: 0.10x
- **Memory Efficiency**: 4.86x
- **Regression Detected**: ❌ Yes


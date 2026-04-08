# Stream Sentinel Documentation

Architecture guides, implementation details, and learning resources for the Stream Sentinel fraud detection system.

## Architecture & Components

| Guide | What it covers |
|-------|---------------|
| [Infrastructure](infrastructure/README.md) | Docker Compose services, Kafka cluster, Redis, PostgreSQL, ClickHouse |
| [Stream Processing](stream-processing/README.md) | Kafka producers, consumers, partitioning, offset management |
| [Fraud Detection](fraud-detection/README.md) | ML scoring pipeline, feature engineering, blocking enforcement |
| [Alert Response](alert-response/README.md) | Severity classification, automated actions, user blocking, SLA tracking |
| [State Management](state-management/README.md) | Redis patterns for user profiles, blocked users, drift baselines |
| [Data Persistence](data-persistence/README.md) | PostgreSQL (OLTP) + ClickHouse (OLAP) hybrid architecture |
| [Data Analysis](data-analysis/README.md) | IEEE-CIS dataset analysis, synthetic data generation |
| [Machine Learning](machine-learning/README.md) | Training pipeline, hyperparameter optimization, model export |

## ML & Online Learning

| Guide | What it covers |
|-------|---------------|
| [Online Learning System](../src/ml/online_learning/README.md) | Drift detection, model registry, A/B testing, feedback processing |

## Learning Resources

| Guide | What it covers |
|-------|---------------|
| [Apache Kafka](learning/kafka.md) | Event streaming concepts, partitioning, producers/consumers |
| [Redis](learning/redis.md) | In-memory data structures, caching, pub/sub patterns |

## Development Journal

| Guide | What it covers |
|-------|---------------|
| [Project Logs](project-logs/README.md) | Phase-by-phase implementation history and design decisions |

## Reading Order

**New to the project:**
1. [Main README](../README.md) -- architecture diagrams and quick start
2. [Infrastructure](infrastructure/README.md) -- understand the service stack
3. [Fraud Detection](fraud-detection/README.md) -- the core scoring pipeline

**Deep dive into ML:**
1. [Machine Learning](machine-learning/README.md) -- training pipeline
2. [Online Learning](../src/ml/online_learning/README.md) -- drift, registry, A/B testing

**Understanding the data flow:**
1. [Stream Processing](stream-processing/README.md) -- Kafka patterns
2. [State Management](state-management/README.md) -- Redis usage
3. [Data Persistence](data-persistence/README.md) -- where results land

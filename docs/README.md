# Stream Sentinel Documentation

Architecture guides, implementation details, and learning resources for the Stream Sentinel fraud detection system.

## Architecture & Components

| Guide | What it covers |
|-------|---------------|
| [Infrastructure](infrastructure/README.md) | Docker Compose services, Kafka cluster, Redis, PostgreSQL, ClickHouse, Kubernetes/Helm |
| [Stream Processing](stream-processing/README.md) | Kafka producers, consumers, partitioning, offset management, distributed tracing |
| [Fraud Detection](fraud-detection/README.md) | ML scoring pipeline, feature engineering, blocking enforcement, ModelRegistry, A/B testing |
| [Alert Response](alert-response/README.md) | Severity classification, automated actions, user blocking, SLA tracking |
| [State Management](state-management/README.md) | Redis patterns for user profiles, blocked users, drift baselines, model registry |
| [Data Persistence](data-persistence/README.md) | PostgreSQL (OLTP) + ClickHouse (OLAP) hybrid architecture |
| [Data Analysis](data-analysis/README.md) | IEEE-CIS dataset analysis, synthetic data generation |
| [Machine Learning](machine-learning/README.md) | Training pipeline, F2-score optimization, hyperparameter tuning, model export |

## Operational Runbooks

Production on-call references. See [runbooks/README.md](runbooks/README.md) for the full index.

| Runbook | What it covers |
|---------|---------------|
| [Incident Response](runbooks/incident-response.md) | SEV1-SEV4 classification, escalation matrix, IC checklist, post-mortems |
| [Alert Response](runbooks/alert-response.md) | Per-alert diagnosis/fix/verify for 15+ Prometheus alerts |
| [Disaster Recovery](runbooks/disaster-recovery.md) | Kafka/Redis/PostgreSQL/ClickHouse recovery with RTO/RPO targets |
| [Scaling](runbooks/scaling.md) | Horizontal (partition-aware) and vertical scaling procedures |
| [Model Operations](runbooks/model-operations.md) | Deploy, rollback, retrain, A/B test, drift investigation |
| [Troubleshooting](runbooks/troubleshooting.md) | Common issues with diagnostic commands |
| [Capacity Planning](runbooks/capacity-planning.md) | Resource estimation and growth projections |

## Performance Reports

| Report | What it covers |
|--------|---------------|
| [Model Performance](model-performance-report.md) | Training metrics, feature importance, inference benchmarks, training-production gap analysis |
| [System Benchmarks](system-benchmarks-report.md) | Producer/consumer throughput, end-to-end latency, data quality, resource utilization |
| [Synthetic Data Validation](../data/SYNTHETIC_DATA_VALIDATION.md) | Distribution comparison vs IEEE-CIS, feature compatibility, fraud rate calibration |

## ML & Online Learning

| Guide | What it covers |
|-------|---------------|
| [Online Learning System](../src/ml/online_learning/README.md) | Drift detection, model registry, A/B testing, feedback processing |

## Deployment

| Location | What it covers |
|----------|---------------|
| [Kubernetes manifests](../k8s/) | Namespace, ConfigMap, Secrets, consumer Deployments, HPA, Prometheus + Grafana |
| [Helm chart](../helm/stream-sentinel/) | Templated manifests with configurable `values.yaml` |
| [Dockerfile.consumer](../docker/Dockerfile.consumer) | Multi-stage, non-root consumer image |
| [Prometheus alert rules](../docker/prometheus/alert_rules.yml) | 16+ production alert rules |
| [CI/CD pipelines](../.github/workflows/) | `ci.yml` (test/lint/build), `performance.yml`, `security.yml` |
| [Model deployment CLI](../scripts/deploy_model.py) | `register`, `promote`, `rollback`, `ab-test`, `status` |

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
3. [Model Operations Runbook](runbooks/model-operations.md) -- deployment and rollback

**Understanding the data flow:**
1. [Stream Processing](stream-processing/README.md) -- Kafka patterns
2. [State Management](state-management/README.md) -- Redis usage
3. [Data Persistence](data-persistence/README.md) -- where results land

**On-call / production operations:**
1. [Runbooks Index](runbooks/README.md) -- quick reference for ports, topics, consumer groups
2. [Incident Response](runbooks/incident-response.md) -- classification and escalation
3. [Alert Response](runbooks/alert-response.md) -- per-alert remediation
4. [Troubleshooting](runbooks/troubleshooting.md) -- common issues

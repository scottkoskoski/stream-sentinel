# Alert Response System

The alert processor (`src/consumers/alert_processor.py`) consumes fraud alerts from the `fraud-alerts` Kafka topic, classifies severity, executes response actions, tracks SLA compliance, and maintains user risk profiles. It is the enforcement layer that closes the loop with the fraud detection pipeline.

## Processing Pipeline

```
fraud-alerts (Kafka)
        |
        v
  Severity classification
  (score + risk factors)
        |
        v
  Response action selection
        |
        v
  Action execution
  (block, investigate, review, notify, log)
        |
        v
  User risk profile update (Redis)
        |
        v
  Audit trail record
        |
        v
  SLA compliance check
```

## Severity Classification

Severity is determined by the fraud score combined with contextual risk factors:

| Severity | Condition | SLA Target |
|----------|-----------|------------|
| **CRITICAL** | Score >= 0.9 | 1 second |
| **HIGH** | Score >= 0.7 with additional risk factors (high amount, rapid velocity, unusual hour) | 5 seconds |
| **MEDIUM** | Score >= 0.4 with fraud indicators present | 30 seconds |
| **LOW** | All other alerts below the above thresholds | 5 minutes |

Risk factors that escalate severity include high transaction amounts, rapid transaction velocity, unusual hours (2-4 AM peak fraud window), and amount deviation from user history.

## Response Actions

Each severity level maps to one or more response actions:

| Action | Severity | Behavior |
|--------|----------|----------|
| `IMMEDIATE_BLOCK` | CRITICAL | Adds user to Redis `blocked_users` set with 24h TTL. The fraud detector enforces this on subsequent transactions via SISMEMBER check, skipping scoring entirely. |
| `AUTO_INVESTIGATE` | HIGH | Triggers automated analysis of the user's transaction history and patterns. |
| `MANUAL_REVIEW` | HIGH | Queues the alert for human investigation with a complete context package. |
| `NOTIFY_TEAM` | MEDIUM | Sends notifications to the fraud operations team. |
| `LOG_ONLY` | LOW | Records the alert for statistical tracking and pattern analysis. |

### Blocking Enforcement Loop

The blocking mechanism creates a closed feedback loop between the alert processor and the fraud detector:

1. Alert processor receives a CRITICAL-severity fraud alert.
2. Alert processor adds the user ID to the Redis `blocked_users` set with a 24-hour TTL.
3. On subsequent transactions, the fraud detector checks `blocked_users` via SISMEMBER before scoring.
4. If the user is found in the set, the transaction is emitted to the `blocked-transactions` topic and scoring is skipped entirely.

The 24-hour TTL provides automatic unblocking. Manual unblocking is also possible by removing the user from the Redis set.

## User Risk Profiling

The alert processor maintains a risk profile for each user in Redis, tracking escalation over time:

- **Alert count**: Total number of fraud alerts generated for the user.
- **Confirmed fraud count**: Number of alerts later confirmed as actual fraud.
- **Risk level escalation**: Risk level increases based on alert frequency and confirmation rate.
- **Profile TTL**: 90-day expiration on risk profile data.

Risk profiles inform severity classification -- a user with a history of confirmed fraud will have alerts escalated more aggressively than a first-time alert.

## Audit Trail

Every alert processed generates a complete audit record containing:

- Original alert data (fraud score, transaction details, risk factors)
- Severity classification and reasoning
- Response action taken
- Execution timestamp and processing duration
- SLA compliance status (met or violated, with margin)
- User risk profile state at time of processing

The audit trail provides a full response history for compliance review and operational analysis.

## SLA Tracking

The alert processor monitors response times against SLA targets for each severity level. Metrics tracked include:

- Mean response time by severity
- SLA compliance rate (percentage of alerts processed within target)
- SLA violation count and details
- P50, P95, P99 response time distributions

SLA violations are logged and surfaced through Prometheus metrics (exposed on port 8001).

## Observability

The alert processor exposes Prometheus metrics on port 8001, including:

- Alerts processed (counter, by severity)
- Users blocked (counter)
- Response time histogram (by severity)
- SLA violations (counter, by severity)
- Active alerts in processing (gauge)

Structured JSON logging via `src/utils/logging.py` includes contextual fields: `alert_id`, `user_id`, `severity`, `action`, and processing timestamps.

## Kafka Integration

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `fraud-alerts` | Input | Fraud alerts from the detection pipeline |
| `fraud-detection-results` | Output | Enriched results with severity and action metadata |

## Running

```bash
python src/consumers/alert_processor.py
```

Configuration is managed through environment variables (see `.env.example`) and the centralized Kafka config in `src/kafka/config.py`.

## Related Documentation

- [Fraud Detection Pipeline](../fraud-detection/README.md) -- Upstream detection and blocking enforcement
- [State Management](../state-management/README.md) -- Redis patterns for blocked users and risk profiles

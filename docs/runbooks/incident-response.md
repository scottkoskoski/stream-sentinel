# Incident Response Runbook

## Severity Classification

### SEV1 -- Critical

**Definition:** Complete system outage. No transactions are being scored. Fraud is passing undetected.

**Indicators:**
- `transactions_processed_total` rate drops to zero
- `component_health_status{component_name="fraud-detector"} == 0`
- Kafka consumer lag growing unboundedly on `synthetic-transactions`
- All consumers are down or unreachable

**Response Time:** Immediate. Page on-call within 5 minutes.

**Examples:**
- Kafka cluster fully down
- Redis completely unavailable and no graceful degradation
- All fraud detector instances crashed
- Network partition isolating the processing tier

---

### SEV2 -- High

**Definition:** Degraded fraud detection. System is partially operational but accuracy, throughput, or coverage is materially impacted.

**Indicators:**
- `model_status_info{status="rules_fallback"} == 1` (ML model unavailable, rules-only scoring)
- `fraud_detection_duration_seconds` P99 exceeds 500ms (target: <100ms)
- `kafka_consumer_lag_messages` exceeding 100,000 on any partition
- `fraud_model_drift_psi` exceeding 0.25 (high drift severity)
- One or more consumers are down but others remain operational

**Response Time:** Acknowledge within 15 minutes. Begin remediation within 30 minutes.

**Examples:**
- ML model failed to load, scoring via rules fallback
- Significant model drift detected (PSI > 0.25)
- Consumer lag growing beyond recovery at current throughput
- PostgreSQL down, alerts not being persisted
- One of the three Kafka brokers (production) is offline

---

### SEV3 -- Medium

**Definition:** Minor degradation. All core functions operational but with reduced performance or monitoring gaps.

**Indicators:**
- `fraud_detection_duration_seconds` P99 between 100ms and 500ms
- `kafka_consumer_lag_messages` between 10,000 and 100,000
- `fraud_model_drift_psi` between 0.15 and 0.25 (medium drift)
- Dead letter queue volume spiking
- Prometheus or Grafana down (monitoring blind spot)

**Response Time:** Acknowledge within 1 hour. Fix within next business day.

**Examples:**
- Model drift detected at medium severity
- ClickHouse down (analytics impacted, not core scoring)
- Elevated DLQ volume from malformed messages
- Prometheus scrape failures on one consumer

---

### SEV4 -- Low

**Definition:** Minor issue with no user-facing impact. Informational.

**Indicators:**
- `false_positive_rate` slightly elevated
- Consumer lag present but stable and within bounds (<10,000)
- Redis cache miss rate elevated but operations succeed
- Schema Registry unreachable (JSON fallback active)

**Response Time:** Track in issue tracker. Address within current sprint.

**Examples:**
- Schema Registry down (system falls back to JSON, no scoring impact)
- Redis Insight UI unavailable
- Kafka UI unreachable
- Non-critical log warnings increasing

---

## Escalation Matrix

| Severity | First Responder | Escalation (15 min) | Escalation (1 hr) | Executive Notification |
|----------|----------------|---------------------|-------------------|----------------------|
| SEV1 | On-call engineer | Engineering lead + SRE lead | VP Engineering | Yes, within 30 min |
| SEV2 | On-call engineer | Engineering lead | SRE lead | If not resolved in 2 hrs |
| SEV3 | On-call engineer | Team lead (next standup) | -- | No |
| SEV4 | On-call engineer | -- | -- | No |

## Incident Commander Checklist

### SEV1/SEV2 Incidents

1. **Acknowledge** the incident in the alerting system
2. **Declare** severity level in the incident channel
3. **Assign roles:**
   - Incident Commander (IC): coordinates response
   - Technical Lead: drives investigation and fix
   - Communications Lead: stakeholder updates
4. **Open a war room** (video call or dedicated chat channel)
5. **Check system status** -- run the quick diagnostic:

```bash
# Quick health check across all consumers
for port in 8000 8001 8002 8003; do
  echo "--- Port $port ---"
  curl -s --max-time 3 http://localhost:$port/health || echo "UNREACHABLE"
done

# Check Kafka broker health
docker exec stream-sentinel-kafka kafka-broker-api-versions \
  --bootstrap-server localhost:9092 2>&1 | head -5

# Check Redis
redis-cli -p 6379 ping

# Check PostgreSQL
docker exec stream-sentinel-postgres pg_isready -U stream_sentinel_user -d stream_sentinel

# Check ClickHouse
curl -s "http://localhost:8123/ping"

# Check consumer lag
docker exec stream-sentinel-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group fraud-detection-group
```

6. **Communicate** initial assessment to stakeholders
7. **Execute** the relevant alert-response or troubleshooting runbook
8. **Document** actions taken in the incident timeline
9. **Resolve** and communicate resolution
10. **Schedule post-mortem** within 48 hours for SEV1, 1 week for SEV2

---

## Post-Mortem Template

```markdown
# Post-Mortem: [Incident Title]

**Date:** YYYY-MM-DD
**Severity:** SEVx
**Duration:** HH:MM (from detection to resolution)
**Incident Commander:** [Name]
**Author:** [Name]

## Summary
[1-2 sentence description of what happened and the impact]

## Impact
- **Users affected:** [number/scope]
- **Transactions unscored:** [count or estimate]
- **Duration of impact:** [time window]
- **Financial exposure:** [estimated undetected fraud during the window]
- **Data loss:** [any permanent data loss]

## Timeline (all times UTC)
| Time | Event |
|------|-------|
| HH:MM | [First anomaly observed / alert fired] |
| HH:MM | [On-call acknowledged] |
| HH:MM | [Root cause identified] |
| HH:MM | [Fix deployed] |
| HH:MM | [System fully recovered] |

## Root Cause
[Detailed description of what caused the incident]

## Detection
- How was the incident detected? (Alert / manual observation / customer report)
- How long between the start of impact and detection?
- What alerts fired? What alerts should have fired but did not?

## Resolution
[Step-by-step description of what was done to resolve the incident]

## Lessons Learned
### What went well
- [Item]

### What went poorly
- [Item]

### Where we got lucky
- [Item]

## Action Items
| Action | Owner | Priority | Due Date | Ticket |
|--------|-------|----------|----------|--------|
| [Action item] | [Name] | P1/P2/P3 | YYYY-MM-DD | [Link] |

## Appendix
[Relevant logs, graphs, screenshots]
```

---

## Communication Templates

### Initial Notification (SEV1/SEV2)

```
INCIDENT: [SEV level] - [Brief description]
IMPACT: [What is affected]
STATUS: Investigating
NEXT UPDATE: [time, typically 30 min for SEV1, 1 hr for SEV2]
IC: [Name]
```

### Ongoing Update

```
INCIDENT UPDATE: [SEV level] - [Brief description]
STATUS: [Investigating / Mitigating / Monitoring]
CURRENT STATE: [What we know]
ACTIONS: [What is being done]
NEXT UPDATE: [time]
```

### Resolution

```
INCIDENT RESOLVED: [SEV level] - [Brief description]
DURATION: [total time]
ROOT CAUSE: [1 sentence]
RESOLUTION: [1 sentence]
POST-MORTEM: Scheduled for [date]
```

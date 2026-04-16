# Recommended Next Steps

A prioritized list of follow-up work captured at the end of the
2026-04-16 hardening session. Each item includes a brief rationale so
future contributors can decide whether it still matters.

## Priority 1 — Ship-blockers / known production risks

### 1. Retrain the production model on corrected synthetic data

**Status:** Not started.

**Why:** The current production model (`models/synthetic_fraud_model_production.pkl`)
was trained on synthetic data from *before* the C-feature inflation fix
(commit `b15f591`) and the D-feature time-delta bootstrap fix. The
training set had C3 values ~3300x higher than IEEE-CIS (mean 197.67 vs
0.06) and D1/D3/D11/D13 values ~0 instead of ~100-170 days. The
streaming producer now emits values that match IEEE-CIS, so at
production-inference time the model sees a distribution it never
trained on. The 99.42% production AUC claim was measured on the *old*
distribution.

**Fix:** Regenerate a training set from the current producer (or use
real IEEE-CIS directly), then run
`python -m src.ml.training.core.pipeline_orchestrator`. The pipeline
auto-registers the new model to the Redis ModelRegistry and the
streaming detector will hot-swap within 60s.

**Mitigation in the meantime:** Watch `fraud_model_drift_psi` (exposed
by `src/ml/online_learning/live_drift_monitor.py`) during any
production-style run. If PSI crosses the alert rule threshold
(`docker/prometheus/alert_rules.yml`), scoring is unreliable and the
retrain becomes urgent.

### 2. End-to-end verification of the consumer Docker image

**Status:** Not started.

**Why:** `docker/Dockerfile.consumer` now compiles the C++ extension
during the builder stage and uninstalls pybind11 before the runtime
image is promoted (commit `fe4585b`). None of this has been exercised
end-to-end -- if `make` fails in the slim base image, or if
`pip uninstall -y pybind11` doesn't exit cleanly, CI and deployments
will break on the next PR open.

**Fix:** `docker build -f docker/Dockerfile.consumer -t test .`; verify
the produced image contains `src/inference/cpp/simple_xgboost_cpp.*.so`
and does **not** contain `pybind11` in `/opt/venv`; run the consumer
against a compose stack and confirm the startup log shows
`C++ accelerated inference engine loaded successfully`.

### 3. Measure actual producer TPS with the current configuration

**Status:** Claims in docs are *estimated*, not measured since the
2026-04-16 changes.

**Why:** The producer's Kafka tuning (`linger.ms` 5 → 50, 1 MiB batch,
LZ4 compression) and the `np.random.*` → `random.*` scalar swap both
landed in commit `3294063` but `THROUGHPUT_REPORT.md` still reports
the 949 TPS baseline from before those changes.

**Fix:** `python scripts/verify_throughput.py` (or its equivalent) with
Kafka running; compare against the 949 TPS baseline and update
`scripts/THROUGHPUT_REPORT.md`.

## Priority 2 — Correctness / observability gaps

### 4. Unify the two `AlertSeverity` enums

**Status:** Defended-in-depth but not fixed at the root.

**Why:** `src/consumers/alert_processor.py::AlertSeverity` uses
lower-case values (JSON-facing) and
`src/persistence/schemas.py::AlertSeverity` uses upper-case (DB-facing).
The DB insert path is now defensive (commits `870fcd8`, `d0b8830`),
but anywhere else that constructs a string or dict from the
alert_processor enum can still produce wrong-case data that leaks into
a system expecting the DB convention (e.g., a metrics label, an alert
name, an audit search).

**Fix options:**
- Consolidate to a single enum in a new `src/shared/severity.py`. Give
  it both canonical (`HIGH`) and JSON-facing presentations, e.g., via
  `.value` and a `.json()` method. Change alert_processor's Kafka
  output to the canonical form -- this is a **Kafka message-contract
  change** on `alert-responses`, so coordinate with any downstream
  consumers first.
- Or: mark the two enums as genuinely different concepts and rename
  one (e.g., `AlertSeverityTier` vs `AlertSeverityDB`) so there is no
  confusion.

### 5. Concurrent model hot-swap stress test

**Status:** Not started. Latent risk flagged during code review.

**Why:** The review of the fast-path commit (`4a1d3c8`) raised a
threading concern about `_encoder_lookup` reads on the hot path vs.
writes from the `ModelRegistry` refresh loop. Argued at the time that
single-reference reassignment is atomic under the GIL, and the
`.items()` iteration view is snapshotted at iteration start so it
cannot be mutated mid-read. That reasoning is correct but not tested.

**Fix:** Add a unit test that runs `_calculate_ml_fraud_score` in a
loop on one thread while another thread repeatedly calls
`_rebuild_encoder_lookup()` and `_rebuild_scaler_params()`. Assert
that every scoring call returns a valid probability in `[0, 1]` and
that no unhandled exception is raised. 10-second burn-in at 1000 ops/s
on each thread is enough signal.

### 6. Audit Kubernetes manifests for stale references

**Status:** Not started.

**Why:** The docs audit found a cluster of stale values across
component READMEs (threshold 0.7 vs. 0.3, port 8002 for alert_processor,
`--fraud-threshold` vs. `--threshold`). Similar issues may exist in
`k8s/` manifests and `helm/stream-sentinel/values.yaml` -- these are
what actually gets deployed, so a mismatch there bites at runtime
rather than at read-time.

**Fix:** Grep `k8s/` and `helm/` for hard-coded ports, thresholds,
metric names, and CLI flags; cross-check against the current state of
`src/consumers/`, `src/monitoring/metrics.py`, and the Prometheus
alert rules.

## Priority 3 — Nice-to-have / polish

### 7. Add a regression test for the `FraudAlert` cross-enum tolerance

The `FraudAlert.to_dict` defensive normalization (commit `870fcd8`)
has no explicit test that documents its intent. A failing test
(`test_fraudalert_normalizes_alert_processor_severity`) would ensure
future refactors don't silently remove the guard.

### 8. Batch mode benchmarking and promotion

The `--batch` flag for `fraud_detector.py` exists and the C++ / fast
feature extraction work benefits batch mode proportionally. Nobody
has recently measured whether batch mode still gives the SERVING_REPORT's
~386x throughput improvement over single-message now that single-message
is 90x faster than it was. If the gap has narrowed a lot, batch mode
may no longer be worth the operational complexity; if it still wins,
consider making batch the default.

### 9. Producer multi-worker scaling validation

The 3.7k / 7.5k TPS numbers for 2-worker / 4-worker in `README.md` are
historical. Re-measure with the current producer (stdlib random,
bumped linger.ms, Poisson C-features) to confirm scaling is still
close to linear and update `scripts/THROUGHPUT_REPORT.md`.

### 10. Drop or archive the pre-current-architecture ONNX benchmark

`benchmarks/demo_results/ieee_fraud_onnx_benchmark_report.md`
(dated 2025-08-29) reports ONNX at 1.5 RPS / 552 ms latency with a
failed stress test. It documents an alternative inference engine
exploration that is not part of the current system. Either move it
into an `archive/` directory with a README explaining the context, or
delete it outright.

### 11. Add a CODEOWNERS and PR template

Nothing under `.github/` specifies required reviewers or a PR checklist.
Given the security-sensitive nature of the codebase and the multi-tier
FAANG-style review process the project has adopted informally, a
`.github/CODEOWNERS` and `.github/pull_request_template.md` would
codify reviewer expectations and reduce reviewer-assignment toil.

### 12. Surface the `fraud_model_drift_psi` gauge in the Grafana dashboard

The metric is published (`src/ml/online_learning/live_drift_monitor.py`)
and an alert rule exists (`docker/prometheus/alert_rules.yml`), but
the Grafana dashboard at `docker/grafana/dashboards/fraud-detection.json`
doesn't currently panel it. Add a time-series panel so on-call has
visual context when the alert fires.

## Out of scope

- **Real-time fraud labeling pipeline.** The system currently assumes
  fraud labels come from offline investigation; a true online-learning
  flow with labeled feedback latency < 1 minute is a separate project.
- **Multi-region deployment.** The K8s manifests assume a single
  cluster. Geo-distributed deployment needs separate Kafka cluster
  topology, Redis replication, and database replication decisions that
  are beyond the scope of the current hardening pass.
- **Regulatory / SAR workflow integration.** The alert processor
  produces `immediate_block` actions but does not integrate with any
  downstream compliance or SAR (Suspicious Activity Report) system.
  That integration would depend on the operating institution.

---

Last updated: 2026-04-16 (end of producer/detector hardening session).

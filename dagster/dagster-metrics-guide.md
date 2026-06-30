# Dagster Metrics Configuration Specification

## Version: 1.0
## Last Updated: 2026-06-30
## Author: Paul Wong
## Status: Current

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Components](#3-components)
4. [Metrics Reference](#4-metrics-reference)
5. [Configuration](#5-configuration)
6. [Verification](#6-verification)
7. [Troubleshooting](#7-troubleshooting)
8. [Limitations](#8-limitations)
9. [Future Enhancements](#9-future-enhancements)

---

## 1. Overview

This document describes the OpenTelemetry (OTel) metrics configuration for the Dagster platform deployed on the Luban CI Kubernetes cluster. Metrics are exported via OTLP/HTTP to an Elastic APM backend managed by the Elastic Cloud on Kubernetes (ECK) operator.

**Current State:** Only the Dagster platform components (daemon, webserver, metrics-exporter) export metrics. Code locations (comp, ewallet, ferry) and K8s run pods do **not** export metrics due to the absence of the OTel SDK in their container images.

---

## 2. Architecture

### 2.1 Two-Layer Configuration Pattern

Dagster OTel uses a two-layer configuration approach:

| Layer | Scope | Contents | Mechanism |
|-------|-------|----------|-----------|
| **Layer 1: Shared Config** | All pods | OTLP endpoint, protocol, exporter toggles, CA cert path | `dagster-observability` ConfigMap via `envFrom` |
| **Layer 2: Per-Deployment** | Individual pods | `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | Deployment env vars (unique per pod) |

### 2.2 Data Flow

```
[Dagster Platform Pods]
    |
    +-- daemon         --> OTel SDK initialized, no spans produced
    +-- webserver      --> OTel SDK initialized, no spans produced
    +-- metrics-exporter --> 10 ObservableGauges (polled from PostgreSQL)
    |
    v
[OTLP/HTTP Exporter] --> [Elastic APM Server :8200] --> [Elasticsearch]
```

### 2.3 Storage in Elasticsearch

| Index Pattern | Content | Current State |
|---------------|---------|:-------------:|
| `traces-apm-*` | Span traces | Empty (no traces produced) |
| `metrics-apm.*` | Gauge metrics | Active (10 metrics from metrics-exporter) |

---

## 3. Components

### 3.1 Platform Components

All platform components use `luban_dagster_platform` as their entrypoint. The initialization flow is:

```
entrypoints.py:
  1. configure_otel()           # Initialize OTel SDK
     ├── configure_tracing()    # Set up TracerProvider
     └── configure_metrics()    # Set up MeterProvider
  2. Start Dagster component    # daemon / webserver
```

| Component | Image Bundles SDK? | Exports Data? | What Is Exported |
|-----------|:-----------------:|:-------------:|------------------|
| `dagster-platform-daemon` | Yes | No | SDK initialized but Dagster 1.12 creates zero spans |
| `dagster-platform-webserver` | Yes | No | Same as daemon |
| `dagster-platform-metrics-exporter` | Yes | Yes | 10 ObservableGauges (self-created, polled from DB) |

### 3.2 Code Locations

Code locations are user-defined services that define and execute Dagster jobs. They receive OTel environment variables via the shared ConfigMap but have **no OTel SDK installed**.

| Component | Image Bundles SDK? | Exports Data? | Reason |
|-----------|:-----------------:|:-------------:|--------|
| `comp` | No | No | No SDK installed |
| `ewallet` | No | No | No SDK installed |
| `ferry` | No | No | No SDK installed |

### 3.3 K8s Run Pods

When Dagster launches a job via `K8sRunLauncher`, it creates a new Pod that inherits the code location's container image. Since code locations lack the OTel SDK, run pods also cannot export metrics.

| Component | Exports Data? | Reason |
|-----------|:-------------:|--------|
| K8s Run Pods | No | Inherits code location image (no SDK) |

---

## 4. Metrics Reference

### 4.1 Source

All current metrics originate from `dagster-platform-metrics-exporter`, a dedicated Python process that polls the Dagster PostgreSQL database every 60 seconds.

### 4.2 Metric Types

All metrics are **ObservableGauges** — values that can increase or decrease at any time, representing the current state (like a speedometer). This is distinct from:

- **Counter**: Monotonically increasing (e.g., total runs completed)
- **Histogram**: Statistical distribution (e.g., run duration percentiles)

### 4.3 Complete Metrics List

| # | Metric Name | Type | Description | Labels | Poll Interval |
|---|------------|------|-------------|--------|:-------------:|
| 1 | `dagster.run.queue.depth` | gauge | Number of runs waiting in the execution queue | — | 60s |
| 2 | `dagster.run.queue.oldest_age_seconds` | gauge | Age of the oldest queued run in seconds | — | 60s |
| 3 | `dagster.run.in_progress.count` | gauge | Number of runs currently executing | — | 60s |
| 4 | `dagster.sensor.enabled.count` | gauge | Number of enabled sensors | — | 60s |
| 5 | `dagster.schedule.enabled.count` | gauge | Number of enabled schedules | — | 60s |
| 6 | `dagster.sensor.last_tick_age_seconds` | gauge | Seconds since the last sensor tick | `dagster.instigator_name`, `dagster.instigator_status` | 60s |
| 7 | `dagster.schedule.last_tick_age_seconds` | gauge | Seconds since the last schedule tick | `dagster.instigator_name`, `dagster.instigator_status` | 60s |
| 8 | `dagster.daemon.heartbeat.count` | gauge | Total daemon heartbeat count | — | 60s |
| 9 | `dagster.daemon.heartbeat_age_seconds` | gauge | Seconds since the last daemon heartbeat | `dagster.daemon_type` | 60s |
| 10 | `dagster.daemon.heartbeat_errors.count` | gauge | Count of daemon heartbeat errors | `dagster.daemon_type` | 60s |

### 4.4 Label Details

**Sensor/Schedule Tick Metrics (items 6-7):**

| Label | Values | Example |
|-------|--------|---------|
| `dagster.instigator_name` | Sensor or schedule name | `my_sensor`, `daily_etl_schedule` |
| `dagster.instigator_status` | Current status | `RUNNING`, `PAUSED`, `ERROR` |

**Daemon Heartbeat Metrics (items 9-10):**

| Label | Values | Example |
|-------|--------|---------|
| `dagster.daemon_type` | Daemon type | `SensorDaemon`, `RunsDaemon`, `QueuedRunsDaemon` |

### 4.5 Interpretation Guide

| Metric | Healthy Range | Warning Signal |
|--------|:------------:|----------------|
| `run.queue.depth` | 0-10 | Sustained > 50 |
| `run.queue.oldest_age_seconds` | < 300s | > 1800s (30 min) |
| `run.in_progress.count` | Matches concurrency limits | Exceeds limits |
| `sensor.last_tick_age_seconds` | < 300s | > 3600s (1 hour) |
| `schedule.last_tick_age_seconds` | < 300s | > 3600s (1 hour) |
| `daemon.heartbeat_age_seconds` | < 60s | > 300s (5 min) |
| `daemon.heartbeat_errors.count` | 0 | Any value > 0 |

---

## 5. Configuration

### 5.1 Shared Configuration (ConfigMap)

**Resource:** `dagster-observability` ConfigMap (namespace: `snd-motogp`)

**Injected into all pods via `envFrom`.**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dagster-observability
  namespace: snd-motogp
data:
  OTEL_TRACES_EXPORTER: "otlp"
  OTEL_METRICS_EXPORTER: "otlp"
  OTEL_EXPORTER_OTLP_ENDPOINT: "https://apm.luban.testing.local:443"
  OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf"
  OTEL_EXPORTER_OTLP_CERTIFICATE: "/etc/luban-ca/ca.crt"
  OTEL_EXPORTER_OTLP_TIMEOUT: "30"
```

**Environment Variables Reference:**

| Variable | Purpose | Valid Values |
|----------|---------|-------------|
| `OTEL_TRACES_EXPORTER` | Enable trace export | `otlp`, `none` |
| `OTEL_METRICS_EXPORTER` | Enable metrics export | `otlp`, `none` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | APM server URL | Full URL with scheme |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | OTLP transport protocol | `http/protobuf`, `http/json`, `grpc` |
| `OTEL_EXPORTER_OTLP_CERTIFICATE` | Path to CA cert bundle | File path inside container |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | Request timeout in seconds | Integer (default: 10) |

### 5.2 Authentication (Secret)

**Resource:** `apm-otlp-headers` Secret (namespace: `snd-motogp`)

The APM Bearer token is stored in a Secret and injected via `envFrom` or `env.valueFrom.secretKeyRef`. **Never store the token in a ConfigMap.**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: apm-otlp-headers
  namespace: snd-motogp
type: Opaque
data:
  OTEL_EXPORTER_OTLP_HEADERS: <base64-encoded "Authorization=Bearer <token>">
```

**Token Retrieval:**

```bash
kubectl get secret -n elastic-system apm-server-apm-token \
  -o jsonpath='{.data.secret-token}' | base64 -d
```

### 5.3 CA Certificate (Volume Mount)

The APM server uses HTTPS. A valid CA certificate must be mounted into each exporting pod.

**Resource:** `luban-ca` ConfigMap (namespace: `snd-motogp`)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: luban-ca
  namespace: snd-motogp
data:
  ca.crt: |
    -----BEGIN CERTIFICATE-----
    <Luban CA certificate PEM data>
    -----END CERTIFICATE-----
```

**Retrieval from cluster:**

```bash
kubectl get secret -n luban-ci luban-ca-cert -o jsonpath='{.data.ca\.crt}' | base64 > /tmp/luban-ca.crt
kubectl create configmap -n snd-motogp luban-ca --from-file=ca.crt=/tmp/luban-ca.crt --dry-run=client -o yaml | kubectl apply -f -
```

**Deployment volume mount:**

```yaml
volumes:
  - name: luban-ca
    configMap:
      name: luban-ca
volumeMounts:
  - name: luban-ca
    mountPath: /etc/luban-ca
    readOnly: true
```

### 5.4 Per-Deployment Overrides

Each platform component needs a unique service identity. Set via deployment env vars:

| Component | `OTEL_SERVICE_NAME` | `dagster.component` |
|-----------|-------------------|--------------------|
| daemon | `<project>-dagster-platform-daemon` | daemon |
| webserver | `<project>-dagster-platform-webserver` | webserver |
| metrics-exporter | `<project>-dagster-platform-metrics-exporter` | metrics-exporter |

**Example deployment env:**

```yaml
env:
  - name: OTEL_SERVICE_NAME
    value: "motogp-dagster-platform-metrics-exporter"
  - name: OTEL_RESOURCE_ATTRIBUTES
    value: "deployment.environment=snd,project.name=motogp,dagster.component=metrics-exporter"
```

### 5.5 Full Deployment Configuration Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dagster-platform-metrics-exporter
  namespace: snd-motogp
spec:
  template:
    spec:
      containers:
        - name: metrics-exporter
          image: <dagster-image>
          envFrom:
            - configMapRef:
                name: dagster-observability
            - secretRef:
                name: apm-otlp-headers
          env:
            - name: OTEL_SERVICE_NAME
              value: "motogp-dagster-platform-metrics-exporter"
            - name: OTEL_RESOURCE_ATTRIBUTES
              value: "deployment.environment=snd,project.name=motogp,dagster.component=metrics-exporter"
          volumeMounts:
            - name: luban-ca
              mountPath: /etc/luban-ca
              readOnly: true
      volumes:
        - name: luban-ca
          configMap:
            name: luban-ca
```

---

## 6. Verification

### 6.1 Check Metrics-Exporter Pod Health

```bash
# Verify pod is running
kubectl get pods -n snd-motogp -l app=dagster-platform-metrics-exporter

# Check logs for OTel initialization
kubectl logs -n snd-motogp deployment/dagster-platform-metrics-exporter --tail=50

# Verify OTel SDK is importable
kubectl exec -n snd-motogp deployment/dagster-platform-metrics-exporter -- \
  /layers/luban-ci_python-uv/venv/bin/python -c \
  "from opentelemetry import metrics; print('OK')"
```

### 6.2 Verify Data in Elasticsearch

```bash
# List metrics indices
kubectl exec -n elastic-system elastic-cluster-es-default-0 -- \
  curl -sk -u "elastic:<password>" \
  "https://localhost:9200/_cat/indices/metrics-apm*?v"

# Search for Dagster metrics
kubectl exec -n elastic-system elastic-cluster-es-default-0 -- \
  curl -sk -u "elastic:<password>" \
  "https://localhost:9200/.ds-metrics-apm.app.dagster*/_search?pretty&size=50" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
hits = data.get('hits', {}).get('hits', [])
fields = set()
for h in hits:
    dagster = h.get('_source', {}).get('dagster', {})
    for cat, vals in dagster.items():
        for metric in vals.keys():
            fields.add(f'{cat}.{metric}')
print('Metrics found:')
for f in sorted(fields):
    print(f'  {f}')
"
```

Expected output should list all 10 metrics from Section 4.3.

### 6.3 Check Kibana Dashboard

Access the Dagster metrics dashboard in Kibana:

1. Navigate to Kibana > Dashboards
2. Open the Dagster metrics dashboard
3. Verify the time range filter (`_g` param) covers the last 15 minutes: `time:(from:now-15m,to:now)`
4. Check auto-refresh is enabled: `refreshInterval:(pause:!t,value:60000)`

**Note:** Kibana Basic license only supports `metric` and `table` visualization types. `line` and `horizontal_bar` charts with `terms` aggregation will hang indefinitely.

---

## 7. Troubleshooting

### 7.1 SSL Certificate Verification Failed

**Error:**
```
SSL: CERTIFICATE_VERIFY_FAILED certificate verify failed: 
unable to get local issuer certificate
```

**Cause:** The OTel Python HTTP exporter requires a valid CA certificate. The environment variable `OTEL_EXPORTER_OTLP_INSECURE=true` does **not** work with the HTTP exporter (it only affects gRPC).

**Solution:** Ensure `OTEL_EXPORTER_OTLP_CERTIFICATE` points to a valid CA cert file, and the file is properly mounted as a volume.

### 7.2 No Metrics Appearing in Elasticsearch

**Checklist:**

1. Verify the metrics-exporter pod is running:
   ```bash
   kubectl get pods -n snd-motogp | grep metrics-exporter
   ```

2. Check if OTel SDK is importable inside the pod:
   ```bash
   kubectl exec -n snd-motogp deployment/dagster-platform-metrics-exporter -- \
     python -c "from opentelemetry import metrics; print('SDK OK')"
   ```

3. Verify the CA cert is mounted:
   ```bash
   kubectl exec -n snd-motogp deployment/dagster-platform-metrics-exporter -- \
     ls -la /etc/luban-ca/ca.crt
   ```

4. Check pod logs for OTel errors:
   ```bash
   kubectl logs -n snd-motogp deployment/dagster-platform-metrics-exporter
   ```

5. Confirm the APM endpoint is reachable:
   ```bash
   kubectl exec -n snd-motogp deployment/dagster-platform-metrics-exporter -- \
     curl -sk -o /dev/null -w "%{http_code}" \
     https://apm.luban.testing.local:443
   ```

### 7.3 ConfigMap Changes Not Taking Effect

ConfigMap changes injected via `envFrom` require a pod restart:

```bash
kubectl rollout restart deployment/dagster-platform-metrics-exporter -n snd-motogp
```

### 7.4 ArgoCD Reverting Direct Patches

If the ConfigMap or deployment has the annotation `argocd.argoproj.io/tracking-id`, direct `kubectl patch` commands will be overwritten on the next ArgoCD sync. Instead:

1. Update the GitOps repository overlay
2. Commit and push the change
3. Wait for ArgoCD to sync

### 7.5 Code Locations Showing No Metrics

This is **expected behavior**. Code locations (comp, ewallet, ferry) and their run pods do not have the OTel SDK installed. Only platform components export metrics. See Section 3.2 and Section 8 for details.

---

## 8. Limitations

### 8.1 Dagster 1.12 Does Not Produce OTel Spans

Dagster version 1.12.19 has **zero OpenTelemetry instrumentation** in its core codebase. Searching the entire `dagster` package for `start_span` or `start_as_current_span` yields no results. This means:

- No traces are produced for sensor ticks, run executions, or API calls
- No automatic span creation for job steps
- No automatic metric recording for run durations

The OTel SDK initializes successfully, but nothing in Dagster uses it.

### 8.2 Code Locations Lack OTel SDK

Code location images do not include `opentelemetry-sdk`, `opentelemetry-api`, or `opentelemetry-exporter-otlp-proto-http`. Environment variables for OTel are inherited via the shared ConfigMap but have no effect without the SDK packages.

### 8.3 Kibana Basic License Restrictions

With the Basic license:
- Only `metric` and `table` visualization types work reliably
- `line` and `horizontal_bar` charts with `terms` aggregation hang at "loading"
- The `data_views` API requires Enterprise license — use `saved_objects` API instead

### 8.4 HTTP Exporter Does Not Support Insecure Mode

`OTEL_EXPORTER_OTLP_INSECURE=true` only affects the gRPC exporter. The HTTP exporter always requires a valid CA certificate. There is no equivalent `OTEL_EXPORTER_OTLP_HTTP_INSECURE` environment variable.

### 8.5 No Run-Level Tracing

Individual job runs executed via `K8sRunLauncher` produce no traces or metrics. Each run pod inherits the code location image without OTel SDK.

---

## 9. Future Enhancements

### 9.1 Enable Code Location Metrics

To export metrics from code locations, add the following to each code location's `pyproject.toml`:

```toml
[project.dependencies]
opentelemetry-api = ">=1.30,<2.0"
opentelemetry-sdk = ">=1.30,<2.0"
opentelemetry-exporter-otlp-proto-http = ">=1.30,<2.0"
```

After rebuilding the code location images, the existing environment variables will take effect.

### 9.2 Add Custom Metrics to Code Locations

Once the SDK is available, add custom metrics in your ops:

```python
from opentelemetry import metrics

meter = metrics.get_meter("comp")
runs_total = meter.create_counter("comp.runs.total", description="Total runs executed")

@op
def my_dagster_op(context):
    runs_total.add(1, {"status": "success"})
    # ... rest of the op
```

### 9.3 Enable Dagster Native Metrics

Dagster has its own metrics API (separate from OTel) that can be used without SDK installation:

```python
from dagster import op

@op(metrics={"my_custom_metric": 42})
def my_op(context):
    context.metrics.increment("my_custom_metric", 1)
```

These metrics can be exported to Prometheus or other backends via Dagster's built-in metrics endpoint.

### 9.4 Run-Level Tracing (Long-Term)

For per-step execution timing in APM, consider:

1. Adding OTel SDK to code location images (Section 9.1)
2. Instrumenting Dagster jobs with manual span creation
3. Upgrading to a newer Dagster version that includes native OTel support (if available)

### 9.5 Alternative: Prometheus Integration

For richer metrics without SDK changes, expose Dagster's built-in metrics endpoint and scrape with Prometheus:

```yaml
# prometheus scrape config
scrape_configs:
  - job_name: 'dagster'
    static_configs:
      - targets: ['dagster-webserver:4000']
    metrics_path: '/metrics'
```

---

## Appendix A: Environment Variables Quick Reference

| Variable | Purpose | Required For | Example Value |
|----------|---------|:------------:|--------------|
| `OTEL_TRACES_EXPORTER` | Enable trace export | Platform components | `otlp` |
| `OTEL_METRICS_EXPORTER` | Enable metrics export | Platform components | `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | APM server URL | All exporters | `https://apm.luban.testing.local:443` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Transport protocol | All exporters | `http/protobuf` |
| `OTEL_EXPORTER_OTLP_CERTIFICATE` | CA cert path | HTTPS exporters | `/etc/luban-ca/ca.crt` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Auth headers | All exporters | `Authorization=Bearer <token>` |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | Request timeout (s) | All exporters | `30` |
| `OTEL_SERVICE_NAME` | Service identity | All exporters | `motogp-dagster-platform-metrics-exporter` |
| `OTEL_RESOURCE_ATTRIBUTES` | Resource labels | All exporters | `deployment.environment=snd,dagster.component=metrics-exporter` |

## Appendix B: Key Files and Resources

| Resource | Namespace | Kind | Name |
|----------|----------|------|------|
| ConfigMap | `snd-motogp` | ConfigMap | `dagster-observability` |
| Secret (auth) | `snd-motogp` | Secret | `apm-otlp-headers` |
| ConfigMap (CA) | `snd-motogp` | ConfigMap | `luban-ca` |
| Secret (token) | `elastic-system` | Secret | `apm-server-apm-token` |
| APM Server | `elastic-system` | Service | `apm-server-apm-http` |
| Elasticsearch | `elastic-system` | StatefulSet | `elastic-cluster-es-default` |

## Appendix C: Deployment Namespaces

| Component | Namespace |
|-----------|----------|
| Dagster platform (daemon, webserver, metrics-exporter) | `snd-motogp` |
| Code locations (comp, ewallet, ferry) | `snd-motogp` |
| Elastic APM Server | `elastic-system` |
| Luban CA cert source | `luban-ci` |

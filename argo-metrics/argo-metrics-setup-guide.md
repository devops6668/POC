# Argo Workflows Metrics — Step-by-Step Setup Guide
## OpenTelemetry → Elasticsearch → Kibana Dashboard

---

## 1. 部署 OpenTelemetry Collector

在 `elastic-system` namespace 部署 OTel Collector，接收 Argo Controller 的 OTLP metrics，寫入 ECK Elasticsearch。

### 1a. 建立 ES 憑證 Secret

```bash
kubectl create secret generic otel-es-credentials -n elastic-system \
  --from-literal=es-username=elastic \
  --from-literal=es-password=<PASSWORD>
```

> `<PASSWORD>` 改為你 ES `elastic` 用戶的密碼，可從 secret `elastic-cluster-es-elastic-user -n elastic-system` 取得。

### 1b. 部署 OTel Collector

**`otel-collector.yaml`：**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: otel-es-credentials
  namespace: elastic-system
type: Opaque
stringData:
  es-username: elastic
  es-password: <PASSWORD>  # 從 elastic-cluster-es-elastic-user secret 拎
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: otel-collector-config
  namespace: elastic-system
data:
  otel-collector-config.yaml: |
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318

    processors:
      batch:
        timeout: 5s
        send_batch_size: 1000

    exporters:
      elasticsearch:
        endpoints:
          - "https://${env:ES_USERNAME}:${env:ES_PASSWORD}@elastic-cluster-es-http.elastic-system.svc:9200"
        tls:
          insecure: false
          ca_file: /etc/otel/tls/ca.crt

    service:
      pipelines:
        metrics:
          receivers: [otlp]
          processors: [batch]
          exporters: [elasticsearch]
---
apiVersion: v1
kind: Service
metadata:
  name: otel-collector
  namespace: elastic-system
spec:
  selector:
    app: otel-collector
  ports:
    - name: otlp-grpc
      port: 4317
      targetPort: 4317
    - name: otlp-http
      port: 4318
      targetPort: 4318
    - name: metrics
      port: 8888
      targetPort: 8888
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: otel-collector
  namespace: elastic-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: otel-collector
  template:
    metadata:
      labels:
        app: otel-collector
    spec:
      containers:
        - name: otel-collector
          image: otel/opentelemetry-collector-contrib:0.119.0
          args:
            - "--config=/conf/otel-collector-config.yaml"
          ports:
            - containerPort: 4317
              name: otlp-grpc
            - containerPort: 4318
              name: otlp-http
            - containerPort: 8888
              name: metrics
          volumeMounts:
            - name: config
              mountPath: /conf
            - name: es-ca
              mountPath: /etc/otel/tls
              readOnly: true
      volumes:
        - name: config
          configMap:
            name: otel-collector-config
        - name: es-ca
          secret:
            secretName: apm-server-apm-es-ca
            items:
              - key: ca.crt
                path: ca.crt
```

> 注意：`<PASSWORD>` 改為你 ES `elastic` 用戶的密碼。
> CA 證書 secret 名稱可能不同，請按實際環境調整。

```bash
kubectl apply -f otel-collector.yaml
```

---

## 2. 配置 Argo Controller 發送 OTLP Metrics

為 Argo `workflow-controller` 和 `argo-server` deployment 加入 OTLP 環境變數：

```bash
kubectl set env deployment/workflow-controller -n argo \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.elastic-system.svc:4317 \
  OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
  OTEL_SERVICE_NAME=workflows-controller \
  OTEL_RESOURCE_ATTRIBUTES=service.name=workflows-controller

kubectl set env deployment/argo-server -n argo \
  OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.elastic-system.svc:4317 \
  OTEL_EXPORTER_OTLP_PROTOCOL=grpc \
  OTEL_SERVICE_NAME=argo-server \
  OTEL_RESOURCE_ATTRIBUTES=service.name=argo-server
```

確認 rollout：

```bash
kubectl rollout status deployment/workflow-controller -n argo
kubectl rollout status deployment/argo-server -n argo
```

---

## 3. 啟用 Argo Controller Prometheus Metrics

Argo controller 默認會暴露 Prometheus metrics 在 port 9090 (`/metrics`)。如果自訂過 configmap，可以確認以下設定：

```bash
kubectl patch configmap workflow-controller-configmap -n argo --type=merge -p '{
  "data": {
    "config": "metricsConfig:\n  enabled: true\n  port: 9090\n  path: /metrics\n"
  }
}'
```

---

## 4. 驗證 Metrics 流入 ES

等待 1–2 分鐘讓數據流動：

```bash
# 確認有新的 generic metrics index
kubectl exec curl-pod -- curl -sk \
  -u "elastic:$(kubectl get secret elastic-cluster-es-elastic-user -n elastic-system -o jsonpath='{.data.elastic}' | base64 -d)" \
  "https://elastic-cluster-es-http.elastic-system.svc:9200/_cat/indices" | grep generic

# 檢查數據內容
kubectl exec curl-pod -- curl -sk \
  -u "elastic:$(kubectl get secret elastic-cluster-es-elastic-user -n elastic-system -o jsonpath='{.data.elastic}' | base64 -d)" \
  "https://elastic-cluster-es-http.elastic-system.svc:9200/.ds-metrics-generic-default*/_search?size=3"
```

---

## 5. 在 Kibana 建立 Index Pattern

```bash
curl -sk -X POST "https://kibana.luban.paulhome.local/api/saved_objects/index-pattern/ds-metrics-generic-default" \
  -u "elastic:$(kubectl get secret elastic-cluster-es-elastic-user -n elastic-system -o jsonpath='{.data.elastic}' | base64 -d)" \
  -H "Content-Type: application/json" \
  -H "kbn-xsrf: true" \
  -d '{"attributes":{"title":".ds-metrics-generic-default*","timeFieldName":"@timestamp"}}'
```

---

## 6. 建立 Visualizations 和 Dashboard

使用 Saved Objects API 依次建立：

1. **Saved Search** — `argo_metrics_search`（filter `service.name: workflows-controller`）
2. **7 個 Controller Visualizations**：
   - `argo_phase_trend` — Workflow Phase Trend（line, max of gauge by phase）
   - `argo_namespaces` — Workflows by Namespace（pie, count by kubernetes.namespace）
   - `argo_k8s_api` — K8s API Requests（pie, sum of k8s_request_total by kind）
   - `argo_queue_depth` — Queue Depth（line, avg of queue_depth_gauge by queue）
   - `argo_errors` — Error Count（metric, sum of error_count）
   - `argo_workers` — Workers Busy（line, avg of workers_busy_count）
   - `argo_error_causes` — Error Causes（table, sum of error_count by cause）
3. **Dashboard** — `argo-workflows-overview`（16 panels, time range now-24h）

API 調用範例（每個 visualization 一個 POST）：

```bash
curl -sk -X POST "https://kibana.luban.paulhome.local/api/saved_objects/visualization/argo_phase_trend" \
  -u "elastic:$(kubectl get secret elastic-cluster-es-elastic-user -n elastic-system -o jsonpath='{.data.elastic}' | base64 -d)" \
  -H "Content-Type: application/json" \
  -H "kbn-xsrf: true" \
  -d '{
    "attributes": {
      "title": "Workflow Phase Trend",
      "visState": "{\"title\":\"Workflow Phase Trend\",\"type\":\"line\",\"aggs\":[{\"id\":\"1\",\"type\":\"max\",\"schema\":\"metric\",\"params\":{\"field\":\"gauge\",\"customLabel\":\"Workflow Count\"}},{\"id\":\"2\",\"type\":\"date_histogram\",\"schema\":\"segment\",\"params\":{\"field\":\"@timestamp\",\"interval\":\"auto\",\"min_doc_count\":1}},{\"id\":\"3\",\"type\":\"terms\",\"schema\":\"group\",\"params\":{\"field\":\"phase\",\"size\":10,\"order\":\"desc\",\"orderBy\":\"1\"}}]}",
      "uiStateJSON": "{}",
      "kibanaSavedObjectMeta": {
        "searchSourceJSON": "{\"index\":\"ds-metrics-generic-default\",\"query\":{\"bool\":{\"must\":[{\"term\":{\"service.name\":\"workflows-controller\"}}]}}}"
      }
    }
  }'
```

Dashboard panelsJSON 需使用 Kibana 8.x 格式（`gridData` 非 `gridPosition`）。

---

## 7. 建立 Workflow Duration 收集 (CronJob)

### 7a. 設定 RBAC

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: argo-duration-sa
  namespace: argo
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: argo-duration-reader
rules:
- apiGroups: ["argoproj.io"]
  resources: ["workflows"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: argo-duration-reader-binding
subjects:
- kind: ServiceAccount
  name: argo-duration-sa
  namespace: argo
roleRef:
  kind: ClusterRole
  name: argo-duration-reader
  apiGroup: rbac.authorization.k8s.io
```

### 7b. 建立 ES 憑證 Secret

```bash
kubectl create secret generic es-credentials -n argo \
  --from-literal=ES_USER=elastic \
  --from-literal=ES_PASS=<PASSWORD>
```

### 7c. 建立 Python Script 作為 ConfigMap

Script 邏輯：
- 通過 K8s API 獲取所有 namespace 的 Workflow CRD
- 提取 `status.startedAt` / `finishedAt` 計算 duration
- 推送到 ES index `argo-workflow-durations`
- GC：每個 template 只保留最近 10 筆

```bash
kubectl create configmap argo-duration-script -n argo --from-file=main.py=collector.py
```

### 7c. 建立 CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: argo-workflow-durations
  namespace: argo
spec:
  schedule: "*/3 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: argo-duration-sa
          containers:
          - name: collector
            image: python:3.12-alpine
            command:
            - /bin/sh
            - -c
            - |
              apk add --no-cache curl >/dev/null 2>&1
              python3 /scripts/main.py
            envFrom:
            - secretRef:
                name: es-credentials
            env:
            - name: ES_URL
              value: https://elastic-cluster-es-http.elastic-system.svc:9200
            volumeMounts:
            - name: script
              mountPath: /scripts
              readOnly: true
          restartPolicy: Never
          volumes:
          - name: script
            configMap:
              name: argo-duration-script
              defaultMode: 0555
```

### 7d. 在 Kibana 建立 Duration Index Pattern

```bash
curl -sk -X POST "https://kibana.luban.paulhome.local/api/data_views/data_view" \
  -u "elastic:$(kubectl get secret elastic-cluster-es-elastic-user -n elastic-system -o jsonpath='{.data.elastic}' | base64 -d)" \
  -H "Content-Type: application/json" \
  -H "kbn-xsrf: true" \
  -d '{"data_view":{"title":"argo-workflow-durations*","name":"Argo Workflow Durations","timeFieldName":"@timestamp"}}'
```

### 7e. 建立 Duration Visualizations

5 個 visualizations（共用 index: `argo-workflow-durations`）：

| ID | 類型 | 用途 |
|----|------|------|
| `argo_duration_latest` | metric | 最新一次 workflow duration |
| `argo_duration_trend` | line | 各 template 的 duration 趨勢 |
| `argo_duration_by_tmpl` | histogram | 每個 template 平均 duration |
| `argo_ns_duration` | table | 每個 NS + app label 的最新 duration |
| `argo_duration_runs_line` | line | 各 workflow 的 duration 時間序列 |

---

## 8. 最終 Dashboard Layout

16 個 panels：

```
Row 0: [Workflow Phase Trend                                    ]  全闊 line
Row 1: [By NS] [K8s API] [Queue Depth] [Errors]
Row 2: [API by NS] [Workers Busy] [Error Causes]
Row 3: [Leader] [Latest Controller Logs                          ]
Row 4: [Duration by App Over Time                                ]  全闊 line
Row 5: [Latest Dur] [Workflow Duration Over Time                 ]
Row 6: [Duration Tmpl] [Duration NS(app)] [Latest Runs Line]
```

---

## 9. 已知限制

- **Histogram metrics 被 drop**：OTel ES exporter 0.119.0 不支持 cumulative temporality histogram，`operation_duration_seconds`、`k8s_request_duration` 等 metrics 不會寫入 ES。如果需要，可改用 Prometheus remote write 或在 OTel pipeline 加入 `cumulativetodelta` processor（需 upgrade 到 0.121.0+，但 0.121.0 有 mapping 問題）。
- **CronJob 不支援 kubectl**：使用 K8s API 直接查 workflow，不需 kubectl binary。
- **Dashboard 時間範圍**：設為 `now-24h`，可手動在 Kibana 調整。

---

*Generated by Hermes Agent — 2026-07-09*

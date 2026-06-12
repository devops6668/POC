# StarRocks on Kubernetes — Shared-Data (Storage-Compute Separation) Deployment Guide

> References:
> - [StarRocks Kubernetes Operator Docs (v4.1)](https://docs.starrocks.io/docs/deployment/sr_operator/)
> - [Deploy Shared-Data StarRocks Manually](https://docs.starrocks.io/docs/deployment/deploy_shared_data_manually/)
> - [Operator GitHub](https://github.com/StarRocks/starrocks-kubernetes-operator)
> - [Operator API Reference](https://github.com/StarRocks/starrocks-kubernetes-operator/blob/main/doc/api.md)

**Architecture**: FE + CN (storage-compute separation), MinIO as object storage, CN autoscaling enabled.
**Not deploying to Paul's cluster.**

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  Kubernetes Cluster                                     │
│                                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                 │
│  │   FE-0  │  │   FE-1  │  │   FE-2  │                 │
│  └────┬────┘  └─────────┘  └─────────┘   Metadata mgmt│
│       │  Leader/Follower/Election                       │
│       │                                                 │
│  ┌──────────────────────────────────────────┐           │
│  │          Compute Nodes (CN)              │           │
│  │  ┌──────┐  ┌──────┐  ┌──────┐           │           │
│  │  │ CN-0 │  │ CN-1 │  │ CN-2 │ → HPA     │           │
│  │  └──────┘  └──────┘  └──────┘           │           │
│  └──────────────────────────────────────────┘           │
│         ↑ Query execution + Local Cache                  │
│         │                                               │
│  ┌──────────────────────────────────────────┐           │
│  │         MinIO (S3 Compatible)            │           │
│  │  bucket: starrocks-data                  │           │
│  │  Access: minio:minio123456789            │           │
│  └──────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────┘
```

**Key difference**:
- **shared-nothing (traditional)**: FE + BE (BE handles storage + compute)
- **shared-data (storage-compute separation)**: FE + CN (CN handles only compute, data stored in MinIO/S3)

---

## 2. Install the Operator

### 2.1 Create CRD

```bash
kubectl apply -f https://raw.githubusercontent.com/StarRocks/starrocks-kubernetes-operator/main/deploy/starrocks.com_starrocksclusters.yaml
```

> **Note**: If you encounter a `Too long: must have at most 262144 bytes` error on first install:
> ```bash
> kubectl create -f https://raw.githubusercontent.com/StarRocks/starrocks-kubernetes-operator/main/deploy/starrocks.com_starrocksclusters.yaml
> ```

### 2.2 Deploy the Operator

```bash
kubectl apply -f https://raw.githubusercontent.com/StarRocks/starrocks-kubernetes-operator/main/deploy/operator.yaml
```

### 2.3 Verify

```bash
kubectl -n starrocks get pods
```

Expected:

```
NAME                                           READY   STATUS    RESTARTS   AGE
starrocks-controller-65bb8679-jkbtg            1/1     Running   0          5m
```

---

## 3. Create the StarRocks Cluster

### 3.1 Complete YAML File

Create file `starrocks-shared-data.yaml`:

```yaml
---
apiVersion: starrocks.com/v1
kind: StarRocksCluster
metadata:
  name: starrocks-shared-data
  namespace: starrocks
spec:
  # ─────────────────────────────────────
  # FE: Frontend — Metadata mgmt + Query compilation
  # ─────────────────────────────────────
  starRocksFeSpec:
    image: starrocks/fe-ubuntu:latest
    replicas: 3
    limits:
      cpu: 4
      memory: 8Gi
    requests:
      cpu: 2
      memory: 4Gi
    configMapInfo:
      configMapName: starrocks-shared-data-fe-cm
      resolveKey: fe.conf
    storageVolumes:
    - name: fe-meta
      storageSize: 10Gi
      mountPath: /opt/starrocks/fe/meta
    - name: fe-log
      storageSize: 5Gi
      mountPath: /opt/starrocks/fe/log

  # ─────────────────────────────────────
  # CN: Compute Node — Query execution + Local cache
  # ─────────────────────────────────────
  starRocksCnSpec:
    image: starrocks/cn-ubuntu:latest
    limits:
      cpu: 8
      memory: 32Gi
    requests:
      cpu: 2
      memory: 8Gi
    storageVolumes:
    - name: cn-cache
      storageSize: 100Gi
      mountPath: /opt/starrocks/cn/storage
    - name: cn-log
      storageSize: 5Gi
      mountPath: /opt/starrocks/cn/log
    autoScalingPolicy:
      maxReplicas: 10
      minReplicas: 1
      hpaPolicy:
        metrics:
          - type: Resource
            resource:
              name: memory
              target:
                averageUtilization: 60
                type: Utilization
          - type: Resource
            resource:
              name: cpu
              target:
                averageUtilization: 60
                type: Utilization
        behavior:
          scaleUp:
            policies:
              - type: Pods
                value: 1
                periodSeconds: 10
          scaleDown:
            selectPolicy: Disabled

---
# FE Config — set shared_data run_mode
apiVersion: v1
kind: ConfigMap
metadata:
  name: starrocks-shared-data-fe-cm
  labels:
    cluster: starrocks-shared-data
data:
  fe.conf: |
    run_mode = shared_data
    meta_dir = /opt/starrocks/fe/meta
    LOG_DIR = ${STARROCKS_HOME}/log
    http_port = 8030
    rpc_port = 9020
    query_port = 9030
    edit_log_port = 9010
    cloud_native_meta_port = 6090
    mysql_service_nio_enabled = true
    sys_log_level = INFO
    # If you have an IPv4 network interface, uncomment:
    # priority_networks = 192.168.0.0/16
```

**Important fields**:

| Field | Description |
|-------|-------------|
| `starRocksFeSpec.configMapInfo` | Pass FE config via ConfigMap (includes `run_mode = shared_data`) |
| `starRocksCnSpec.storageVolumes.cn-cache` | CN local cache directory (hot data cache) for query acceleration |
| `starRocksCnSpec.autoScalingPolicy` | HPA autoscaling policy (CPU/Memory 60% threshold) |
| `minReplicas / maxReplicas` | CN scaling range (1~10 nodes) |
| `scaleDown.selectPolicy: Disabled` | Disable autoscaling down (avoid frequent scaling affecting performance) |

> **No-cache mode**: If CN Pods have no persistent storage (e.g., scaling to zero), set `storageVolumes` to empty:
> ```yaml
> starRocksCnSpec:
>   storageVolumes: []
> ```

### 3.2 Deploy

```bash
kubectl apply -f starrocks-shared-data.yaml
```

### 3.3 Verify

```bash
kubectl -n starrocks get pods -l app.kubernetes.io/managed-by=starrocks-operator
```

Expected:

```
NAME                                              READY   STATUS    RESTARTS   AGE
starrocks-shared-data-fe-0                        1/1     Running   0          3m
starrocks-shared-data-fe-1                        1/1     Running   0          3m
starrocks-shared-data-fe-2                        1/1     Running   0          3m
starrocks-shared-data-cn-0                        1/1     Running   0          3m
```

---

## 4. Configure MinIO as Object Storage

### 4.1 Connect to StarRocks

Connect to FE via MySQL client and create an S3 storage volume:

```bash
kubectl -n starrocks exec -it starrocks-shared-data-fe-0 -- \
  mysql -h localhost -P 9030 -uroot
```

```sql
-- Create MinIO storage volume (TYPE = S3, MinIO is S3-compatible)
CREATE STORAGE VOLUME minio_volume
TYPE = S3
LOCATIONS = ("s3://starrocks-data")
PROPERTIES
(
    "enabled" = "true",
    "aws.s3.region" = "us-east-1",
    "aws.s3.endpoint" = "https://minio.luban.paulhome.local:9000",
    "aws.s3.access_key" = "minio",
    "aws.s3.secret_key" = "minio123456789",
    "aws.s3.enable_partitioned_prefix" = "true"
);

-- Set as default storage volume
SET minio_volume AS DEFAULT STORAGE VOLUME;

-- Verify
SHOW STORAGE VOLUMES;
```

> **Notes**:
> - `aws.s3.region` can be any value (MinIO ignores this field)
> - `aws.s3.endpoint` points to the MinIO service address
> - For HTTP (non-TLS), remove the `https://` prefix
> - Ensure the minio_volume bucket has been created in MinIO

### 4.2 Create a Test Table

```sql
-- Create database using the storage volume
CREATE DATABASE starrocks_db;
USE starrocks_db;

-- Create table (data will be stored in MinIO)
CREATE TABLE IF NOT EXISTS test_table
(
    id          BIGINT NOT NULL,
    name        VARCHAR(50),
    amount      DECIMAL(10, 2),
    create_time DATETIME
)
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 3
PROPERTIES
(
    "storage_volume" = "minio_volume",
    "format" = "json",
    "strip_json_brackets" = "true",
    "jsonpaths" = "[\"$.id\", \"$.name\", \"$.amount\", \"$.create_time\"]"
);

-- Insert test data
INSERT INTO test_table VALUES
(1, 'item_1', 100.00, '2025-01-01 00:00:00'),
(2, 'item_2', 200.00, '2025-01-01 00:00:00'),
(3, 'item_3', 300.00, '2025-01-01 00:00:00');

-- Query to verify
SELECT * FROM test_table;
```

---

## 5. Access StarRocks

### 5.1 Internal Access (Within Cluster)

```bash
# View Service
kubectl -n starrocks get svc | grep fe

# Connect
mysql -h starrocks-shared-data-fe-service.starrocks.svc.cluster.local -P 9030 -uroot
```

### 5.2 External Access (Outside Cluster)

Change the Service type to `NodePort`:

```bash
kubectl -n starrocks edit src starrocks-shared-data
```

Add to `starRocksFeSpec`:

```yaml
starRocksFeSpec:
  service:
    type: NodePort   # or LoadBalancer
```

Connect:

```bash
mysql -h <NODE_IP> -P <NODEPORT> -uroot
```

---

## 6. Manage the Cluster

### 6.1 Upgrade CN

```bash
kubectl -n starrocks patch starrockscluster starrocks-shared-data \
  --type='merge' \
  -p '{"spec":{"starRocksCnSpec":{"image":"starrocks/cn-ubuntu:latest"}}}'
```

### 6.2 Upgrade FE

```bash
kubectl -n starrocks patch starrockscluster starrocks-shared-data \
  --type='merge' \
  -p '{"spec":{"starRocksFeSpec":{"image":"starrocks/fe-ubuntu:latest"}}}'
```

### 6.3 Manually Adjust CN Replicas

```bash
kubectl -n starrocks edit src starrocks-shared-data
```

Add `replicas` field in `starRocksCnSpec` (remove `autoScalingPolicy`):

```yaml
starRocksCnSpec:
  replicas: 5    # manually set to 5 CN nodes
```

### 6.4 Check Autoscaling Status

```bash
kubectl -n starrocks get hpa -l app.kubernetes.io/managed-by=starrocks-operator
```

Or watch Pods:

```bash
kubectl -n starrocks get pods -w -l component=cn
```

---

## 7. FAQ

### CRD size limit error

```
The CustomResourceDefinition 'starrocksclusters.starrocks.com' is invalid:
metadata.annotations: Too long: must have at most 262144 bytes
```

**Fix**: Use `kubectl create -f` for first install, `kubectl replace -f` for updates.

### Pods fail to start

```bash
kubectl logs -n starrocks <pod_name>
kubectl -n starrocks describe pod <pod_name>
```

### Check cluster status

```sql
-- Check FE status
SHOW PROC '/frontends';

-- Check CN status
SHOW PROC '/compute_nodes';
```

---

## Appendix: Resource List

| Resource | Description |
|----------|-------------|
| `starrocks-shared-data` | StarRocksCluster CR instance |
| `starrocks-shared-data-fe-*` | FE Pods (3 replicas) |
| `starrocks-shared-data-cn-*` | CN Pods (1~10, controlled by HPA) |
| `starrocks-shared-data-fe-service` | FE Service |
| `starrocks-shared-data-fe-cm` | FE Config ConfigMap |
| `starrocks-shared-data-cn-*-hpa-*` | HPA Resources (autoscaling) |

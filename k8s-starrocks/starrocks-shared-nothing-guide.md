# StarRocks on Kubernetes — Shared-Data (存算分離) 部署指南

> 參考文件：
> - [StarR Kubernetes Operator Docs (v4.1)](https://docs.starrocks.io/docs/deployment/sr_operator/)
> - [Deploy Shared-Data StarRocks Manually](https://docs.starrocks.io/docs/deployment/deploy_shared_data_manually/)
> - [Operator GitHub](https://github.com/StarRocks/starrocks-kubernetes-operator)
> - [Operator API Reference](https://github.com/StarRocks/starrocks-kubernetes-operator/blob/main/doc/api.md)

**架構**：FE + CN（存算分離），MinIO 作對象存儲，CN 開自動伸縮
**不部署到 Paul 的 cluster。**

---

## 1. 架構概述

```
┌─────────────────────────────────────────────────────────┐
│  Kubernetes Cluster                                     │
│                                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                 │
│  │   FE-0  │  │   FE-1  │  │   FE-2  │   ← 元數據管理 │
│  └────┬────┘  └─────────┘  └─────────┘                 │
│       │  Leader/Follower/Election                       │
│       │                                                 │
│  ┌──────────────────────────────────────────┐           │
│  │          Compute Nodes (CN)              │           │
│  │  ┌──────┐  ┌──────┐  ┌──────┐           │           │
│  │  │ CN-0 │  │ CN-1 │  │ CN-2 │ → HPA     │           │
│  │  └──────┘  └──────┘  └──────┘           │           │
│  └──────────────────────────────────────────┘           │
│         ↑ 查詢執行 / Local Cache                        │
│         │                                               │
│  ┌──────────────────────────────────────────┐           │
│  │         MinIO (S3 Compatible)            │           │
│  │  bucket: starrocks-data                  │           │
│  │  Access: minio:minio123456789            │           │
│  └──────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────┘
```

**關鍵區別：**
- **shared-nothing（傳統）**：FE + BE（BE 同時管存儲+計算）
- **shared-data（存算分離）**：FE + CN（CN 只管計算，數據存在 MinIO/S3）

---

## 2. 安裝 Operator

### 2.1 創建 CRD

```bash
kubectl apply -f https://raw.githubusercontent.com/StarRocks/starrocks-kubernetes-operator/main/deploy/starrocks.com_starrocksclusters.yaml
```

> **注意**：若首次安裝遇到 `Too long: must have at most 262144 bytes` 錯誤：
> ```bash
> kubectl create -f https://raw.githubusercontent.com/StarRocks/starrocks-kubernetes-operator/main/deploy/starrocks.com_starrocksclusters.yaml
> ```

### 2.2 部署 Operator

```bash
kubectl apply -f https://raw.githubusercontent.com/StarRocks/starrocks-kubernetes-operator/main/deploy/operator.yaml
```

### 2.3 驗證

```bash
kubectl -n starrocks get pods
```

預期：

```
NAME                                           READY   STATUS    RESTARTS   AGE
starrocks-controller-65bb8679-jkbtg            1/1     Running   0          5m
```

---

## 3. 創建 StarRocks 集群

### 3.1 完整 YAML 文件

創建文件 `starrocks-shared-data.yaml`：

```yaml
---
apiVersion: starrocks.com/v1
kind: StarRocksCluster
metadata:
  name: starrocks-shared-data
  namespace: starrocks
spec:
  # ─────────────────────────────────────
  # FE: Frontend — 元數據管理 + 查詢編譯
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
  # CN: Compute Node — 查詢執行 + 本地緩存
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
# FE 配置 — 設置 shared_data run_mode
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
    # 如果有 IPv4 網絡卡，取消註解：
    # priority_networks = 192.168.0.0/16
```

**重要字段說明：**

| 字段 | 說明 |
|------|------|
| `starRocksFeSpec.configMapInfo` | 透過 ConfigMap 傳遞 FE 配置（含 `run_mode = shared_data`） |
| `starRocksCnSpec.storageVolumes.cn-cache` | CN 本地緩存目錄（hot data cache），可加速查詢 |
| `starRocksCnSpec.autoScalingPolicy` | HPA 自動伸縮策略（CPU/Memory 60% 閾值） |
| `minReplicas / maxReplicas` | CN 伸縮範圍（1~10 節點） |
| `scaleDown.selectPolicy: Disabled` | 關閉自動縮容（避免頻繁伸縮影響性能） |

> **無緩存模式**：如果 CN Pod 沒有持久化存儲（如對空載伸縮），可將 `storageVolumes` 設為空：
> ```yaml
> starRocksCnSpec:
>   storageVolumes: []
> ```

### 3.2 部署

```bash
kubectl apply -f starrocks-shared-data.yaml
```

### 3.3 驗證

```bash
kubectl -n starrocks get pods -l app.kubernetes.io/managed-by=starrocks-operator
```

預期：

```
NAME                                              READY   STATUS    RESTARTS   AGE
starrocks-shared-data-fe-0                        1/1     Running   0          3m
starrocks-shared-data-fe-1                        1/1     Running   0          3m
starrocks-shared-data-fe-2                        1/1     Running   0          3m
starrocks-shared-data-cn-0                        1/1     Running   0          3m
```

---

## 4. 配置 MinIO 作為對象存儲

### 4.1 連接 StarRocks

通過 MySQL 客戶端連上 FE，創建 S3 存儲卷：

```bash
kubectl -n starrocks exec -it starrocks-shared-data-fe-0 -- \
  mysql -h localhost -P 9030 -uroot
```

```sql
-- 創建 MinIO 存儲卷（TYPE = S3，MinIO 兼容 S3 API）
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

-- 設為默認存儲卷
SET minio_volume AS DEFAULT STORAGE VOLUME;

-- 驗證
SHOW STORAGE VOLUMES;
```

> **注意**：
> - `aws.s3.region` 可隨意設（MinIO 忽略此字段）
> - `aws.s3.endpoint` 指向 MinIO 服務地址
> - 如果是 HTTP（非 TLS），去掉 `https://` 前綴
> - 確保 minio_volume 的 bucket 已在 MinIO 中創建

### 4.2 創建測試表

```sql
-- 使用存儲卷創建數據庫
CREATE DATABASE starrocks_db;
USE starrocks_db;

-- 創建表（數據將存儲在 MinIO）
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

-- 插入測試數據
INSERT INTO test_table VALUES
(1, 'item_1', 100.00, '2025-01-01 00:00:00'),
(2, 'item_2', 200.00, '2025-01-01 00:00:00'),
(3, 'item_3', 300.00, '2025-01-01 00:00:00');

-- 查詢驗證
SELECT * FROM test_table;
```

---

## 5. 訪問 StarRocks

### 5.1 內部訪問（Cluster 內）

```bash
# 查看 Service
kubectl -n starrocks get svc | grep fe

# 連接
mysql -h starrocks-shared-data-fe-service.starrocks.svc.cluster.local -P 9030 -uroot
```

### 5.2 外部訪問（Cluster 外）

修改 Service 類型為 `NodePort`：

```bash
kubectl -n starrocks edit src starrocks-shared-data
```

在 `starRocksFeSpec` 中添加：

```yaml
starRocksFeSpec:
  service:
    type: NodePort   # 或 LoadBalancer
```

連接：

```bash
mysql -h <NODE_IP> -P <NODEPORT> -uroot
```

---

## 6. 管理操作

### 6.1 升級 CN

```bash
kubectl -n starrocks patch starrockscluster starrocks-shared-data \
  --type='merge' \
  -p '{"spec":{"starRocksCnSpec":{"image":"starrocks/cn-ubuntu:latest"}}}'
```

### 6.2 升級 FE

```bash
kubectl -n starrocks patch starrockscluster starrocks-shared-data \
  --type='merge' \
  -p '{"spec":{"starRocksFeSpec":{"image":"starrocks/fe-ubuntu:latest"}}}'
```

### 6.3 手動調整 CN 副本數

```bash
# 先移除自動伸縮，改手動
kubectl -n starrocks edit src starrocks-shared-data
```

在 `starRocksCnSpec` 中新增 `replicas` 字段（移除 `autoScalingPolicy`）：

```yaml
starRocksCnSpec:
  replicas: 5    # 手動設為 5 個 CN
```

### 6.4 查看伸縮狀態

```bash
kubectl -n starrocks get hpa -l app.kubernetes.io/managed-by=starrocks-operator
```

或查看 Pod：

```bash
kubectl -n starrocks get pods -w -l component=cn
```

---

## 7. FAQ

### CRD 超限錯誤

```
The CustomResourceDefinition 'starrocksclusters.starrocks.com' is invalid:
metadata.annotations: Too long: must have at most 262144 bytes
```

**解法**：首次用 `kubectl create -f`，更新用 `kubectl replace -f`。

### Pod 無法啟動

```bash
kubectl logs -n starrocks <pod_name>
kubectl -n starrocks describe pod <pod_name>
```

### 檢查集群狀態

```sql
-- 查看 FE 狀態
SHOW PROC '/frontends';

-- 查看 CN 狀態
SHOW PROC '/compute_nodes';
```

---

## 附錄：資源清單

| 資源 | 說明 |
|------|------|
| `starrocks-shared-data` | StarRocksCluster CR 實例 |
| `starrocks-shared-data-fe-*` | FE Pod（3 副本） |
| `starrocks-shared-data-cn-*` | CN Pod（1~10，HPA 控制） |
| `starrocks-shared-data-fe-service` | FE Service |
| `starrocks-shared-data-fe-cm` | FE 配置 ConfigMap |
| `starrocks-shared-data-cn-*-hpa-*` | HPA 資源（自動伸縮） |

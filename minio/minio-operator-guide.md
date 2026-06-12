# MinIO Operator on Kubernetes — 安裝指南

> 參考文件：
> - [MinIO Operator Documentation](https://operator.min.io/)
> - [MinIO Operator GitHub](https://github.com/minio/operator)
> - [cert-manager Documentation](https://cert-manager.io/)
> - [Nginx Ingress Controller](https://kubernetes.github.io/ingress-nginx/)
> - [Envoy Gateway](https://envoyproxy.io/)

**Operator 版本**：v7.1.1
**Helm Chart 版本**：4.3.7

---

## 1. 架構概述

```
                        ┌─────────────────────┐
                        │   External Domain    │
                        │  minio.luban.paul... │
                        └──────────┬──────────┘
                                   │
                       ┌───────────▼────────────┐
                       │   Ingress Gateway       │
                       │                         │
               ┌───────┴───────┐        ┌───────▼────────┐
               │  Nginx Ingress │        │ Envoy Gateway  │
               │  (one or both) │        │                │
               └───────┬───────┘        └───────┬────────┘
                       │                         │
                       │   HTTPS (cert-manager)  │
                       │                         │
                       └─────────┬───────────────┘
                                 │
                      ┌──────────▼──────────┐
                      │  MinIO Tenant        │
                      │                      │
                      │  minio svc :9000     │
                      │  console svc :9090   │
                      └─────────────────────┘
```

**組件**：
- **cert-manager** — 自動 TLS 證書管理（Let's Encrypt 或內部 CA）
- **Nginx Ingress** — 標準 Ingress 控制器（可選其一或兩者並用）
- **Envoy Gateway** — 雲端原生服務網關（基於 Envoy）
- **MinIO Operator** — 管理 MinIO Tenant 生命週期

---

## 2. 環境準備

### 2.1 所需插件

確保集群已安裝以下組件：

```bash
# 1. cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.3/cert-manager.yaml
kubectl get pods -n cert-manager | grep cert-manager

# 2a. Nginx Ingress Controller（二選一）
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.type=NodePort

# 2b. 或 Envoy Gateway（替代選項）
# kubectl apply -f https://github.com/envoyproxy/gateway/releases/download/v1.1.0/install.yaml
# kubectl get pods -n envoy-gateway-system
```

### 2.2 StorageClass

```bash
kubectl get storageclass
```

選項：`local-path`（預設）、`nfs-csi`

### 2.3 DNS

確保您的域名指向 Ingress Gateway 的外部 IP（或 NodePort）。

k3s 環境：
```bash
kubectl get svc -n ingress-nginx -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}'
# 或
kubectl get svc -n ingress-nginx -o jsonpath='{.items[0].spec.ports[?(@.name=="https")].nodePort}'
```

---

## 3. 安裝 MinIO Operator

### 3.1 安裝 Operator

```bash
# 方法一：Kustomize（推薦）
kubectl kustomize "github.com/minio/operator?ref=v7.1.1" | kubectl apply -f -

# 方法二：Helm
helm repo add minio-tenant-csi https://operator.min.io
helm repo update
helm install minio-operator minio-tenant-csi/minio-operator \
  --namespace minio-operator \
  --create-namespace \
  --wait
```

### 3.2 驗證

```bash
kubectl get pods -n minio-operator
```

預期輸出：

```
NAME                              READY   STATUS    RESTARTS   AGE
minio-operator-69fd675557-lsrqg   1/1     Running   0          99s
```

---

## 4. 創建 MinIO Tenant

### 4.1 Tenant YAML

創建 `minio-tenant.yaml`：

```yaml
---
apiVersion: minio.min.io/v2
kind: Tenant
metadata:
  name: minio1
  namespace: minio-tenant
spec:
  image: quay.io/minio/minio:RELEASE.2025-04-08T15-41-24Z
  podManagementPolicy: Parallel
  requestAutoCert: true

  credsSecret:
    name: minio1-secret

  pools:
    - servers: 4
      name: pool-0
      volumeClaimTemplate:
        metadata:
          name: data
        spec:
          accessModes:
            - ReadWriteOnce
          storageClassName: "local-path"
          resources:
            requests:
              storage: 1Ti
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        runAsNonRoot: true
        fsGroup: 1000
        fsGroupChangePolicy: "OnRootMismatch"
      containerSecurityContext:
        runAsUser: 1000
        runAsGroup: 1000
        runAsNonRoot: true
        allowPrivilegeEscalation: false
        capabilities:
          drop:
            - ALL
        seccompProfile:
          type: RuntimeDefault

  mountPath: /export
  subPath: ""
```

### 4.2 憑證 Secret

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: minio1-secret
  namespace: minio-tenant
type: Opaque
stringData:
  username: "minio"
  password: "minio123456789"
```

### 4.3 部署

```bash
kubectl create namespace minio-tenant
kubectl apply -f minio-tenant-secret.yaml
kubectl apply -f minio-tenant.yaml
kubectl wait --for=condition=Ready tenant/minio1 -n minio-tenant --timeout=300s
```

### 4.4 驗證

```bash
kubectl get pods -n minio-tenant
kubectl get svc -n minio-tenant
```

預期 Service：

```
NAME            TYPE           CLUSTER-IP       EXTERNAL-IP   PORT(S)                     AGE
minio           ClusterIP      10.43.123.45     <none>        9090/TCP,9443/TCP           2m
myminio-console ClusterIP      10.43.234.56     <none>        9443/TCP                    2m
myminio-hl      ClusterIP      None             <none>        9000/TCP                    2m
```

---

## 5. 使用 cert-manager 管理 TLS 證書

### 5.1 創建 Issuer（或 ClusterIssuer）

Let's Encrypt 生產環境（需公共域名）：

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@example.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
      - http01:
          ingress:
            class: nginx
```

內部/自簽 CA（homelab）：

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: internal-ca
spec:
  ca:
    secretName: ca-secret
```

### 5.2 為 MinIO 創建證書

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: minio-tls
  namespace: minio-tenant
spec:
  secretName: minio-tls-cert
  duration: 2160h   # 90 天
  renewBefore: 360h  # 提前 15 天續期
  subject:
    organizations:
      - luban
  commonName: minio.luban.paulhome.local
  dnsNames:
    - minio.luban.paulhome.local
  issuerRef:
    name: letsencrypt-prod   # 或 internal-ca
    kind: ClusterIssuer
```

### 5.3 應用證書

```bash
kubectl apply -f minio-tls-cert.yaml
kubectl get certificate minio-tls -n minio-tenant
kubectl get secret minio-tls-cert -n minio-tenant
```

驗證：

```bash
kubectl describe certificate minio-tls -n minio-tenant
kubectl describe secret minio-tls-cert -n minio-tenant
```

預期 secret 類型：

```
type: kubernetes.io/tls
data:
  ca.crt     — CA 證書
  tls.crt    — MinIO 服務器證書
  tls.key    — 私鑰
```

### 5.4 配置 MinIO Tenant 使用外部證書

在 tenant spec 中添加：

```yaml
spec:
  externalCaCertSecret:
    - name: internal-ca-secret   # CA 證書 secret
      type: Opaque
  externalCertSecret:
    - name: minio-tls-cert       # TLS 證書 secret
      namespace: minio-tenant
```

然後更新 tenant：

```bash
kubectl apply -f minio-tenant.yaml
```

---

## 6. 透過 Ingress 訪問 MinIO

### 6.1 Nginx Ingress

創建 `nginx-ingress.yaml`：

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: minio-ingress
  namespace: minio-tenant
  annotations:
    # cert-manager
    cert-manager.io/cluster-issuer: letsencrypt-prod
    # Nginx 專用
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    # TLS
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - minio.luban.paulhome.local
      secretName: minio-tls-cert
  rules:
    - host: minio.luban.paulhome.local
      http:
        paths:
          # MinIO API (S3)
          - path: /
            pathType: Prefix
            backend:
              service:
                name: minio
                port:
                  number: 9090
```

部署：

```bash
kubectl apply -f nginx-ingress.yaml
```

驗證：

```bash
kubectl get ingress -n minio-tenant
kubectl describe ingress minio-ingress -n minio-tenant
```

### 6.2 Envoy Gateway Ingress

創建 `envoy-ingress.yaml`：

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: HTTPRoute
metadata:
  name: minio-route
  namespace: minio-tenant
spec:
  parentRefs:
    - name: envoy-gateway   # Envoy Gateway 實例
      sectionName: https    # HTTPS 監聽器
  hostnames:
    - "minio.luban.paulhome.local"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: minio
          port: 9090
---
# Envoy Gateway TLS 終止
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: TLSPolicy
metadata:
  name: minio-tls
  namespace: minio-tenant
spec:
  gatewaySelector:
    matchLabels:
      gateway.envoyproxy.io/owned-by: envoy-gateway
  tls:
    certificateRefs:
      - name: minio-tls-cert   # cert-manager 管理的證書
        kind: Secret
        group: ""
```

部署：

```bash
kubectl apply -f envoy-ingress.yaml
```

驗證：

```bash
kubectl get httproute -n minio-tenant
kubectl get gateway -A
```

---

## 7. 完整配置（cert-manager + Ingress）

整合所有組件的完整示例：

### 7.1 全部 YAML

創建 `minio-full.yaml`：

```yaml
---
# Tenant 命名空間
apiVersion: v1
kind: Namespace
metadata:
  name: minio-tenant

---
# MinIO Tenant 憑證
apiVersion: v1
kind: Secret
metadata:
  name: minio1-secret
  namespace: minio-tenant
type: Opaque
stringData:
  username: "minio"
  password: "minio123456789"

---
# TLS 證書（由 cert-manager 管理）
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: minio-tls
  namespace: minio-tenant
spec:
  secretName: minio-tls-cert
  duration: 2160h
  renewBefore: 360h
  commonName: minio.luban.paulhome.local
  dnsNames:
    - minio.luban.paulhome.local
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer

---
# MinIO Tenant
apiVersion: minio.min.io/v2
kind: Tenant
metadata:
  name: minio1
  namespace: minio-tenant
spec:
  image: quay.io/minio/minio:RELEASE.2025-04-08T15-41-24Z
  podManagementPolicy: Parallel
  requestAutoCert: false   # 禁用 — 改用 cert-manager

  credsSecret:
    name: minio1-secret

  # 使用 cert-manager TLS
  externalCertSecret:
    - name: minio-tls-cert

  pools:
    - servers: 4
      name: pool-0
      volumeClaimTemplate:
        metadata:
          name: data
        spec:
          accessModes:
            - ReadWriteOnce
          storageClassName: "local-path"
          resources:
            requests:
              storage: 1Ti
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        runAsNonRoot: true
        fsGroup: 1000
        fsGroupChangePolicy: "OnRootMismatch"
      containerSecurityContext:
        runAsUser: 1000
        runAsGroup: 1000
        runAsNonRoot: true
        allowPrivilegeEscalation: false
        capabilities:
          drop:
            - ALL
        seccompProfile:
          type: RuntimeDefault

  mountPath: /export
  subPath: ""

---
# Nginx Ingress
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: minio-ingress
  namespace: minio-tenant
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - minio.luban.paulhome.local
      secretName: minio-tls-cert
  rules:
    - host: minio.luban.paulhome.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: minio
                port:
                  number: 9090

---
# Envoy Gateway HTTPRoute（Nginx Ingress 的替代方案）
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: HTTPRoute
metadata:
  name: minio-route
  namespace: minio-tenant
spec:
  parentRefs:
    - name: envoy-gateway
      sectionName: https
  hostnames:
    - "minio.luban.paulhome.local"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: minio
          port: 9090
```

### 7.2 部署全部

```bash
kubectl apply -f minio-full.yaml
kubectl wait --for=condition=Ready tenant/minio1 -n minio-tenant --timeout=300s
kubectl get ingress -n minio-tenant
kubectl get httproute -n minio-tenant
```

---

## 8. 管理 Tenant

### 8.1 查看狀態

```bash
kubectl get tenant -n minio-tenant
kubectl describe tenant minio1 -n minio-tenant
kubectl get pods -n minio-tenant
```

### 8.2 新增 Pool

```yaml
spec:
  pools:
    - servers: 4
      name: pool-0
      ...
    - servers: 4
      name: pool-1
      volumeClaimTemplate:
        metadata:
          name: data
        spec:
          accessModes:
            - ReadWriteOnce
          storageClassName: "local-path"
          resources:
            requests:
              storage: 1Ti
```

```bash
kubectl apply -f minio-tenant.yaml
```

### 8.3 更新鏡像

```bash
kubectl -n minio-tenant patch tenant minio1 \
  --type='merge' \
  -p '{"spec":{"image":{"tag":"RELEASE.2025-04-08T15-41-24Z"}}}'
```

### 8.4 刪除

```bash
kubectl delete -f minio-full.yaml
```

---

## 9. 監控

### 9.1 Prometheus Metrics

在 tenant 中添加註解：

```yaml
metadata:
  annotations:
    prometheus.io/path: /minio/v2/metrics/cluster
    prometheus.io/port: "9000"
    prometheus.io/scrape: "true"
```

### 9.2 透過 Ingress 訪問 Console

新增獨立的 Ingress 規則：

```yaml
spec:
  rules:
    - host: minio-console.luban.paulhome.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: myminio-console
                port:
                  number: 9443
```

---

## 10. 故障排除

### 10.1 證書未簽發

```bash
# 查看證書狀態
kubectl describe certificate minio-tls -n minio-tenant

# 查看 cert-manager 日誌
kubectl logs -n cert-manager deployment/cert-manager

# 檢查 secret 是否已創建
kubectl get secret minio-tls-cert -n minio-tenant -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -text
```

### 10.2 Ingress 不工作

```bash
# 查看 ingress 狀態
kubectl get ingress -n minio-tenant -o wide
kubectl describe ingress minio-ingress -n minio-tenant

# 測試連通性
curl -k https://minio.luban.paulhome.local

# 查看 nginx controller 日誌
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller
```

### 10.3 Envoy Gateway 問題

```bash
# 查看 gateway 狀態
kubectl get gateway -A
kubectl describe gateway envoy-gateway -n envoy-gateway-system

# 查看 HTTPRoute
kubectl describe httproute minio-route -n minio-tenant

# 查看 envoy 日誌
kubectl logs -n envoy-gateway-system deploy/envoy -l app.kubernetes.io/name=envoy
```

### 10.4 Tenant Pod 未就緒

```bash
kubectl logs -n minio-tenant <pod-name>
kubectl describe pod <pod-name> -n minio-tenant
kubectl get pvc -n minio-tenant
```

---

## 附錄：快速參考

### kubectl 命令

```bash
# 列出所有 tenant
kubectl get tenants -A

# 查看 tenant 狀態
kubectl get tenant <name> -n <namespace> -o wide

# 查看證書
kubectl get certificate -A

# port-forward（用於除錯）
kubectl port-forward -n minio-tenant svc/minio 9000:9090
kubectl port-forward -n minio-tenant svc/myminio-console 9090:9443
```

### 常見端口

| Service | 端口 | 協議 |
|---------|------|------|
| MinIO API | 9000（集群內）/ 9090（via svc） | HTTP |
| MinIO Console | 9443 | HTTPS |
| Nginx Ingress | 80/443 | HTTP/HTTPS |
| Envoy Gateway | 80/443 | HTTP/HTTPS |

### 資源類型

| Kind | 名稱 | 命名空間 | 用途 |
|------|------|----------|------|
| `ClusterIssuer` | `letsencrypt-prod` / `internal-ca` | 集群級 | TLS 證書頒發者 |
| `Certificate` | `minio-tls` | tenant | MinIO TLS 證書 |
| `Ingress` | `minio-ingress` | tenant | Nginx Ingress 路由 |
| `HTTPRoute` | `minio-route` | tenant | Envoy Gateway 路由 |
| `Tenant` | `minio1` | tenant | MinIO 集群 |
| `Deployment` | `minio-operator` | `minio-operator` | Operator 本身 |

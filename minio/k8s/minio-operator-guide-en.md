# MinIO Operator on Kubernetes — Installation Guide

> References:
> - [MinIO Operator Documentation](https://operator.min.io/)
> - [MinIO Operator GitHub](https://github.com/minio/operator)
> - [cert-manager Documentation](https://cert-manager.io/)
> - [Nginx Ingress Controller](https://kubernetes.github.io/ingress-nginx/)
> - [Envoy Gateway](https://envoyproxy.io/)

**Operator Version**: v7.1.1
**Helm Chart Version**: 4.3.7

---

## 1. Architecture Overview

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

**Components**:
- **cert-manager** — Automatic TLS certificate management (Let's Encrypt or internal CA)
- **Nginx Ingress** — Standard Ingress controller (optionally specified, supports custom annotations)
- **Envoy Gateway** — Cloud-native service gateway (Envoy-based)
- **MinIO Operator** — Manages MinIO Tenant lifecycle

---

## 2. Prerequisites

### 2.1 Required Addons

Ensure the following are already installed on your cluster:

```bash
# 1. cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.3/cert-manager.yaml
kubectl get pods -n cert-manager | grep cert-manager

# 2a. Nginx Ingress Controller (one option)
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.type=NodePort

# 2b. OR Envoy Gateway (alternative option)
# kubectl apply -f https://github.com/envoyproxy/gateway/releases/download/v1.1.0/install.yaml
# kubectl get pods -n envoy-gateway-system
```

### 2.2 StorageClass

```bash
kubectl get storageclass
```

Options: `local-path` (default), `nfs-csi`

### 2.3 DNS

Ensure your domain points to the Ingress Gateway's external IP (or NodePort).

For k3s:
```bash
kubectl get svc -n ingress-nginx -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}'
# OR
kubectl get svc -n ingress-nginx -o jsonpath='{.items[0].spec.ports[?(@.name=="https")].nodePort}'
```

---

## 3. Install the MinIO Operator

### 3.1 Install Operator

```bash
# Method 1: Kustomize (recommended)
kubectl kustomize "github.com/minio/operator?ref=v7.1.1" | kubectl apply -f -

# Method 2: Helm
helm repo add minio-tenant-csi https://operator.min.io
helm repo update
helm install minio-operator minio-tenant-csi/minio-operator \
  --namespace minio-operator \
  --create-namespace \
  --wait
```

### 3.2 Verify

```bash
kubectl get pods -n minio-operator
```

Expected:

```
NAME                              READY   STATUS    RESTARTS   AGE
minio-operator-69fd675557-lsrqg   1/1     Running   0          99s
```

---

## 4. Create a MinIO Tenant

### 4.1 Tenant YAML

Create `minio-tenant.yaml`:

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

### 4.2 Credentials Secret

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

### 4.3 Deploy

```bash
kubectl create namespace minio-tenant
kubectl apply -f minio-tenant-secret.yaml
kubectl apply -f minio-tenant.yaml
kubectl wait --for=condition=Ready tenant/minio1 -n minio-tenant --timeout=300s
```

### 4.4 Verify

```bash
kubectl get pods -n minio-tenant
kubectl get svc -n minio-tenant
```

Expected services:

```
NAME            TYPE           CLUSTER-IP       EXTERNAL-IP   PORT(S)                     AGE
minio           ClusterIP      10.43.123.45     <none>        9090/TCP,9443/TCP           2m
myminio-console ClusterIP      10.43.234.56     <none>        9443/TCP                    2m
myminio-hl      ClusterIP      None             <none>        9000/TCP                    2m
```

---

## 5. TLS Certificates with cert-manager

### 5.1 Create Issuer (or use ClusterIssuer)

For Let's Encrypt production (requires public domain):

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

For internal/self-signed CA (homelab):

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: internal-ca
spec:
  ca:
    secretName: ca-secret
```

### 5.2 Create Certificate for MinIO

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: minio-tls
  namespace: minio-tenant
spec:
  secretName: minio-tls-cert
  duration: 2160h   # 90 days
  renewBefore: 360h  # 15 days
  subject:
    organizations:
      - luban
  commonName: minio.luban.paulhome.local
  dnsNames:
    - minio.luban.paulhome.local
  issuerRef:
    name: letsencrypt-prod   # or internal-ca
    kind: ClusterIssuer
```

### 5.3 Apply Certificate

```bash
kubectl apply -f minio-tls-cert.yaml
kubectl get certificate minio-tls -n minio-tenant
kubectl get secret minio-tls-cert -n minio-tenant
```

Verify:

```bash
kubectl describe certificate minio-tls -n minio-tenant
kubectl describe secret minio-tls-cert -n minio-tenant
```

Expected secret type:

```
type: kubernetes.io/tls
data:
  ca.crt     — CA certificate
  tls.crt    — MinIO server certificate
  tls.key    — Private key
```

### 5.4 Configure MinIO Tenant to Use External Certificates

Add to the tenant spec:

```yaml
spec:
  externalCaCertSecret:
    - name: internal-ca-secret   # CA certificate secret
      type: Opaque
  externalCertSecret:
    - name: minio-tls-cert       # TLS cert secret
      namespace: minio-tenant
```

Then update the tenant:

```bash
kubectl apply -f minio-tenant.yaml
```

---

## 6. Access MinIO via Ingress

### 6.1 Nginx Ingress

Create `nginx-ingress.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: minio-ingress
  namespace: minio-tenant
  annotations:
    # cert-manager
    cert-manager.io/cluster-issuer: letsencrypt-prod
    # Nginx-specific
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

Deploy:

```bash
kubectl apply -f nginx-ingress.yaml
```

Verify:

```bash
kubectl get ingress -n minio-tenant
kubectl describe ingress minio-ingress -n minio-tenant
```

### 6.2 Envoy Gateway Ingress

Create `envoy-ingress.yaml`:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: HTTPRoute
metadata:
  name: minio-route
  namespace: minio-tenant
spec:
  parentRefs:
    - name: envoy-gateway   # the Envoy Gateway instance
      sectionName: https    # HTTPS listener
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
# TLS termination at Envoy Gateway
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
      - name: minio-tls-cert   # cert-manager managed cert
        kind: Secret
        group: ""
```

Deploy:

```bash
kubectl apply -f envoy-ingress.yaml
```

Verify:

```bash
kubectl get httproute -n minio-tenant
kubectl get gateway -A
```

---

## 7. Full Configuration (cert-manager + Ingress)

Complete example combining all components:

### 7.1 All-in-One YAML

Create `minio-full.yaml`:

```yaml
---
# Tenant namespace
apiVersion: v1
kind: Namespace
metadata:
  name: minio-tenant

---
# MinIO Tenant credentials
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
# TLS Certificate (managed by cert-manager)
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
  requestAutoCert: false   # disabled — using cert-manager

  credsSecret:
    name: minio1-secret

  # Use cert-manager TLS
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
# Envoy Gateway HTTPRoute (alternative to Nginx Ingress)
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

### 7.2 Deploy All

```bash
kubectl apply -f minio-full.yaml
kubectl wait --for=condition=Ready tenant/minio1 -n minio-tenant --timeout=300s
kubectl get ingress -n minio-tenant
kubectl get httproute -n minio-tenant
```

---

## 8. Manage the Tenant

### 8.1 Check Status

```bash
kubectl get tenant -n minio-tenant
kubectl describe tenant minio1 -n minio-tenant
kubectl get pods -n minio-tenant
```

### 8.2 Add a Pool

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

### 8.3 Update Image

```bash
kubectl -n minio-tenant patch tenant minio1 \
  --type='merge' \
  -p '{"spec":{"image":{"tag":"RELEASE.2025-04-08T15-41-24Z"}}}'
```

### 8.4 Delete

```bash
kubectl delete -f minio-full.yaml
```

---

## 9. Monitoring

### 9.1 Prometheus Metrics

Add annotations to the tenant:

```yaml
metadata:
  annotations:
    prometheus.io/path: /minio/v2/metrics/cluster
    prometheus.io/port: "9000"
    prometheus.io/scrape: "true"
```

### 9.2 Console Access via Ingress

Add a separate Ingress rule for the console:

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

## 10. Troubleshooting

### 10.1 Certificate Not Issued

```bash
# Check certificate status
kubectl describe certificate minio-tls -n minio-tenant

# Check cert-manager logs
kubectl logs -n cert-manager deployment/cert-manager

# Check if the secret was created
kubectl get secret minio-tls-cert -n minio-tenant -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -text
```

### 10.2 Ingress Not Working

```bash
# Check ingress status
kubectl get ingress -n minio-tenant -o wide
kubectl describe ingress minio-ingress -n minio-tenant

# Test connectivity
curl -k https://minio.luban.paulhome.local

# Check nginx controller logs
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller
```

### 10.3 Envoy Gateway Issues

```bash
# Check gateway status
kubectl get gateway -A
kubectl describe gateway envoy-gateway -n envoy-gateway-system

# Check HTTPRoute
kubectl describe httproute minio-route -n minio-tenant

# Check envoy logs
kubectl logs -n envoy-gateway-system deploy/envoy -l app.kubernetes.io/name=envoy
```

### 10.4 Tenant Pods Not Ready

```bash
kubectl logs -n minio-tenant <pod-name>
kubectl describe pod <pod-name> -n minio-tenant
kubectl get pvc -n minio-tenant
```

---

## Appendix: Quick Reference

### kubectl Commands

```bash
# List tenants
kubectl get tenants -A

# Get tenant status
kubectl get tenant <name> -n <namespace> -o wide

# Check certificates
kubectl get certificate -A

# Port-forward (for debugging)
kubectl port-forward -n minio-tenant svc/minio 9000:9090
kubectl port-forward -n minio-tenant svc/myminio-console 9090:9443
```

### Common Ports

| Service | Port | Protocol |
|---------|------|----------|
| MinIO API | 9000 (in-cluster) / 9090 (via svc) | HTTP |
| MinIO Console | 9443 | HTTPS |
| Nginx Ingress | 80/443 | HTTP/HTTPS |
| Envoy Gateway | 80/443 | HTTP/HTTPS |

### Resource Types

| Kind | Name | Namespace | Purpose |
|------|------|-----------|---------|
| `ClusterIssuer` | `letsencrypt-prod` / `internal-ca` | cluster-wide | TLS certificate issuer |
| `Certificate` | `minio-tls` | tenant | MinIO TLS certificate |
| `Ingress` | `minio-ingress` | tenant | Nginx Ingress routing |
| `HTTPRoute` | `minio-route` | tenant | Envoy Gateway routing |
| `Tenant` | `minio1` | tenant | MinIO cluster |
| `Deployment` | `minio-operator` | `minio-operator` | Operator itself |

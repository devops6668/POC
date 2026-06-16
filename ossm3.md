# Step-by-Step Migration Guide: OpenShift Service Mesh 2 to Service Mesh 3
### Version 2.0 — Updated with Centralised Gateway Architecture (On-Premises / F5)

**Environment:** Production OpenShift Cluster — `lab.devops.local` (On-Premises)
**Source Version:** OpenShift Service Mesh 2.6.11 → must upgrade to **2.6.14** first
**Target Version:** OpenShift Service Mesh 3.0 (Istio 1.24.x)
**Deployment Model:** MultiTenant (ServiceMeshMemberRoll-based)
**Ingress Architecture:** F5 BIG-IP → OpenShift Infra Nodes (HAProxy Router) → Centralised `istio-ingressgateway`

---

## Table of Contents

1. [Environment Summary](#1-environment-summary)
2. [Architecture Analysis: Preserving the F5 Ingress Flow](#2-architecture-analysis-preserving-the-f5-ingress-flow)
3. [Key Architectural Changes (OSSM 2 → OSSM 3)](#3-key-architectural-changes-ossm-2--ossm-3)
4. [Pre-Migration Checklist](#4-pre-migration-checklist)
4.5. [Gateway Injection Migration (If Still Using SMCP-Defined Gateways)](#45-gateway-injection-migration-if-still-using-smcp-defined-gateways)
4.6. [Configure Replacement Observability Components](#46-configure-replacement-observability-components)
4.7. [Disable IOR (Automatic Route Creation)](#47-disable-ior-automatic-route-creation)
4.8. [Disable Network Policy Management](#48-disable-network-policy-management)
4.9. [Pre-Create Network Policies (Optional but Recommended)](#49-pre-create-network-policies-optional-but-recommended)
4.10. [Verify SMCP State After Pre-Migration Steps](#410-verify-smcp-state-after-pre-migration-steps)
5. [Phase 1 — Prepare the OSSM 2 Control Plane](#5-phase-1--prepare-the-ossm-2-control-plane)
6. [Phase 2 — Install OSSM 3 Operator and IstioCNI](#6-phase-2--install-ossm-3-operator-and-istiocni)
7. [Phase 3 — Deploy the OSSM 3 Control Plane (Istio Resource)](#7-phase-3--deploy-the-ossm-3-control-plane-istio-resource)
8. [Phase 4 — Migrate the Gateway to OSSM 3](#8-phase-4--migrate-the-gateway-to-ossm-3)
9. [Phase 5 — Migrate Workload Namespaces](#9-phase-5--migrate-workload-namespaces)
10. [Phase 6 — Update Gateway Resources and Finalise](#10-phase-6--update-gateway-resources-and-finalise)
11. [Phase 7 — Complete the Migration and Clean Up](#11-phase-7--complete-the-migration-and-clean-up)
12. [Post-Migration Validation](#12-post-migration-validation)
13. [Configuration Mapping Reference](#13-configuration-mapping-reference)
14. [Rollback Procedure](#14-rollback-procedure)
15. [Telemetry CRD Configuration](#15-telemetry-crd-configuration)
16. [Gateway Migration Strategy Note](#16-gateway-migration-strategy-note)
17. [cert-manager Migration (Optional)](#17-cert-manager-migration-optional)
18. [References](#references)

---

## 1. Environment Summary

### 1.1 Control Plane

| Item | Current Value |
|---|---|
| SMCP Name | `basic` |
| Control Plane Namespace | `istio-system` |
| OSSM Version | `v2.6` (Operator: `OSSM_2.6.11`) |
| Deployment Mode | **MultiTenant** (ServiceMeshMemberRoll) |
| Control Plane mTLS | Enabled (`controlPlane.mtls: true`, `dataPlane.mtls: true`) |
| TLS Cipher Suites | 8 custom suites configured |
| Tracing | Jaeger (`type: Jaeger`, sampling: `10000` = 100%) |
| Prometheus / Grafana / Kiali | All enabled as built-in add-ons |
| Network Policy Management | Enabled (`manageNetworkPolicy: true`) |
| OpenShift Route (IOR) | Enabled (`openshiftRoute.enabled: true`) |
| Istiod Replicas | 2 (on infra nodes) |
| Proxy Resources | Requests: 10m CPU / 10Mi RAM; Limits: 200m CPU / 512Mi RAM |
| Termination Drain Duration | 60s |
| Auto-inject | `false` (pod-level annotation `sidecar.istio.io/inject: \"true\"` used) |

### 1.2 Mesh Member Namespaces

| Namespace | Status | Workloads |
|---|---|---|
| `devops` | Configured | — |
| `project-01` | Configured | `hello-world1`, `hello-world2` |
| `project-02` | Configured | `hello-world3`, `hello-world4` |
| `project-03` | Configured | `hello-world5`, `hello-world6` |
| `project-04` | Configured | `hello-world7`, `hello-world8` |
| `test` | Pending (namespace does not exist) | — |

### 1.3 Current Gateway Configuration

All four Istio `Gateway` resources live in their respective project namespaces and use the selector `istio: ingressgateway`, which targets the **single centralised** `istio-ingressgateway` pod running in `istio-system`. TLS is terminated at the gateway using **SIMPLE mode** with per-host `credentialName` Secrets.

| Gateway Resource | Namespace | Hosts | TLS Mode | Credential Secrets |
|---|---|---|---|---|
| `project-01-https-gateway` | `project-01` | `hello-world1.devops.local`, `hello-world2.devops.local` | SIMPLE | `hello-world1`, `hello-world2` |
| `project-02-https-gateway` | `project-02` | `hello-world3.devops.local`, `hello-world4.devops.local` | SIMPLE | `hello-world3`, `hello-world4` |
| `project-03-https-gateway` | `project-03` | `hello-world5.devops.local`, `hello-world6.devops.local` | SIMPLE | `hello-world5`, `hello-world6` |
| `project-04-https-gateway` | `project-04` | `hello-world7.devops.local`, `hello-world8.devops.local` | SIMPLE | `hello-world7`, `hello-world8` |

> **Important:** TLS mode is `SIMPLE` (not `PASSTHROUGH`). This means TLS is **terminated at the Istio gateway**, not at the OpenShift HAProxy Router. The OpenShift Routes generated by IOR use `tls.termination: passthrough` at the Router level, so the encrypted traffic passes through HAProxy and is decrypted by the Istio gateway pod. This is the standard pattern for F5 → Router (passthrough) → Istio gateway (TLS termination).

### 1.4 IOR-Generated Routes (in `istio-system`)

| Route | Host | TLS at Router | Target Service |
|---|---|---|---|
| `project-01-...-9b5b7eccb027a7d6` | `hello-world1.devops.local` | passthrough | `istio-ingressgateway` |
| `project-01-...-7df5bdb846ec88bd` | `hello-world2.devops.local` | passthrough | `istio-ingressgateway` |
| `project-02-...-70a02a424be5fc30` | `hello-world3.devops.local` | passthrough | `istio-ingressgateway` |
| `project-02-...-46d6fe5c65f80e14` | `hello-world4.devops.local` | passthrough | `istio-ingressgateway` |
| `project-03-...-c538c33c479ca7ca` | `hello-world6.devops.local` | passthrough | `istio-ingressgateway` |
| `project-03-...-60f1b0c2ce6ed7f2` | `hello-world5.devops.local` | passthrough | `istio-ingressgateway` |
| `project-04-...-5a4a7574dc421c6f` | `hello-world7.devops.local` | passthrough | `istio-ingressgateway` |
| `project-04-...-d95ee210fcd81601` | `hello-world8.devops.local` | passthrough | `istio-ingressgateway` |

---

## 2. Architecture Analysis: Preserving the F5 Ingress Flow

### 2.1 Current Traffic Flow (OSSM 2)

```
External Client
      │
      ▼
F5 BIG-IP (on-premises load balancer)
      │  (routes *.devops.local to OpenShift infra node VIPs)
      ▼
OpenShift HAProxy Router (on infra nodes)
      │  (Route: tls.termination=passthrough → istio-ingressgateway Service in istio-system)
      ▼
istio-ingressgateway Pod (in istio-system, on infra nodes)
      │  (TLS terminated here using SIMPLE mode + credentialName Secret)
      ▼
Application Pod (in project-01..04, via VirtualService routing)
```

### 2.2 Your Concern: Can You Keep This Flow in OSSM 3?

**Yes, absolutely.** OSSM 3 fully supports a single centralised ingress gateway. In fact, this is the **recommended pattern** for on-premises environments with a shared load balancer. The key insight is:

In OSSM 2, the `istio-ingressgateway` was a pod managed by the SMCP. In OSSM 3, it becomes a **standalone Deployment** that you manage yourself, but it can still:
- Stay in `istio-system` during migration (recommended), then optionally move to a dedicated `istio-ingress` namespace post-migration
- Run **exclusively on infra nodes** using `nodeSelector` and `tolerations`
- Expose a **single ClusterIP Service** named `istio-ingressgateway` with the same label `istio: ingressgateway`
- Be the target of **OpenShift Routes** with `tls.termination: passthrough`, exactly as today
- Serve **all four project namespaces** from a single gateway pod, exactly as today

The F5 → Router → Gateway flow is **unchanged**. The only differences are:
1. The gateway pod is no longer managed by the SMCP; you manage its Deployment directly.
2. The OpenShift Routes must be created manually (IOR is removed), but they point to the same gateway Service — initially in `istio-system`, optionally in `istio-ingress` post-migration.
3. TLS Secrets remain in `istio-system` during migration; if you move the gateway to `istio-ingress` post-migration, they must be copied there because the gateway reads Secrets from its own namespace via SDS.

### 2.3 Recommended Gateway Namespace Strategy

| Option | Description | Recommendation |
|---|---|---|
| **`istio-system`** (same namespace) | Gateway Deployment stays alongside istiod during migration. Service, Routes, and Secrets remain unchanged. Simplest path. | **Recommended for migration** — zero changes to existing infrastructure |
| `istio-ingress` (dedicated) | Gateway Deployment in a separate namespace. Cleaner separation, but requires copying Secrets and recreating Routes. | **Only after migration complete** (see Section 10.4) |

This guide keeps the gateway in `istio-system` throughout the migration (Phase 4) and optionally moves it to `istio-ingress` post-migration (Section 10.4).

### 2.4 Gateway Selector Compatibility

Your existing Istio `Gateway` resources use `selector: istio: ingressgateway`. The new standalone gateway Deployment will carry the label `istio: ingressgateway`, so **the existing Gateway resources require no selector change**. The only update needed is to ensure the `Gateway` resources reference the correct namespace when using cross-namespace VirtualService bindings.

---

## 3. Key Architectural Changes (OSSM 2 → OSSM 3)

| Aspect | OSSM 2.6 | OSSM 3.0 |
|---|---|---|
| Operator basis | Maistra (midstream) | Upstream Istio (Sail Operator) |
| Control plane resource | `ServiceMeshControlPlane` | `Istio` (sailoperator.io/v1) |
| CNI management | Per-version CNI, auto-managed | Separate `IstioCNI` resource (shared) |
| Namespace enrolment | `ServiceMeshMemberRoll` | `discoverySelectors` labels on namespaces |
| Observability (Prometheus, Grafana, Jaeger) | Built-in SMCP add-ons | Installed separately by their own Operators |
| Kiali | Built-in SMCP add-on | Managed by Kiali Operator (standalone) |
| Route management | IOR (automatic) | **Manual** Route creation required |
| Gateway management | SMCP-defined, in `istio-system` | **Standalone Deployment** (initially in same namespace for canary; optionally moved to `istio-ingress` post-migration) |
| TLS Secret location | `istio-system` | **Gateway namespace** (stays in `istio-system` during canary; copied to `istio-ingress` if moving post-migration) |
| mTLS strict mode | `spec.security.dataPlane.mtls: true` | `PeerAuthentication` + `DestinationRule` resources |
| Network policy management | Auto-managed by SMCP | **Manual** — administrator responsibility |
| Sidecar injection trigger | Pod annotation `sidecar.istio.io/inject: \"true\"` | Namespace/pod **labels** |
| DNS capture | Enabled by default | Must be **explicitly enabled** via `proxyMetadata` |
| Update strategy | In-place only | In-place (`InPlace`) or canary (`RevisionBased`) |

---

## 4. Pre-Migration Checklist

Complete every item before installing the OSSM 3 Operator.

### 4.1 Version and Access Prerequisites

- [ ] Verify OSSM 2 is at **2.6.14** (current environment is on 2.6.11 — upgrade required):
  ```bash
  oc get smcp basic -n istio-system -o jsonpath='{.status.chartVersion}'
  # Must output: 2.6.14
  ```
- [ ] Switch Operator updates to **Manual** in the OpenShift web console.
- [ ] Verify OCP version is **4.14 or later**:
  ```bash
  oc version | grep \"Server Version\"
  ```
- [ ] Install **`istioctl`** matching Istio 1.24.x (download from Red Hat customer portal).
- [ ] Confirm `cluster-admin` access:
  ```bash
  oc auth can-i '*' '*' --all-namespaces
  ```

### 4.2 Validate ServiceEntry Resources

OSSM 3 (Istio 1.24) blocks installation if any `ServiceEntry` has more than 256 hostnames or missing ports [1]:
```bash
# Check for ServiceEntry with >256 hostnames
oc get serviceentries -A -o json | jq -r '.items[] | select(.spec.hosts | length > 256) | "\\(.metadata.namespace)/\\(.metadata.name): \\( .spec.hosts | length) hosts"'

# Check for ServiceEntry with missing port numbers
oc get serviceentries -A -o json | jq -r '.items[] | select(.spec.ports == null or (.spec.ports | length == 0)) | "\\(.metadata.namespace)/\\(.metadata.name)"'
```
Remediate any findings before proceeding.

### 4.3 Inventory TLS Secrets

Your gateways use SIMPLE TLS mode with `credentialName` Secrets. These Secrets currently reside in `istio-system`. During migration they stay in `istio-system` (Phase 4); if you later move the gateway to a dedicated namespace (Section 10.4), they will be copied there.

```bash
oc get secrets -n istio-system | grep -E \"hello-world[0-9]\"
```

Expected output: Secrets named `hello-world1` through `hello-world8`. These stay in `istio-system` during migration.

### 4.4 Disable Add-ons in the OSSM 2 SMCP

The current SMCP has Prometheus, Grafana, Kiali, and Jaeger enabled. Disable them all:

```bash
# Disable Prometheus
oc patch smcp basic -n istio-system --type merge \\
  -p '{"spec":{"addons":{"prometheus":{"enabled":false}}}}'

# Disable Grafana
oc patch smcp basic -n istio-system --type merge \\
  -p '{"spec":{"addons":{"grafana":{"enabled":false}}}}'

# Disable Kiali
oc patch smcp basic -n istio-system --type merge \\
  -p '{"spec":{"addons":{"kiali":{"enabled":false}}}}'

# Disable Jaeger tracing
oc patch smcp basic -n istio-system --type merge \\
  -p '{"spec":{"tracing":{"type":"None"}}}'
```

Verify the SMCP reconciles successfully:
```bash
oc get smcp basic -n istio-system
# STATUS column must show: ComponentsReady
```

### 4.5 Gateway Injection Migration (If Still Using SMCP-Defined Gateways)

> **Critical:** Red Hat OpenShift Service Mesh 3 does not manage gateways through the control plane. If your current gateway is still defined in the `ServiceMeshControlPlane` resource (SMCP), you **must** migrate it to a standalone Deployment using gateway injection **before** proceeding to Phase 2.
>
> This procedure is based on the official Red Hat OSSM 2.x documentation, Section 2.15.2 — "Migrate from SMCP-Defined gateways to gateway injection" [Ref 6].

#### 4.5.1. Check if Your Gateway is SMCP-Defined

```bash
oc get smcp basic -n istio-system -o jsonpath='{.spec.gateways}' | python3 -m json.tool
```

If the output contains `ingress:` with deployment configuration (e.g., `runtime`, `deployment`, `replicas`), your gateway is SMCP-managed and you need the steps below.

> **If your gateway is already a standalone Deployment** (not managed by SMCP), skip this section entirely.

#### 4.5.2. Create the Canary Gateway Deployment

Create a new gateway Deployment that uses gateway injection. Deploy it in the **same namespace** as the SMCP-defined gateway (`istio-system`).

```yaml
# canary-gateway.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: istio-ingressgateway-canary
  namespace: istio-system
spec:
  selector:
    matchLabels:
      app: istio-ingressgateway
      istio: ingressgateway
  template:
    metadata:
      annotations:
        inject.istio.io/templates: gateway
      labels:
        app: istio-ingressgateway
        istio: ingressgateway
        sidecar.istio.io/inject: "true"
    spec:
      containers:
      - name: istio-proxy
        image: auto
      serviceAccountName: istio-ingressgateway
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: istio-ingressgateway
  namespace: istio-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: secret-reader
  namespace: istio-system
rules:
- apiGroups: [""]
  resources: ["secrets"]
  verbs: ["get", "watch", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: istio-ingressgateway-secret-reader
  namespace: istio-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: secret-reader
subjects:
- kind: ServiceAccount
  name: istio-ingressgateway
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gatewayingress
  namespace: istio-system
spec:
  podSelector:
    matchLabels:
      istio: ingressgateway
  ingress:
  - {}
  policyTypes:
  - Ingress
```

> **Notes:**
> 1. The gateway injection deployment and all supporting resources must be in the **same namespace** as the SMCP-defined gateway.
> 2. The pod template labels must include **all label selectors** from the existing Service object (`app: istio-ingressgateway, istio: ingressgateway`).
> 3. The NetworkPolicy grants external access to the new gateway. This is required when `spec.security.manageNetworkPolicy` is `true` (the default).

Apply it:

```bash
oc apply -f canary-gateway.yaml
```

#### 4.5.3. Verify the Canary Gateway

```bash
# Check pods are running
oc get pods -n istio-system -l app=istio-ingressgateway

# Verify with istioctl
istioctl ps -n istio-system | grep canary

# Test a sample route through the gateway
curl -sk https://hello-world1.devops.local/ | head -5
```

#### 4.5.4. Gradually Shift Traffic

Scale up the canary and scale down the old SMCP-managed gateway:

```bash
# Increase canary replicas
oc scale -n istio-system deployment/istio-ingressgateway-canary --replicas 3

# Decrease old gateway replicas
oc scale -n istio-system deployment/istio-ingressgateway --replicas 0
```

> **Repeat incrementally:** Adjust replica counts until the canary handles all traffic. Monitor the old gateway's access logs to confirm traffic has shifted.

#### 4.5.5. Detach the Service Object from SMCP Management

After confirming the canary handles all traffic, detach the existing `istio-ingressgateway` Service from the SMCP so it won't be deleted when the old gateway is disabled.

```bash
# Remove the managed-by label
oc label service -n istio-system istio-ingressgateway app.kubernetes.io/managed-by-

# Remove ownerReferences (prevents garbage collection)
oc patch service -n istio-system istio-ingressgateway --type='json' \
  -p '[{"op": "remove", "path": "/metadata/ownerReferences"}]'
```

#### 4.5.6. Disable the Old SMCP-Managed Gateway

```bash
oc patch smcp basic -n istio-system --type='json' \
  -p '[{"op": "replace", "path": "/spec/gateways/ingress/enabled", "value": false}]'
```

> **Note:** When the old ingress gateway Service is disabled, it is **not deleted**. You may save this Service object to a file and manage it alongside the new gateway injection resources.
>
> The `/spec/gateways/ingress/enabled` path is available if you explicitly set it. If using the default value, patch `/spec/gateways/enabled` for both ingress and egress.

#### 4.5.7. Verify the Migration

```bash
# Confirm old gateway is disabled
oc get smcp basic -n istio-system -o jsonpath='{.spec.gateways.ingress.enabled}'
# Should output: false

# Confirm canary gateway is handling traffic
oc get pods -n istio-system -l app=istio-ingressgateway
# Should show only the canary deployment

# Test all endpoints
for i in 1 2 3 4 5 6 7 8; do
  echo -n "hello-world${i}.devops.local: "
  curl -sk -o /dev/null -w "%{http_code}\n" https://hello-world${i}.devops.local/
done
# All should return 200
```

#### 4.5.8. What Comes Next

After completing this gateway injection migration, your environment is ready for the **OSSM 3 migration**. The canary gateway Deployment (`istio-ingressgateway-canary`) becomes your working gateway. You may rename it to `istio-ingressgateway` (deleting the old SMCP-managed one):

```bash
# Delete the old SMCP-managed deployment (now at 0 replicas)
oc delete deployment istio-ingressgateway -n istio-system --ignore-not-found

# Rename canary to the standard name
# (Actually just use a new manifest — Kubernetes doesn't support rename)
# Create a final deployment named istio-ingressgateway retaining all settings
```

Proceed to **Phase 4** to migrate this gateway from OSSM 2 to OSSM 3 via canary/in-place.



### 4.6 Configure Replacement Observability Components

Before migrating, install and configure the following replacements:

- **Metrics:** Configure OpenShift User Workload Monitoring as the Prometheus replacement. See "Integration with user-workload monitoring" [1].
- **Distributed Tracing:** Install the Red Hat OpenShift Distributed Tracing Platform (Tempo) and Red Hat build of OpenTelemetry [1].
- **Kiali:** Install a standalone Kiali resource using the Kiali Operator provided by Red Hat. Remove deprecated namespace settings (`spec.deployment.accessible_namespaces`, `api.namespaces.*`) from the Kiali CR.

### 4.7 Disable IOR (Automatic Route Creation)

IOR is not present in OSSM 3. Disable it in OSSM 2 now so you can manage Routes manually going forward:

```bash
oc patch smcp basic -n istio-system --type merge \\
  -p '{"spec":{"gateways":{"openshiftRoute":{"enabled":false}}}}'
```

> **Note:** Disabling IOR does **not** delete existing Routes. The 8 auto-generated Routes in `istio-system` will remain. If you choose to move the gateway to a dedicated namespace later (Section 10.4), you will delete these and create replacement Routes in the new namespace. Plan a brief maintenance window for that step — between deleting old Routes and applying new ones, the hostnames will be temporarily unreachable.

### 4.8 Disable Network Policy Management

```bash
oc patch smcp basic -n istio-system --type merge \\
  -p '{"spec":{"security":{"manageNetworkPolicy":false}}}'
```

> **Warning:** This removes the network policies that OSSM 2 created automatically. If your security policy requires network isolation to be maintained throughout the migration, pre-create the network policies described in Section 4.8 **before** running this command.

### 4.9 Pre-Create Network Policies (Optional but Recommended)

Label all member namespaces with a mesh-scoped label:
```bash
for ns in istio-system devops project-01 project-02 project-03 project-04; do
  oc label namespace $ns service-mesh=enabled --overwrite
done
```

Create the following NetworkPolicy resources. Apply the Istiod policy for OSSM 2 in `istio-system`:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: istiod-basic-ossm2
  namespace: istio-system
spec:
  ingress:
  - {}
  podSelector:
    matchLabels:
      app: istiod
      istio.io/rev: basic
  policyTypes:
  - Ingress
```

Create a mesh ingress policy in `istio-system` allowing traffic from all member namespaces:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: istio-mesh
  namespace: istio-system
spec:
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          service-mesh: enabled
  podSelector: {}
  policyTypes:
  - Ingress
```

Create the same `istio-mesh` NetworkPolicy in each application namespace (`project-01` through `project-04`), changing the `namespace` field accordingly.

### 4.10 Verify SMCP State After Pre-Migration Steps

```bash
oc get smcp basic -n istio-system -o jsonpath='{.spec}' | python3 -m json.tool | grep -E \"enabled|type|manageNetwork\"
```

Confirm the following values are set:
- `addons.grafana.enabled: false`
- `addons.kiali.enabled: false`
- `addons.prometheus.enabled: false`
- `tracing.type: None`
- `gateways.openshiftRoute.enabled: false`
- `security.manageNetworkPolicy: false`

---


---


---
## 5. Phase 1 — Prepare the OSSM 2 Control Plane

**Step 1.1:** Confirm the SMCP is healthy after all pre-migration patches:
```bash
oc get smcp basic -n istio-system
```
Expected:
```
NAME    READY   STATUS            PROFILES      VERSION   AGE
basic   9/9     ComponentsReady   [\"default\"]   2.6.14    ...
```

**Step 1.2:** Confirm the ServiceMeshMemberRoll lists the correct namespaces:
```bash
oc get smmr default -n istio-system -o jsonpath='{.spec.members}'
# Expected: [\"devops\",\"project-01\",\"project-02\",\"project-03\",\"project-04\",\"test\"]
```

**Step 1.3:** Confirm all application workloads are running:
```bash
for ns in project-01 project-02 project-03 project-04; do
  echo \"=== $ns ===\"; oc get pods -n $ns
done
```

**Step 1.4:** Confirm all proxies are connected to the OSSM 2 control plane:
```bash
istioctl ps --istioNamespace istio-system --revision basic
```
All `hello-world` pods should appear, connected to `istiod-basic-*`.

---

## 6. Phase 2 — Install OSSM 3 Operator and IstioCNI

**Step 2.1:** Install the **Red Hat OpenShift Service Mesh 3 Operator** from OperatorHub. In the web console, navigate to **Operators → OperatorHub**, search for \"OpenShift Service Mesh\", select version 3.x, install into `openshift-operators`, and set update approval to **Manual**.

Alternatively, apply the Subscription:
```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: servicemeshoperator3
  namespace: openshift-operators
spec:
  channel: stable
  installPlanApproval: Manual
  name: servicemeshoperator3
  source: redhat-operators
  sourceNamespace: openshift-marketplace
```

**Step 2.2:** Approve the install plan:
```bash
oc get installplan -n openshift-operators
oc patch installplan <install-plan-name> -n openshift-operators \\
  --type merge -p '{"spec":{"approved":true}}'
```

**Step 2.3:** Verify the OSSM 3 Operator pod is running:
```bash
oc get pods -n openshift-operators | grep servicemesh
```

**Step 2.4:** Create the **IstioCNI** resource. This is a new resource in OSSM 3 that manages the Istio CNI node agent as a DaemonSet shared by all Istio control planes [1]:

```yaml
apiVersion: sailoperator.io/v1
kind: IstioCNI
metadata:
  name: default
spec:
  version: v1.24.3
  namespace: istio-cni
  profile: openshift
```

```bash
oc apply -f istiocni.yaml
```

**Step 2.5:** Verify the IstioCNI DaemonSet is running on all nodes:
```bash
oc get istiocni default
oc get pods -n istio-cni -o wide
# Confirm pods are running on all nodes including infra nodes
```

---

## 7. Phase 3 — Deploy the OSSM 3 Control Plane (Istio Resource)

The OSSM 3 Istio resource **must be deployed in the same namespace as the OSSM 2 SMCP** (`istio-system`). This is critical because both control planes must share the same root certificate (`istio-ca-secret`) to maintain mTLS continuity between workloads during the migration period [1].

### 7.1 Construct the Istio Resource

> **Tip:** Run `oc explain istios.spec.values` to view the full validation schema of the Istio resource. This is helpful when translating SMCP settings to the new format.

The following Istio resource translates all relevant settings from your SMCP `basic` configuration to the OSSM 3 format, including settings from your actual SMCP that were not captured in earlier versions of this guide. It uses `RevisionBased` update strategy, which is recommended for production because it enables canary-style migration and safe rollback.

Create a file named `ossm3-istio.yaml`:

```yaml
apiVersion: sailoperator.io/v1
kind: Istio
metadata:
  name: basic
  namespace: istio-system          # MUST match the OSSM 2 SMCP namespace (hard requirement)
spec:
  updateStrategy:
    type: RevisionBased            # Enables canary migration; creates named IstioRevision
  version: v1.24.3       # Use v1.24-latest for automatic patch updates (e.g., v1.24.x)
  values:
    meshConfig:
      # discoverySelectors scopes the control plane to the same namespaces
      # as the OSSM 2 ServiceMeshMemberRoll. We use service-mesh=enabled label
      # (applied in Section 4.8) instead of a per-tenant label because all
      # namespaces share a single OSSM 3 control plane. The label value
      # can be any key/value pair as long as it matches the labels applied
      # to each dataplane namespace in subsequent phases.
      discoverySelectors:
        - matchLabels:
            service-mesh: enabled


      # Enable Prometheus metrics merge (matches OSSM 2 default behaviour)
      enablePrometheusMerge: true

      # Enable tracing (matches OSSM 2: tracing.type: Jaeger)
      enableTracing: true

      # DNS refresh rate — matches OSSM 2 (proxy.networking.dns.refreshRate: 300s)
      dnsRefreshRate: 300s

      # DNS capture must be explicitly enabled in OSSM 3.
      # In OSSM 2 this was enabled by default (via techPreview.meshConfig.defaultConfig.proxyMetadata).
      # Required for any ServiceEntry resources that rely on DNS resolution.
      defaultConfig:
        proxyMetadata:
          ISTIO_META_DNS_AUTO_ALLOCATE: "true"
          ISTIO_META_DNS_CAPTURE: "true"
        # Migrated from: spec.techPreview.meshConfig.defaultConfig.terminationDrainDuration
        terminationDrainDuration: 60s

      # Extension providers for observability.
      # Update the otel entry after installing your OpenTelemetry Collector.
      extensionProviders:
        - name: prometheus
          prometheus: {}
        # - name: otel
        #   opentelemetry:
        #     port: 4317
        #     service: otel-collector.opentelemetrycollector-3.svc.cluster.local


      # TLS defaults (migrated from spec.security.controlPlane.tls)
      tlsDefaults:
        minProtocolVersion: TLSv1_2
        cipherSuites:
          - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256
          - TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256
          - TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
          - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
          - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
          - TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384
          - TLS_RSA_WITH_AES_128_GCM_SHA256
          - TLS_RSA_WITH_AES_256_GCM_SHA384

    global:
      # Migrated from: spec.proxy.runtime.container.resources
      proxy:
        resources:
          limits:
            cpu: 200m
            memory: 512Mi
          requests:
            cpu: 10m
            memory: 10Mi
        # Migrated from: appliedValues.istio.global.proxy.logLevel: warning
        logLevel: warning


      # Global infra node defaults — matches OSSM 2 behaviour where all
      # mesh components without explicit nodeSelector land on infra nodes.
      # Migrated from: appliedValues.istio.global.defaultNodeSelector
      # and appliedValues.istio.global.defaultTolerations
      defaultNodeSelector:
        node-role.kubernetes.io/infra: ''
      defaultTolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved

    pilot:
      # Migrated from: spec.runtime.components.pilot.deployment.replicas: 2
      replicaCount: 2
      # Migrated from: spec.tracing.sampling: 10000 (= 100%)
      traceSampling: 100
      # Migrated from: spec.runtime.components.pilot.pod.nodeSelector
      # (also redundant given global.defaultNodeSelector, but explicit is clearer)
      nodeSelector:
        node-role.kubernetes.io/infra: ''
      tolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
```

> **Warning: Do not add new namespaces to the mesh during migration.** In multitenant mode, a control plane only interacts with namespaces that are part of its mesh. When you install the 3.0 control plane in the same namespace as the 2.6 control plane, leader election determines which one manages the `istio-ca-root-cert` ConfigMap. If the 2.6 control plane becomes the leader, it does not distribute this ConfigMap to new namespaces managed by the 3.0 control plane. As a result, sidecar injection fails because the required root certificate is missing.

### 7.2 Apply the Istio Resource

```bash
oc apply -f ossm3-istio.yaml
```

### 7.3 Verify Shared Root Certificate (Critical Step)

Wait approximately 60 seconds for the new istiod to start, then verify it loaded the existing root certificate:

```bash
oc logs deployments/istiod-basic-v1-24-3 -n istio-system \\
  | grep 'Load signing key and cert from existing secret'
```

Expected output:
```
info pkica Load signing key and cert from existing secret istio-system/istio-ca-secret
```

> **Do not proceed if this message does not appear.** If the new istiod generated its own certificate instead of loading the existing one, mTLS communication between OSSM 2 and OSSM 3 proxies will fail during migration.

### 7.4 Capture the Active Revision Name

```bash
ACTIVE_REVISION=$(oc get istios basic -n istio-system \\
  -o jsonpath='{.status.activeRevision}')
echo \"Active Revision: $ACTIVE_REVISION\"
# Example output: basic-v1-24-3
```

Store this value — it is used in every subsequent phase.

### 7.5 Apply mTLS Strict Mode

In OSSM 2, `spec.security.dataPlane.mtls: true` in the SMCP managed both sides — server-side enforcement (PeerAuthentication) and client-side traffic policy (DestinationRule). In OSSM 3, you need both resources explicitly.

Create a `PeerAuthentication` resource to enforce strict mTLS on the server side (the proxy only accepts mTLS connections), and a `DestinationRule` to tell client proxies to send mTLS traffic to all mesh services:

```yaml
# Server-side: only accept mTLS connections
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: istio-system
spec:
  mtls:
    mode: STRICT
---
# Client-side: send mTLS to all mesh services
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: enable-mtls
  namespace: istio-system
spec:
  host: "*.local"
  trafficPolicy:
    tls:
      mode: ISTIO_MUTUAL
```

```bash
oc apply -f mtls-strict.yaml
```

> **Why `*.local`:** All Kubernetes service DNS names within the cluster end in `.svc.cluster.local`. The `*.local` wildcard covers all inter-service communication in the mesh. The `ISTIO_MUTUAL` mode tells the client proxy to use mTLS with the certificate automatically provisioned by Istio, without requiring custom cert paths.

---

## 8. Phase 4 — Migrate the Gateway to OSSM 3

This phase migrates the existing gateway (now a standalone Deployment using gateway injection in `istio-system`) from the OSSM 2 control plane to the OSSM 3 control plane. The migration happens **in the same namespace** (`istio-system`) so the existing `Service`, OpenShift `Route`, and Istio `Gateway` resources continue to work without modification. The F5 → Router → Gateway traffic flow is **fully preserved throughout**.

> **Important:** Do NOT create a new gateway in a different namespace during migration. The Red Hat documented approach requires the canary gateway to be in the same namespace as the existing gateway, sharing the same Service selector. Only after the migration is complete (Phase 7) would you optionally relocate the gateway to a dedicated namespace.

### 8.1 Migration Options

Per the Red Hat documentation (Chapter 5 — Migrating Gateways):

| Method | When to Use | Zero Downtime? |
|--------|-------------|----------------|
| **Canary** (Section 5.1.1) | Gradual rollout with full control; new and old gateways run side by side | Yes |
| **In-Place** (Section 5.1.2) | Simple restart; less control, faster | Brief blip from pod restart |

This guide documents **both** options. Choose the one that fits your operational requirements.

### 8.2 Prerequisites

- OSSM 3 control plane (Istio resource) deployed and healthy (Phase 3 complete)
- `$ACTIVE_REVISION` captured from Phase 3 — will be used to label the namespace
- Gateway is already using **gateway injection** (completed in Section 4.5)

```bash
ACTIVE_REVISION=$(oc get istios basic -n istio-system \
  -o jsonpath='{.status.activeRevision}')
echo "Active Revision: $ACTIVE_REVISION"
```

### 8.3 Label the Gateway Namespace

The namespace containing the gateway Deployment (`istio-system`) must be labelled to:
- Enable sidecar injection from the **OSSM 3** control plane (`istio.io/rev`)
- Prevent the **OSSM 2** injection webhook from interfering (`maistra.io/ignore-namespace`)

```bash
oc label namespace istio-system \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  --overwrite=true
```

Remove the old OSSM 2 injection label if present:
```bash
oc label namespace istio-system istio-injection- 2>/dev/null || true
```

> **What these labels do:**
> - `istio.io/rev=${ACTIVE_REVISION}` — any new pod created in `istio-system` gets its sidecar proxy from the OSSM 3 control plane
> - `maistra.io/ignore-namespace="true"` — the OSSM 2 injection webhook skips this namespace entirely. Without this, both webhooks would inject, and the pod would fail to start with conflicting CNI annotations
>
> **Regarding multitenant vs cluster-wide:** If using multitenant mode with `discoverySelectors`, ensure the `istio-system` namespace also carries the label your `discoverySelectors` match against (e.g., `service-mesh=enabled` from Section 4.8). In cluster-wide mode, no additional label is needed.

---

### 8.4 Option A: Canary Migration (Recommended for Zero Downtime)

Deploy a new gateway canary Deployment **in the same namespace** (`istio-system`) with labels that match the existing Service's selector. Both deployments receive traffic from the same Service; you control the ratio by adjusting replica counts.

```
OSSM 2 时期                          OSSM 2→3 迁移中期
┌──────────────────┐                 ┌──────────────────┐
│ istio-system      │                 │ istio-system      │
│                    │                 │                    │
│ Service (selector: │                 │ Service (selector: │
│  istio=ingressgateway)              │  istio=ingressgateway) ← 没变
│         ▲         │                 │         ▲         │
│         │         │                 │    ┌────┴────┐    │
│ ┌───────┴────┐    │                 │ ┌──┴───┐  ┌──┴───┐│
│ │旧 Deployment│    │                 │ │旧    │  │新    ││
│ │istio.io/rev │    │                 │ │OSSM2 │  │OSSM3 ││
│ │= basic(2.6) │    │                 │ │replica│  │canary││
│ └────────────┘    │                 │ │减量  │  │增量  ││
└──────────────────┘                 │ └──────┘  └──────┘│
                                      └──────────────────┘
```

#### 8.4.1 Create the Canary Gateway Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: istio-ingressgateway-canary
  namespace: istio-system                # Same namespace as existing gateway
spec:
  selector:
    matchLabels:
      istio: ingressgateway              # Must match existing Service selector
  template:
    metadata:
      annotations:
        inject.istio.io/templates: gateway  # Enables gateway injection
      labels:
        istio: ingressgateway              # Matches Service selector
        istio.io/rev: ${ACTIVE_REVISION}   # Points to OSSM 3 control plane
    spec:
      serviceAccountName: istio-ingressgateway  # If RBAC was created in 4.5
      # Infra node placement — matches existing gateway
      nodeSelector:
        node-role.kubernetes.io/infra: ''
      tolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
      containers:
        - name: istio-proxy
          image: auto
```

> **Key points:**
> - The canary deployment is in the **same namespace** (`istio-system`) as the existing gateway
> - `spec.selector.matchLabels` must include all labels that the existing **Service** selector uses (typically `istio: ingressgateway`). This ensures the existing Service routes traffic to both deployments
> - `inject.istio.io/templates: gateway` tells the injector to use the gateway template (not the default sidecar template)
> - `istio.io/rev` must match the label applied to the namespace in Step 8.3
> - Include `serviceAccountName`, `nodeSelector`, and `tolerations` if your gateway requires them

Apply with the correct revision:
```bash
sed "s/\${ACTIVE_REVISION}/${ACTIVE_REVISION}/g" canary-gateway.yaml | oc apply -f -
# OR inline:
oc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: istio-ingressgateway-canary
  namespace: istio-system
spec:
  selector:
    matchLabels:
      istio: ingressgateway
  template:
    metadata:
      annotations:
        inject.istio.io/templates: gateway
      labels:
        istio: ingressgateway
        istio.io/rev: ${ACTIVE_REVISION}
    spec:
      serviceAccountName: istio-ingressgateway
      nodeSelector:
        node-role.kubernetes.io/infra: ''
      tolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
      containers:
        - name: istio-proxy
          image: auto
EOF
```

#### 8.4.2 Verify Canary Gateway Pods

```bash
# Check all gateway pods (both old and new)
oc get pods -n istio-system -l istio=ingressgateway

# Verify revision mapping
istioctl ps -n istio-system
# Look for the canary pod(s) connected to the OSSM 3 istiod
```

Expected output:
```
NAME                                               ...  ISTIOD                                  VERSION
istio-ingressgateway-canary-xxxxx.istio-system      ...  istiod-basic-v1-24-3-xxxxx              1.24.3
istio-ingressgateway-yyyyy.istio-system             ...  istiod-basic-xxxxx                      1.20.8
```

The canary pod should show the OSSM 3 istiod and version. The old pod should still show the OSSM 2 istiod.

Test a sample route:
```bash
curl -sk -o /dev/null -w "%{http_code}\n" https://hello-world1.devops.local/
# Should return 200
```

> **Note:** At this point, both old and new gateways are handling traffic through the same Service. You have not shifted any traffic yet; the Service distributes requests across all matching pods.

#### 8.4.3 Gradually Shift Traffic

Incrementally transfer traffic by scaling replica counts:

```bash
# Step 1: Scale canary to 1 replica (or your starting size)
oc scale -n istio-system deployment/istio-ingressgateway-canary --replicas 1

# Step 2: Reduce old gateway by 1
oc scale -n istio-system deployment/istio-ingressgateway --replicas 2

# Step 3: Verify traffic — test all 8 endpoints
for i in 1 2 3 4 5 6 7 8; do
  echo -n "hello-world${i}.devops.local: "
  curl -sk -o /dev/null -w "%{http_code}\n" https://hello-world${i}.devops.local/
done

# Step 4: Continue shifting — scale canary up, old down
oc scale -n istio-system deployment/istio-ingressgateway-canary --replicas 3
oc scale -n istio-system deployment/istio-ingressgateway --replicas 0
```

Repeat until the old deployment is at 0 replicas and the canary handles all traffic.

#### 8.4.4 Clean Up Old Deployment

Once the canary handles all traffic successfully, delete the old gateway Deployment:

```bash
oc delete deployment istio-ingressgateway -n istio-system --ignore-not-found
```

Rename the canary to the standard name by applying a final manifest:
```bash
oc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: istio-ingressgateway
  namespace: istio-system
spec:
  replicas: 3
  selector:
    matchLabels:
      istio: ingressgateway
  template:
    metadata:
      annotations:
        inject.istio.io/templates: gateway
      labels:
        istio: ingressgateway
        istio.io/rev: ${ACTIVE_REVISION}
    spec:
      serviceAccountName: istio-ingressgateway
      nodeSelector:
        node-role.kubernetes.io/infra: ''
      tolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
      containers:
        - name: istio-proxy
          image: auto
EOF
```

Then delete the canary:
```bash
oc delete deployment istio-ingressgateway-canary -n istio-system --ignore-not-found
```

---

### 8.5 Option B: In-Place Migration (Faster, Simpler)

If you do not need fine-grained traffic control, restart the existing gateway deployment. The sidecar injector will re-inject it with the OSSM 3 proxy (because the namespace is now labelled with `istio.io/rev=${ACTIVE_REVISION}`).

> **Trade-off:** During the restart, the gateway pod is briefly unavailable. Existing connections are terminated, and new requests may see connection errors for a few seconds until the new pod is Ready.

#### 8.5.1 Label the Namespace (same as Option A)

```bash
oc label namespace istio-system \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  --overwrite=true

oc label namespace istio-system istio-injection- 2>/dev/null || true
```

#### 8.5.2 Restart the Gateway Deployment

```bash
oc rollout restart deployment istio-ingressgateway -n istio-system
oc rollout status deployment istio-ingressgateway -n istio-system
```

#### 8.5.3 Verify

```bash
istioctl ps -n istio-system
# The gateway pod should now show the OSSM 3 istiod and version

for i in 1 2 3 4 5 6 7 8; do
  echo -n "hello-world${i}.devops.local: "
  curl -sk -o /dev/null -w "%{http_code}\n" https://hello-world${i}.devops.local/
done
```

---

### 8.6 Post-Migration State

After either Option A or B, the gateway is:

| Aspect | Before (OSSM 2) | After (Phase 4) | Changes Required? |
|--------|-----------------|------------------|-------------------|
| Namespace | `istio-system` | `istio-system` (same) | None |
| Deployment | SMCP-managed or standalone injection | Standalone injection | Handled by this phase |
| Service (`istio-ingressgateway`) | In `istio-system` | In `istio-system` (same) | None — labels unchanged |
| Service selector | `istio: ingressgateway` | `istio: ingressgateway` (unchanged) | None |
| OpenShift Routes | In `istio-system` | In `istio-system` (same) | None — not modified |
| TLS Secrets | In `istio-system` | In `istio-system` (same) | None |
| Gateway resources (Istio CRs) | In project namespaces | In project namespaces (same) | Only if cross-namespace refs needed |
| Control plane connected to | OSSM 2 istiod | OSSM 3 istiod | Done ✓ |

**No Route changes, no Service changes, no Secret copying, no namespace creation is needed at this stage.** The F5 → Router → Gateway flow is uninterrupted.

> **Optional: Move to `istio-ingress` later.** After the entire migration is complete (Phase 7), you may optionally move the gateway to a dedicated `istio-ingress` namespace for better separation. See **Section 10.4 (Optional)** for that procedure.

### 8.7 Egress Gateway (Optional)

Your SMCP has an egress gateway enabled with 2 replicas on infra nodes. If your workloads use the egress gateway for outbound traffic, you need to migrate it to OSSM 3 as well. The same canary/in-place approach applies.

The SMCP egress gateway configuration:
- 2 replicas on infra nodes
- Resource requests: 10m CPU / 128Mi RAM
- Label: `istio: egressgateway`

**If the egress gateway is still SMCP-managed** (not yet migrated to gateway injection), first apply the same gateway injection migration from Section 4.5 to the egress gateway:

```bash
# Create a standalone egress gateway deployment using gateway injection
oc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: istio-egressgateway
  namespace: istio-system
spec:
  replicas: 2
  selector:
    matchLabels:
      istio: egressgateway
  template:
    metadata:
      annotations:
        inject.istio.io/templates: gateway
      labels:
        istio: egressgateway
        sidecar.istio.io/inject: "true"
    spec:
      nodeSelector:
        node-role.kubernetes.io/infra: ''
      tolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
      containers:
        - name: istio-proxy
          image: auto
EOF
```

Then apply the same Phase 4 canary/in-place migration: since `istio-system` namespace is already labelled with `istio.io/rev=${ACTIVE_REVISION}` and `maistra.io/ignore-namespace="true"` from Section 8.3, simply restart the egress gateway:

```bash
oc rollout restart deployment istio-egressgateway -n istio-system
oc rollout status deployment istio-egressgateway -n istio-system

# Verify
istioctl ps -n istio-system | grep egress
```

---

## 9. Phase 5 — Migrate Workload Namespaces

This phase migrates each workload namespace from the OSSM 2 control plane to the OSSM 3 control plane one at a time. Both control planes share the same root certificate, so mTLS communication between workloads on different control planes continues to work throughout the migration.

> **Note on gateway migration order:** Gateway migration (Phase 4) and workload migration (Phase 5) can happen **independently and in any order**, per the Red Hat documentation. You can migrate the gateway first (the canary runs alongside the old gateway while workloads are still on OSSM 2), or migrate workloads first (the old OSSM 2 gateway still serves traffic to OSSM 3 workloads). Both control planes share the same root certificate, so mTLS between proxies on different control planes works correctly throughout.
>
> This guide assumes you complete Phase 4 (gateway) before Phase 5 (workloads) because the gateway canary migration is simple and non-disruptive. However, if your environment requires workloads to be migrated first (e.g., for testing), that order is also fully supported.

> **Important:** Migrate one namespace at a time in production. Verify connectivity after each namespace before proceeding to the next.

### 9.1 Confirm the Active Revision

```bash
ACTIVE_REVISION=$(oc get istios basic -n istio-system \\
  -o jsonpath='{.status.activeRevision}')
echo \"Active Revision: $ACTIVE_REVISION\"
```

### 9.2 Migrate `project-01` (hello-world1, hello-world2)

**Step 9.2.1:** Apply injection labels to switch to OSSM 3 and disable OSSM 2 injection:
```bash
oc label ns project-01 \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  istio-injection- \
  --overwrite=true
```

The `istio.io/rev` label directs new pods to the OSSM 3 proxy. The `maistra.io/ignore-namespace="true"` label prevents the OSSM 2 webhook from injecting proxies in this namespace. Without this label, both webhooks would attempt injection and the pod would fail to start [1].

**Step 9.2.2:** Restart workloads:
```bash
oc rollout restart deployments -n project-01
oc rollout status deployment hello-world1 -n project-01
oc rollout status deployment hello-world2 -n project-01
```

**Step 9.2.3:** Verify workloads are connected to OSSM 3:
```bash
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION} | grep project-01
# hello-world1 and hello-world2 pods should appear, connected to istiod-basic-v1-24-3-*
```

**Step 9.2.4:** Test application connectivity:
```bash
curl -sk https://hello-world1.devops.local/ | head -5
curl -sk https://hello-world2.devops.local/ | head -5
```

### 9.3 Migrate `project-02` (hello-world3, hello-world4)

```bash
oc label ns project-02 \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  istio-injection- \
  --overwrite=true

oc rollout restart deployments -n project-02
oc rollout status deployment hello-world3 -n project-02
oc rollout status deployment hello-world4 -n project-02

# Verify
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION} | grep project-02
curl -sk https://hello-world3.devops.local/ | head -5
curl -sk https://hello-world4.devops.local/ | head -5
```

### 9.4 Migrate `project-03` (hello-world5, hello-world6)

```bash
oc label ns project-03 \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  istio-injection- \
  --overwrite=true

oc rollout restart deployments -n project-03
oc rollout status deployment hello-world5 -n project-03
oc rollout status deployment hello-world6 -n project-03

# Verify
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION} | grep project-03
curl -sk https://hello-world5.devops.local/ | head -5
curl -sk https://hello-world6.devops.local/ | head -5
```

### 9.5 Migrate `project-04` (hello-world7, hello-world8)

```bash
oc label ns project-04 \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  istio-injection- \
  --overwrite=true

oc rollout restart deployments -n project-04
oc rollout status deployment hello-world7 -n project-04
oc rollout status deployment hello-world8 -n project-04

# Verify
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION} | grep project-04
curl -sk https://hello-world7.devops.local/ | head -5
curl -sk https://hello-world8.devops.local/ | head -5
```

### 9.6 Migrate `devops` Namespace

```bash
oc label ns devops \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  istio-injection- \
  --overwrite=true

oc rollout restart deployments -n devops
```

### 9.7 Verify All Workloads Are on OSSM 3

```bash
# Should return no output (no workloads remaining on OSSM 2)
istioctl ps --istioNamespace istio-system --revision basic

# Should show all workloads from all namespaces
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION}
```

---

## 10. Phase 6 — Update Gateway Resources and Finalise

After Phase 4 (gateway canary/in-place migration) and Phase 5 (workload migration), both the gateway and all workloads are connected to the OSSM 3 control plane. The existing Istio `Gateway` resources remain in their original project namespaces, and the gateway pod (in `istio-system`) is selected by the same `istio: ingressgateway` label. **No changes to Gateway resources or selectors are required.**

This phase covers:
- Verifying the existing Gateway resources work correctly with OSSM 3
- End-to-end traffic testing
- (Optional) Moving Gateway resources to a dedicated `istio-ingress` namespace post-migration

### 10.1 Verify Gateway Configuration

The existing Istio `Gateway` resources (in `project-01` through `project-04`) use `selector: istio: ingressgateway`. The gateway pod in `istio-system` carries this exact label. In OSSM 3, a Gateway `selector` matches pods globally across the cluster — there is **no cross-namespace restriction**. Therefore, the Gateway resources work without any modification.

However, if your Istio `VirtualService` resources reference gateways using only the short name (e.g., `gateways: [project-01-https-gateway]`), this assumes the Gateway is in the same namespace as the VirtualService. Since both are still in the same project namespace (`project-01`), this continues to work unchanged.

Verify the gateway is processing the configuration correctly:

```bash
GW_POD=$(oc get pod -n istio-system -l istio=ingressgateway \
  -o jsonpath='{.items[0].metadata.name}')

# Check listeners (should show port 443 for each host)
istioctl proxy-config listener -n istio-system $GW_POD | grep -E "443|8443"

# Verify TLS certificates are loaded via SDS
istioctl proxy-config secret -n istio-system $GW_POD
# Should list hello-world1 through hello-world8 Secrets
```

### 10.2 End-to-End Traffic Test

```bash
for i in 1 2 3 4 5 6 7 8; do
  echo -n "Testing hello-world${i}.devops.local: "
  curl -sk -o /dev/null -w "%{http_code}\n" https://hello-world${i}.devops.local/
done
# All endpoints should return HTTP 200
```

### 10.3 Verify mTLS Between Workloads and Gateway

```bash
istioctl analyze --istioNamespace istio-system
# Review and resolve any warnings

# Confirm all proxies are synced
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION}
# All pods (workloads + gateway) should show SYNCED for CDS, LDS, EDS, RDS
```

### 10.4 (Optional) Move Gateway to Dedicated Namespace

After the entire OSSM 3 migration is complete (Phase 7 done), you may optionally move the gateway to a dedicated `istio-ingress` namespace for cleaner separation. This is **not required** — the gateway works fine in `istio-system`. Follow these steps only if you prefer the dedicated namespace approach:

#### 10.4.1 Create the `istio-ingress` Namespace

```bash
oc new-project istio-ingress
oc label namespace istio-ingress \
  istio.io/rev=${ACTIVE_REVISION} \
  maistra.io/ignore-namespace="true" \
  service-mesh=enabled \
  --overwrite
```

#### 10.4.2 Copy TLS Secrets

In OSSM 3, the gateway pod reads TLS Secrets from its own namespace via SDS:

```bash
for secret in hello-world1 hello-world2 hello-world3 hello-world4 \
              hello-world5 hello-world6 hello-world7 hello-world8; do
  oc get secret $secret -n istio-system -o json \
    | jq 'del(.metadata.namespace, .metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.managedFields)' \
    | oc apply -n istio-ingress -f -
done
```

#### 10.4.3 Create Gateway RBAC in `istio-ingress`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: istio-ingressgateway
  namespace: istio-ingress
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: istio-ingressgateway-sds
  namespace: istio-ingress
rules:
- apiGroups: [""]
  resources: ["secrets"]
  verbs: ["get", "watch", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: istio-ingressgateway-sds
  namespace: istio-ingress
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: istio-ingressgateway-sds
subjects:
- kind: ServiceAccount
  name: istio-ingressgateway
  namespace: istio-ingress
```

```bash
oc apply -f gateway-rbac-istio-ingress.yaml
```

#### 10.4.4 Create Gateway Deployment in `istio-ingress`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: istio-ingressgateway
  namespace: istio-ingress
spec:
  replicas: 3
  selector:
    matchLabels:
      istio: ingressgateway
  template:
    metadata:
      annotations:
        inject.istio.io/templates: gateway
      labels:
        istio: ingressgateway
        istio.io/rev: ${ACTIVE_REVISION}
    spec:
      serviceAccountName: istio-ingressgateway
      nodeSelector:
        node-role.kubernetes.io/infra: ''
      tolerations:
        - effect: NoExecute
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
        - effect: NoSchedule
          key: node-role.kubernetes.io/infra
          operator: Equal
          value: reserved
      containers:
        - name: istio-proxy
          image: auto
---
apiVersion: v1
kind: Service
metadata:
  name: istio-ingressgateway
  namespace: istio-ingress
  labels:
    istio: ingressgateway
spec:
  type: ClusterIP
  selector:
    istio: ingressgateway
  ports:
    - name: https
      port: 443
      targetPort: 8443
      protocol: TCP
    - name: http2
      port: 80
      targetPort: 8080
      protocol: TCP
```

```bash
oc apply -f gateway-deployment-istio-ingress.yaml
```

#### 10.4.5 Move OpenShift Routes

> **Note:** IOR was already disabled in Phase 1. The existing Routes are in `istio-system`. Delete them there and create new ones in `istio-ingress`. Hostnames will be briefly unreachable during this switchover (1–3 seconds for HAProxy to reload).

```bash
# Delete old Routes in istio-system
oc delete route -n istio-system \
  hello-world1 hello-world2 \
  hello-world3 hello-world4 \
  hello-world5 hello-world6 \
  hello-world7 hello-world8

# Create new Routes in istio-ingress
oc apply -f - <<EOF
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: hello-world1
  namespace: istio-ingress
spec:
  host: hello-world1.devops.local
  port:
    targetPort: https
  tls:
    termination: passthrough
  to:
    kind: Service
    name: istio-ingressgateway
    weight: 100
  wildcardPolicy: None
EOF
# ... repeat for hello-world2-8
```

#### 10.4.6 Scale Down Old Gateway and Verify

```bash
# Scale old gateway in istio-system to 0
oc scale -n istio-system deployment/istio-ingressgateway --replicas 0

# Verify routes
oc get routes -n istio-ingress
# All should show ADMITTED=True

# Test all endpoints
for i in 1 2 3 4 5 6 7 8; do
  echo -n "hello-world${i}.devops.local: "
  curl -sk -o /dev/null -w "%{http_code}\n" https://hello-world${i}.devops.local/
done
```

#### 10.4.7 Clean Up

Optionally delete the old gateway resources in `istio-system`:

```bash
oc delete deployment istio-ingressgateway -n istio-system --ignore-not-found
# Leave the Service in istio-system — it does no harm and other components may reference it
```

---

## 11. Phase 7 — Complete the Migration and Clean Up

### 11.1 Final Verification Before Removing OSSM 2

```bash
# Confirm no workloads remain on OSSM 2
istioctl ps --istioNamespace istio-system --revision basic
# Expected: no output

# Confirm all workloads are on OSSM 3
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION}
# Expected: all hello-world pods from all namespaces + gateway pods
```

### 11.2 Remove the OSSM 2 Control Plane

```bash
# Find all OSSM 2 resources
oc get smcp,smm,smmr -A

# Delete the ServiceMeshControlPlane
oc delete smcp basic -n istio-system

# Delete the ServiceMeshMemberRoll
oc delete smmr default -n istio-system

# Delete any ServiceMeshMember resources
oc delete smm --all -A

# Verify all OSSM 2 resources are removed
oc get smcp,smm,smmr -A
# Expected: No resources found
```

### 11.3 Remove the OSSM 2 Operator and Maistra CRDs

```bash
# Find the OSSM 2 Operator subscription and CSV
csv=$(oc get subscription servicemeshoperator -n openshift-operators \\
  -o yaml | grep currentCSV | awk '{print $2}')
echo \"Deleting CSV: $csv\"

# Delete the subscription
oc delete subscription servicemeshoperator -n openshift-operators

# Delete the ClusterServiceVersion
oc delete clusterserviceversion $csv -n openshift-operators

# Remove all Maistra CRDs
oc get crds -o name | grep \".*\\.maistra\\.io\" | xargs -r -n 1 oc delete
```

### 11.4 Remove Maistra Labels from Namespaces

```bash
# Find namespaces with Maistra labels
oc get namespace -l maistra.io/ignore-namespace=\"true\"

# Remove migration labels from all namespaces
for ns in istio-system istio-ingress devops project-01 project-02 project-03 project-04; do
  oc label namespace $ns maistra.io/ignore-namespace- 2>/dev/null || true
  oc label namespace $ns maistra.io/member-of- 2>/dev/null || true
  oc label namespace $ns kiali.io/member-of- 2>/dev/null || true
done
```

### 11.5 Recreate Network Policies for OSSM 3

Now that OSSM 2 is removed, create network policies scoped to the OSSM 3 control plane and the new `istio-ingress` gateway namespace.

**Istiod network policy in `istio-system` (for OSSM 3):**
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: istiod-ossm3
  namespace: istio-system
spec:
  ingress:
  - {}
  podSelector:
    matchLabels:
      app: istiod
      istio.io/rev: basic-v1-24-3   # Replace with your $ACTIVE_REVISION
  policyTypes:
  - Ingress
```

**Allow ingress from HAProxy Router to gateway pods in `istio-ingress`:**
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-router-ingress
  namespace: istio-ingress
spec:
  podSelector:
    matchLabels:
      istio: ingressgateway
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          network.openshift.io/policy-group: ingress
  policyTypes:
  - Ingress
```

**Allow mesh traffic in `istio-ingress`:**
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: istio-mesh
  namespace: istio-ingress
spec:
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          service-mesh: enabled
  podSelector: {}
  policyTypes:
  - Ingress
```

Apply the same `istio-mesh` NetworkPolicy in each application namespace (`project-01` through `project-04`).

---

## 12. Post-Migration Validation

### 12.1 Control Plane Health

```bash
oc get istios -n istio-system
oc get istiocni
oc get pods -n istio-system | grep istiod
oc get pods -n istio-ingress
```

### 12.2 Proxy Synchronisation Status

```bash
istioctl ps --istioNamespace istio-system --revision ${ACTIVE_REVISION}
# All pods should show SYNCED for CDS, LDS, EDS, RDS
```

### 12.3 Configuration Analysis

```bash
istioctl analyze --istioNamespace istio-system
# Review and resolve any warnings or errors
```

### 12.4 Gateway Certificate Verification

```bash
GW_POD=$(oc get pod -n istio-ingress -l istio=ingressgateway \\
  -o jsonpath='{.items[0].metadata.name}')

# Verify all 8 TLS Secrets are loaded
istioctl proxy-config secret -n istio-ingress $GW_POD

# Verify listeners on port 443
istioctl proxy-config listener -n istio-ingress $GW_POD --port 443
```

### 12.5 End-to-End Application Tests

```bash
for i in 1 2 3 4 5 6 7 8; do
  echo -n \"hello-world${i}.devops.local: \"
  curl -sk -o /dev/null -w \"%{http_code}\\n\" https://hello-world${i}.devops.local/
done
# All should return 200
```

### 12.6 mTLS Verification

```bash
# Check PeerAuthentication is enforcing STRICT mTLS
oc get peerauthentication -A

# Check mTLS status for a specific workload
istioctl x check-inject -n project-01
```

### 12.7 Kiali Mesh Topology

After installing the standalone Kiali Operator and creating a Kiali CR, verify that:
- All 5 application namespaces appear in the mesh topology.
- The `istio-system` gateway namespace (or `istio-ingress` if moved post-migration) appears as an ingress node.
- No namespace shows \"missing sidecar\" warnings.

---

## 13. Configuration Mapping Reference

### 13.1 SMCP to Istio Resource Field Mapping

The following table maps the key settings from your SMCP `basic` to the equivalent OSSM 3 `Istio` resource fields.

| OSSM 2 SMCP Field | Current Value | OSSM 3 Istio Field |
|---|---|---|
| `spec.version` | `v2.6` | `spec.version: v1.24.3` |
| `spec.security.controlPlane.mtls` | `true` | `spec.values.meshConfig.enableAutoMtls: true` |
| `spec.security.dataPlane.mtls` | `true` | `PeerAuthentication` resource (STRICT mode) |
| `spec.security.controlPlane.tls.cipherSuites` | 8 suites | `spec.values.meshConfig.tlsDefaults.cipherSuites` |
| `spec.security.manageNetworkPolicy` | `true` | Not supported — manage manually |
| `spec.tracing.sampling` | `10000` (100%) | `spec.values.pilot.traceSampling: 100` |
| `spec.tracing.type` | `Jaeger` | Not in Istio resource — install Tempo separately |
| `spec.addons.prometheus.enabled` | `true` | Not in Istio resource — install separately |
| `spec.addons.grafana.enabled` | `true` | Not supported in OSSM 3 |
| `spec.addons.kiali.enabled` | `true` | Not in Istio resource — install Kiali Operator separately |
| `spec.gateways.openshiftRoute.enabled` | `true` | Not supported — create Routes manually |
| `spec.gateways.ingress.runtime.deployment.replicas` | `3` | Gateway Deployment `spec.replicas: 3` |
| `spec.gateways.ingress.runtime.container.resources` | CPU 10m–2 / Mem 128Mi–3Gi | Gateway Deployment container resources |
| `spec.gateways.ingress.runtime.pod.nodeSelector` | `infra` | Gateway Deployment `nodeSelector` |
| `spec.runtime.components.pilot.deployment.replicas` | `2` | `spec.values.pilot.replicaCount: 2` |
| `spec.runtime.components.pilot.pod.nodeSelector` | `infra` | `spec.values.pilot.nodeSelector` |
| `spec.proxy.runtime.container.resources.limits.cpu` | `200m` | `spec.values.global.proxy.resources.limits.cpu` |
| `spec.proxy.runtime.container.resources.limits.memory` | `512Mi` | `spec.values.global.proxy.resources.limits.memory` |
| `spec.proxy.runtime.container.resources.requests.cpu` | `10m` | `spec.values.global.proxy.resources.requests.cpu` |
| `spec.proxy.runtime.container.resources.requests.memory` | `10Mi` | `spec.values.global.proxy.resources.requests.memory` |
| `spec.techPreview.meshConfig.defaultConfig.terminationDrainDuration` | `60s` | `spec.values.meshConfig.terminationDrainDuration: 60s` |
| `spec.general.logging.componentLevels.default` | `warn` | `spec.values.global.logging.level: warn` |
| `spec.proxy.injection.autoInject` | `false` | `spec.values.global.proxy.autoInject: disabled` |

### 13.2 Gateway Configuration Mapping

| OSSM 2 Behaviour | OSSM 3 Equivalent |
|---|---|
| Gateway pod managed by SMCP in `istio-system` | Standalone Deployment, initially in `istio-system` (via canary/in-place); optionally moved to `istio-ingress` post-migration |
| TLS Secrets in `istio-system` | TLS Secrets stay in `istio-system` during migration; copied to `istio-ingress` only if moving post-migration |
| IOR auto-creates Routes in `istio-system` | Routes stay in `istio-system` during migration; manually created in `istio-ingress` only if moving post-migration |
| Gateway selector `istio: ingressgateway` | **Unchanged** — same label on new Deployment |
| `tls.termination: passthrough` at Router | **Unchanged** — same Route configuration |
| F5 → Router → `istio-ingressgateway` flow | **Unchanged** — same traffic path throughout |
| TLS `SIMPLE` mode with `credentialName` | **Unchanged** — same Gateway resource configuration |

### 13.3 Unsupported OSSM 2 Fields

| OSSM 2 Field | Action Required |
|
---|---|
| `spec.addons.grafana` | Grafana is not supported in OSSM 3. Use OpenShift Monitoring or an external Grafana instance. |
| `spec.addons.jaeger` | Install Red Hat OpenShift Distributed Tracing Platform (Tempo) separately. |
| `spec.addons.prometheus` | Install via OpenShift User Workload Monitoring or a standalone Prometheus Operator. |
| `spec.addons.kiali` | Install via Kiali Operator provided by Red Hat (standalone CR). |
| `spec.gateways` (SMCP-defined) | Replaced by standalone gateway Deployment (initially in `istio-system` via Phase 4; optionally relocated to `istio-ingress` post-migration). |
| `spec.gateways.openshiftRoute` | Create OpenShift Routes manually in `istio-ingress`. |
| `spec.security.manageNetworkPolicy` | Create NetworkPolicy resources manually. |
| `spec.policy.type` | Not applicable in OSSM 3. |
| `spec.telemetry.type` | Not applicable in OSSM 3. |
| `spec.proxy.networking.protocol.autoDetect` | Not supported. |
| `spec.security.controlPlane.tls.maxProtocolVersion` | Not supported. |

---

## 14. Rollback Procedure

Rollback is possible for individual namespaces as long as the OSSM 2 `ServiceMeshControlPlane` still exists. Once OSSM 2 is removed in Phase 7, rollback requires reinstalling OSSM 2.

**Step 14.1:** Remove the OSSM 3 injection label and the `maistra.io/ignore-namespace` label from the affected namespace:
```bash
oc label ns project-01 istio.io/rev- maistra.io/ignore-namespace- istio-injection-
```

**Step 14.2:** The OSSM 2 injection webhook resumes control of the namespace. Restart the workloads:
```bash
oc rollout restart deployments -n project-01
```

**Step 14.3:** Verify the workloads reconnect to the OSSM 2 control plane:
```bash
istioctl ps --istioNamespace istio-system --revision basic | grep project-01
```

**Step 14.4:** If rolling back the gateway, relabel the `istio-system` namespace to switch back to OSSM 2 injection and restore IOR if needed:
```bash
# Revert namespace labels to OSSM 2
oc label namespace istio-system istio-injection=enabled --overwrite
oc label namespace istio-system istio.io/rev- maistra.io/ignore-namespace- 2>/dev/null || true

# If IOR was disabled, re-enable it to auto-create Routes
oc patch smcp basic -n istio-system --type merge \
  -p '{"spec":{"gateways":{"openshiftRoute":{"enabled":true}}}}'
```

---

## 15. Telemetry CRD Configuration

In OSSM 3, telemetry is managed through the standalone **Telemetry** CRD instead of the SMCP's built-in tracing addon. After installing your observability replacements (Tempo/OpenTelemetry for tracing, OpenShift User Workload Monitoring for Prometheus), create a Telemetry resource to configure metrics and tracing.

### 15.1 Create the Telemetry Resource

```yaml
apiVersion: telemetry.istio.io/v1
kind: Telemetry
metadata:
  name: mesh-default
  namespace: istio-system
spec:
  metrics:
    - providers:
        - name: prometheus
  tracing:
    - providers:
        - name: otel
      random_sampling_percentage: 100.0
```

> **Note:** Update the `otel` provider name and configuration after installing your OpenTelemetry Collector. The `prometheus` provider works out of the box with OpenShift User Workload Monitoring.

### 15.2 Verify Telemetry Configuration

```bash
oc get telemetry -n istio-system
istioctl analyze --istioNamespace istio-system
# Review and resolve any warnings
```

---

## 16. Gateway Migration Strategy Note

The Red Hat official documentation (Chapter 5 — Migrating Gateways) describes two gateway migration methods for moving from OSSM 2 to OSSM 3:

1. **Canary Migration (Section 5.1.1)** — Deploy a second gateway deployment alongside the existing one in the same namespace, gradually shifting traffic by scaling replicas up/down. The existing Service (with unchanged selector) distributes traffic to both deployments. Best for zero-downtime requirements.

2. **In-Place Migration (Section 5.1.2)** — Label the gateway namespace for OSSM 3 injection, then restart the existing deployment. The sidecar injector re-injects the pod with the OSSM 3 proxy. Simpler but causes a brief connectivity blip during the restart.

### Why the Gateway Must Stay in `istio-system` During Migration

The critical requirement from the Red Hat documentation is that the canary or in-place migration happens **in the same namespace** as the existing gateway. This is because:

- The existing `Service` (typically `istio-ingressgateway`) in `istio-system` has a fixed selector (e.g., `istio: ingressgateway`)
- Both the old and new gateway deployments must carry labels matching this selector so the Service distributes traffic to both
- The existing OpenShift Routes point to this Service — they do not need to change
- TLS Secrets remain in `istio-system` where the gateway pod can access them via SDS

Creating a new gateway in a different namespace (`istio-ingress`) during migration would require:
- A new Service with the same name in a different namespace
- New OpenShift Routes pointing to the new Service
- Copying TLS Secrets
- An outage window for Route switchover

None of these are necessary if you follow the documented approach.

### Canary or In-Place — Both Are Done in `istio-system`

Phase 4 of this guide documents **both** options, performed in `istio-system`. Choose based on your tolerance for disruption:

| Factor | Canary | In-Place |
|--------|--------|----------|
| Zero downtime | ✅ Yes | ❌ Brief blip |
| Complexity | Moderate (2 deployments → scale) | Low (1 restart) |
| Rollback speed | Instant (scale old back up) | Requires re-label namespace + restart |
| Traffic control | Fine-grained replica ratio | All-or-nothing |

### Moving to `istio-ingress` — Only After Migration Complete

If you want a dedicated gateway namespace (as recommended by the Istio project for clean separation), do this **only after**:
- The OSSM 3 migration is complete (Phase 7 done)
- OSSM 2 control plane is removed
- All workloads and the gateway are stable on OSSM 3

This post-migration relocation is documented in **Section 10.4 (Optional)**.

---

## 17. cert-manager Migration (Optional)

> **Apply this section only if your OSSM 2 environment uses `cert-manager` for certificate authority** (instead of the built-in `istiod` CA).

If your SMCP has `spec.security.certificateAuthority.type: cert-manager`, follow these additional steps:

### 17.1 Upgrade cert-manager-istio-csr

After installing the OSSM 3 Istio resource, upgrade the cert-manager-istio-csr Helm release to point to the new control plane:

```bash
helm upgrade cert-manager-istio-csr jetstack/cert-manager-istio-csr \
  --install \
  --reuse-values \
  --namespace istio-system \
  --wait \
  --set "app.istio.revisions={basic,$ACTIVE_REVISION}" \
  --set "app.controller.configmapNamespaceSelector=service-mesh=enabled"
```

> **Note:** Replace `service-mesh=enabled` with the same label selector you use in your `discoverySelectors`.

### 17.2 Verify Certificate Authority

```bash
oc get certificates -A
# Verify certificates are issued by cert-manager-istio-csr
oc logs -l app.kubernetes.io/name=cert-manager-istio-csr -n istio-system
```

### 17.3 Remove cert-manager CA from OSSM 2 SMCP

Once all namespaces are migrated to OSSM 3, remove the cert-manager CA configuration from the SMCP before deleting it:

```bash
oc patch smcp basic -n istio-system --type merge \
  -p '{"spec":{"security":{"certificateAuthority":{"type":"None"}}}}'
```

---

## References

[1]: https://docs.redhat.com/en/documentation/red_hat_openshift_service_mesh/3.0/html/migrating_from_service_mesh_2_to_service_mesh_3 "Red Hat OpenShift Service Mesh 3.0 — Migrating from Service Mesh 2 to Service Mesh 3"
[2]: https://docs.redhat.com/en/documentation/red_hat_openshift_service_mesh/2.6 "Red Hat OpenShift Service Mesh 2.6 Documentation"
[3]: https://istio.io/latest/docs/tasks/traffic-management/ingress/ingress-control/ "Istio Ingress Gateways"
[4]: https://istio.io/latest/docs/setup/additional-setup/gateway/ "Istio Gateway Deployment Topologies"
[5]: https://kiali.io/docs/configuration/kialis.kiali.io/
[6]: https://docs.redhat.com/en/documentation/openshift_container_platform/4.17/html/service_mesh/service-mesh-2-x#ossm-migrating-from-smcp-defined-gateways-to-gateway-injection "Red Hat OpenShift Service Mesh 2.x — Migrate from SMCP-Defined gateways to gateway injection (Section 2.15.2)"
"Kiali Operator Custom Resource Reference"

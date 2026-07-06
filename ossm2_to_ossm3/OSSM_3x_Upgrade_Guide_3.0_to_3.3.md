# OSSM 3.x RevisionBased Upgrade Guide (3.0 → 3.1 → 3.2 → 3.3)

**Date:** 2026-07-02
**Author:** Paul Wong
**Environment:** OpenShift Cluster `lab.test.local`
**Current State:** OSSM 3.0 (Istio 1.24.3), RevisionBased strategy
**Target State:** Stepwise upgrade to 3.3 (Istio 1.28.x)

---

## Upgrade Flow Diagram

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                    OSSM 3.x RevisionBased Upgrade                  │
 │              3.0 (v1.24) → 3.1 → 3.2 → 3.3 (v1.28)              │
 └─────────────────────────────────────────────────────────────────────┘

 ┌─── Phase 0 (One-Time — Already Done via Migration Guide) ───────────┐
 │  ✅ IstioRevisionTag "default" created by migration guide            │
 │  ✅ All NS labels already set to istio.io/rev=default               │
 │  (If you did NOT follow the migration guide, run Phase 0 below)     │
 └─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
 ┌─── Each Upgrade Step (Repeat x3) ─────────────────────────────────┐
 │  ⚠️  RevisionBased: patch version = 新舊並排跑，唔係替換！     │
 │                                                                   │
 │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐        │
 │  │ 1. Patch     │───▶│ 2. Wait CSV  │───▶│ 3. Patch     │        │
 │  │ subscription │    │  Succeeded   │    │ Istio version│        │
 │  │ stable-3.N+1 │    │              │    │ vX.Y.Z       │        │
 │  └──────────────┘    └──────────────┘    └──────┬───────┘        │
 │                                                  │                │
 │                      ┌───────────────────────────┘                │
 │                      ▼                                            │
 │  ┌──────────────────────────────────────────────────────────┐     │
 │  │  ⚡ RevisionBased: 新版 control plane 並排建立            │     │
 │  │     舊版仲跑緊 → workload 連住舊版 → 零流量中斷          │     │
 │  └──────────────────────────┬───────────────────────────────┘     │
 │                             │                                     │
 │                             ▼                                     │
 │                                         ┌──────────────┐          │
 │                                         │ 4. Patch     │          │
 │                                         │ IstioCNI     │          │
 │                                         │ (same version│)         │
 │                                         └──────┬───────┘          │
 │                                                │                  │
 │                                                ▼                  │
 │  ┌──────────────────────────────────────────────────────────┐     │
 │  │  5. Wait for BOTH revisions running                      │     │
 │  │     oc get istiorevision → old=InUse, new=NotInUse      │     │
 │  │     oc get pods → 2 istiod pods                         │     │
 │  └──────────────────────────┬───────────────────────────────┘     │
 │                             │                                     │
 │                             ▼                                     │
 │  ┌──────────────────────────────────────────────────────────┐     │
 │  │  6. Verify tag + restart workloads                  │     │
 │  │     Tag auto-follows (verify with oc get)            │     │
 │  │     Option B: update istio.io/rev on each NS            │     │
 │  └──────────────────────────┬───────────────────────────────┘     │
 │                             │                                     │
 │                             ▼                                     │
 │  ┌──────────────────────────────────────────────────────────┐     │
 │  │  7. Restart workloads + gateways + otel-collector       │     │
 │  │     oc rollout restart deployment -n <ns>               │     │
 │  └──────────────────────────┬───────────────────────────────┘     │
 │                             │                                     │
 │                             ▼                                     │
 │  ┌──────────────────────────────────────────────────────────┐     │
 │  │  8. Verify                                              │     │
 │  │     istioctl ps → all VERSION = new                     │     │
 │  │     istioctl analyze -A → no errors                     │     │
 │  │     oc get istiorevision → old cleaned up               │     │
 │  └──────────────────────────┬───────────────────────────────┘     │
 │                             │                                     │
 │                    ┌────────┴────────┐                            │
 │                    │                 │                            │
 │              ┌─────▼─────┐    ┌──────▼──────┐                   │
 │              │ ✅ Pass    │    │ ❌ Fail     │                   │
 │              │ Next step  │    │ Rollback:   │                   │
 │              └────────────┘    │ switch tag  │                   │
 │                                │ back + revert│                   │
 │                                └─────────────┘                   │
 └───────────────────────────────────────────────────────────────────┘
                            │
                            ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │  ✅ Done: OSSM 3.3 — Istio 1.28.x — All workloads migrated     │
 └───────────────────────────────────────────────────────────────────┘
```

---

## Table of Contents

1. [Overview — RevisionBased 點運作](#1-overview--revisionbased-點運作)
2. [Version Mapping](#2-version-mapping)
3. [Current Environment](#3-current-environment)
4. [Prerequisites](#4-prerequisites)
5. [Phase 0: Set Up IstioRevisionTag (One-Time)](#phase-0-set-up-istiorevisiontag-one-time)
6. [Phase 1: Backup](#phase-1-backup)
7. [Step 1: Upgrade from 3.0 to 3.1](#step-1-upgrade-from-30-to-31)
8. [Step 2: Upgrade from 3.1 to 3.2](#step-2-upgrade-from-31-to-32)
9. [Step 3: Upgrade from 3.2 to 3.3](#step-3-upgrade-from-32-to-33)
10. [Post-Upgrade Validation](#10-post-upgrade-validation)
11. [Rollback Procedure](#11-rollback-procedure)
12. [Known Issues & Caveats](#12-known-issues--caveats)
13. [Reference Commands](#13-reference-commands)
14. [Appendix A: Complete Upgrade Lifecycle — YAML Manifests](#appendix-a-complete-upgrade-lifecycle--yaml-manifests)

---

## 1. Overview — RevisionBased 點運作

### 核心概念：CR ≠ Control Plane

`oc patch istio` **唔係直接改 control plane**。佢只係改一個 YAML 配置檔（Istio CR）。
真正做事嘅係 **SAIL Operator**，佢會 watch 呢個 CR 嘅變化，然後自動 reconcile。

```
你執行 oc patch istio (改 YAML)
        │
        ▼
Istio CR (YAML desired state) 被修改
  spec.version: v1.26.8
        │
        ▼
SAIL Operator watch 到變化，開始 reconcile
        │
        ├── InPlace 策略 ──────▶ 替換現有 control plane (1個)
        │                         舊 istiod 刪除 → 新 istiod 建立
        │                         workload 立即連新版
        │
        └── RevisionBased ─────▶ 建立新 IstioRevision CR
              (你用嘅)              │
                                    ▼
                              建立新 istiod Deployment
                              (同舊版並排跑)
                              舊版完全唔郁
                              workload 仲連住舊版
                              零流量中斷
```

### RevisionBased 升級嘅完整鏈條

```
1. 你 patch Istio CR version
2. Operator 建立新 IstioRevision (例如 basic-v1-26-8)
3. Operator 建立新 istiod Deployment (istiod-basic-v1-26-8-xxx)
4. Operator 自動更新 activeRevisionName → 新版
5. IstioRevisionTag（指向 Istio/basic）auto-follow → status.revision 變新版
6. 新舊兩個 istiod 同時跑，互相獨立
7. 你 restart workload → 新版 sidecar 注入
8. 舊 revision 喺 grace period 後自動刪除
```

> **重要發現：** Operator 會自動更新 `activeRevisionName`，唔使手動 patch tag targetRef。
> Tag 因為 `targetRef: Istio/basic`，會 auto-follow activeRevisionName 嘅變化。
> 你只需要 restart workload 就得。

### RevisionBased vs InPlace

| | InPlace | RevisionBased (你用嘅) |
|---|---|---|
|| Patch version 後 | 舊 control plane 被**替換** | 新 control plane **並排建立**，舊嘅仲跑緊 |
|| Workload | 立即連新版 | 仲連住舊版，要 restart 先切 |
|| 同時跑幾多個 istiod | 1 個 | 2 個 (新舊並存) |
|| 切 workload 方式 | 自動 | tag auto-follow，restart 就得 |
|| Tag targetRef | N/A | 唔使改，auto-follow activeRevisionName |
|| 風險 | 有流量中斷 | 可以驗證新版先切 |
|| 資源需求 | 同時 1 個 istiod | 同時 2 個 istiod (雙倍 CPU/memory) |

### Version Alias (Auto Patch Upgrade)

`spec.version` 支持 alias 格式 `vX.Y-latest`。配合 Operator
`approval strategy = Automatic`，Operator 會自動追最新 patch version：

```yaml
spec:
  version: "v1.26-latest"
  updateStrategy:
    type: RevisionBased
```

> **注意：** 即使用 alias，RevisionBased 仍然需要手動切 workload。
> alias 只係自動追 patch version，唔係自動切 workload。

---

## 2. Version Mapping

|| OSSM Version | Istiod Version | OLM Channel |
|---|---|---|---|
|| 3.0 | 1.24.x | `stable-3.0` |
|| 3.1 | 1.26.x | `stable-3.1` |
|| 3.2 | 1.27.x | `stable-3.2` |
|| 3.3 | 1.28.x | `stable-3.3` |

> **Note:** OSSM 3.1 跳過 Istio 1.25.x，直接上 1.26.x。

---

## 3. Current Environment

| Item | Value |
|---|---|
| Istiod version | 1.24.3 (OSSM 3.0) |
| Operator | servicemeshoperator3.v3.0.0 |
| Update strategy | **RevisionBased** |
| Active revision | `basic-v1-24-3` |
| Control plane NS | `istio-system` |
| IstioCNI | v1.24.3, profile=openshift, Healthy |
| Ingress gateways | `istio-ingressgateway-ossm3` (2 replicas) |
| Observability | `otel-collector` (OTel tracing enabled) |
| Workload namespaces | `mesh-demo-1` through `mesh-demo-5`, `project-01` |
| Namespace labels | `istio.io/rev: basic-v1-24-3`, `service-mesh: enabled` |
| IstioRevisionTag | **Not created yet** (Phase 0 will set this up) |
| updateWorkloads | false (default — manual label switching) |

---

## 4. Prerequisites

- [ ] `cluster-admin` access to the OpenShift cluster
- [ ] `oc` CLI installed and pointed at your cluster
- [ ] `istioctl` installed
- [ ] Review release notes for each target version
- [ ] Schedule a maintenance window
- [ ] **Ensure sufficient resources** — dual istiod pods need extra CPU/memory during upgrade
- [ ] Verify available channels before each step:
  ```bash
  oc get packagemanifest servicemeshoperator3 \
    -n openshift-marketplace \
    -o jsonpath='{.status.channels[*].name}'
  ```

---

## Phase 0: Set Up IstioRevisionTag (One-Time)

### 點解需要 IstioRevisionTag

**冇 tag 嘅升級流程：**
```
升級 3.0 → 3.1：
  1. patch istio version
  2. 改每個 NS 嘅 label: istio.io/rev=basic-v1-24-3 → basic-v1-26-8
     (mesh-demo-1, mesh-demo-2, mesh-demo-3, mesh-demo-4, mesh-demo-5, project-01)
     即係要改 6 次
  3. restart workload
```

**有 tag 嘅升級流程：**
```
升級 3.0 → 3.1：
  1. patch istio version
  2. patch istiorevisiontag default: targetRef → 指向新 revision
     (只需改 1 次，所有 NS 自動跟)
  3. restart workload
```

### Tag 指向邊個

Tag 可以指向兩種對象：

**情況 A：指向 Istio resource（你用嘅）**
```yaml
spec:
  targetRef:
    kind: Istio        # ← 指向 Istio resource
    name: basic
```
- Tag 跟住 Istio resource 嘅 active revision
- 升級時要手動 patch tag（因為 activeRevision 唔會自動切）

**情況 B：指向 IstioRevision resource**
```yaml
spec:
  targetRef:
    kind: IstioRevision  # ← 直接指向特定 revision
    name: basic-v1-26-8
```
- Tag 直接指向某個特定 revision
- 更加明確，但升級時都要手動改

### 升級時 Tag 嘅行為

用你嘅環境做例子 — **Operator 會自動更新 activeRevisionName，tag auto-follow：**

```
升級前：
  Istio/basic:
    spec.version: v1.24.3
    status.activeRevisionName: basic-v1-24-3

  IstioRevisionTag/default:
    targetRef: Istio/basic
    status.revision: basic-v1-24-3  ← 跟住 active revision

  workload labels: istio.io/rev=default
  workload 連住: istiod-basic-v1-24-3
```

```
patch istio version → v1.26.8 之後：
  Istio/basic:
    spec.version: v1.26.8
    status.activeRevisionName: basic-v1-26-8  ← ✅ Operator 自動更新咗！

  IstioRevisionTag/default:
    targetRef: Istio/basic               ← 冇改過
    status.revision: basic-v1-26-8       ← ✅ Tag auto-follow！唔使手動 patch！

  新 IstioRevision basic-v1-26-8 已建立
  新 istiod 已跑緊
  Tag 已指向新版
  但 workload 仲連住舊版 ← 要 restart 先切
```

```
restart workload 之後：
  workload 連住: istiod-basic-v1-26-8  ← 切去新版！
  舊 revision 等 grace period 後自動刪除
```

**所以升級時你要做嘅嘢：**
```
1. patch istio version          → 新 revision 建立, operator 自動切 activeRevision
2. restart workload             → workload 切去新版（tag 已 auto-follow）
```

> **注意：** Operator（OSSM 3.1.10+）會自動更新 `activeRevisionName`，
> tag 因為 `targetRef: Istio/basic` 會 auto-follow。你唔需要手動 patch tag targetRef。
> 如果將來發現 operator 行為有變（例如唔自動更新 activeRevision），可以先 check：
> `oc get istio basic -n istio-system -o jsonpath='{.status.activeRevisionName}'`

### 0.1 Create IstioRevisionTag

> **⚠️ If you followed the migration guide (Version 2.1+), this tag is already created in Phase 3 (Section 7.6). Skip this step.**
>
> Only run this if you set up OSSM 3 without the migration guide, or if you're doing a fresh installation.

```yaml
apiVersion: sailoperator.io/v1
kind: IstioRevisionTag
metadata:
  name: default
spec:
  targetRef:
    kind: Istio
    name: basic
```

```bash
oc apply -f istiorevisiontag-default.yaml
```

### 0.2 Verify Tag is Active

```bash
oc get istiorevisiontag default -n istio-system
```

Expected:
```
NAME      STATUS    IN USE   REVISION          AGE
default   Healthy   True     basic-v1-24-3     10s
```

### 0.3 Switch Workload Namespaces to Use Tag

Update each namespace to reference the tag name instead of the revision name.
**包括 `istio-system`** — ingress gateway 都係睇呢個 label 嚟決定連邊個 revision：

```bash
# Switch all mesh namespaces + istio-system to use the tag
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01 istio-system; do
  oc label namespace $ns istio.io/rev=default --overwrite
done
```

> **重要：** `istio-system` 都一定要轉！Inress gateway deployment 係 operator 管理，
> 但 gateway pod 係根據 `istio-system` namespace 嘅 `istio.io/rev` label 決定
> 連邊個 revision。如果唔轉，gateway 會永遠 bind 死 `basic-v1-24-3`，
> 升級時 tag targetRef 點改佢都唔會跟。
>
> Phase 0 做呢步係 no-op（tag default 指向 basic-v1-24-3），唔影響流量。
> 但唔做呢步，之後每次升級都要手動改 `istio-system` 嘅 label。

### 0.4 Verify

```bash
# Confirm tag is in use
oc get istiorevisiontag default -n istio-system

# Confirm workloads still on correct version
istioctl ps | grep VERSION
```

### 0.5 (Optional) Enable Automatic Workload Migration

If you want the operator to automatically move workloads when you switch
the tag, set `updateWorkloads: true` in the Istio resource:

```bash
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"updateStrategy":{"updateWorkloads":true}}}'
```

> **Warning:** With `updateWorkloads: true`, changing the tag's `targetRef`
> will automatically restart all workloads. This removes manual control
> over canary migration. Keep `false` if you want to validate before switching.

---

## Phase 1: Backup

Perform BEFORE each upgrade step.

```bash
# ---- Backup Istio resource ----
oc get istio basic -n istio-system -o yaml > istio-backup-$(date +%Y%m%d-%H%M%S).yaml

# ---- Backup IstioCNI ----
oc get istiocni default -n istio-cni -o yaml > istiocni-backup-$(date +%Y%m%d-%H%M%S).yaml

# ---- Backup IstioRevisionTag ----
oc get istiorevisiontag -n istio-system -o yaml > revisiontag-backup-$(date +%Y%m%d-%H%M%S).yaml

# ---- Backup all IstioRevision resources ----
oc get istiorevision -n istio-system -o yaml > istiorevisions-backup-$(date +%Y%m%d-%H%M%S).yaml

# ---- Backup gateway + policy resources ----
oc get gateway,virtualservice,destinationrule,peerauthentication,requestauthentication,authorizationpolicy,telemetry -A -o yaml > istio-resources-backup-$(date +%Y%m%d-%H%M%S).yaml

# ---- Backup namespace labels ----
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc get namespace $ns -o yaml > ns-labels-${ns}-$(date +%Y%m%d-%H%M%S).yaml
done

# ---- Record current proxy status ----
istioctl ps > istiod-ps-before-$(date +%Y%m%d-%H%M%S).txt

echo "Backup complete."
```

### 1.0 Set Grace Period (First Upgrade Only)

> **為咗避免 rollout restart 途中舊 revision 被回收，第一次升級前加大 grace period。**
> 預設 30 秒太短，尤其你有 gateway + 6 個 namespace 要 rollout restart。
> 視乎你嘅 workload 數量，**建議至少 1 小時（3600 秒）**，確保有足夠時間 rollout
> 同驗證，唔使趕收工。

```bash
# Set grace period to 1 hour (3600 seconds)
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"updateStrategy":{"inactiveRevisionDeletionGracePeriodSeconds":3600}}}'
```

> 呢個設定係 Istio CR 層面，只影響 `RevisionBased` 策略。
> 每次升級後，舊 revision 會响 grace period 完結先自動清理。
> **搞掂晒全部三次升級後，先還原返 30 秒。**

---

## Step 1: Upgrade from 3.0 to 3.1

### 1.1 Update the Operator Subscription

```bash
# Verify subscription name first
oc get subscription -n openshift-operators | grep -i mesh

# Patch channel (replace <sub-name> with actual name)
oc patch subscription <sub-name> \
  -n openshift-operators \
  --type merge \
  -p '{"spec":{"channel":"stable-3.1"}}'
```

### 1.2 Wait for Operator to Upgrade

```bash
oc get csv -n openshift-operators -w | grep -i service-mesh
```

Wait until CSV shows `Succeeded`. Expected:
```
servicemeshoperator3.1.x   stable-3.1   Succeeded
```

### 1.3 Update Istio Resource Version

```bash
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"version":"v1.26.8"}}'
```

> Or use alias for auto patch upgrades:
> `-p '{"spec":{"version":"v1.26-latest"}}'`

**RevisionBased 行為：** 呢個 command 唔係替換舊版，而係建立一個**全新嘅** control plane
`basic-v1-26-8`，同舊版 `basic-v1-24-3` **並排跑**。你嘅 workload 仲連住舊版，唔會有
任何流量中斷。呢個係 RevisionBased 嘅安全之處 — 你可以驗證新版冇問題先切過去。

### 1.4 Update IstioCNI Version

```bash
oc patch istiocni default -n istio-cni --type merge \
  -p '{"spec":{"version":"v1.26.8"}}'

# Wait for DaemonSet rolling update
oc get pods -n istio-cni -w
```

### 1.5 Verify Both Control Planes Running

```bash
# Check Istio resource — should show 2 revisions
oc get istio basic -n istio-system
```

Expected:
```
NAME      REVISIONS   READY   IN USE   ACTIVE REVISION     STATUS    VERSION   AGE
basic     2           2       1        basic-v1-24-3       Healthy   v1.26.8   5m
```

```bash
# Check both IstioRevision resources
oc get istiorevision -n istio-system
```

Expected:
```
NAME              TYPE    READY   STATUS    IN USE   VERSION   AGE
basic-v1-24-3     Local   True    Healthy   True     v1.24.3   10m
basic-v1-26-8     Local   True    Healthy   False    v1.26.8   60s
```

```bash
# Check both istiod pods running
oc get pods -n istio-system | grep istiod
```

Expected:
```
istiod-basic-v1-24-3-xxxxx    1/1     Running   0   10m
istiod-basic-v1-26-8-xxxxx    1/1     Running   0   60s
```

> **⚠️ 呢個時候 workload 仲連住舊版 v1.24.3，完全冇影響。**
> 新版 v1.26.8 跑緊但冇 workload 連入去。你可以安心驗證。

### 1.6 Verify Tag Auto-Follow

> **唔使 patch tag targetRef！** Operator 會自動更新 `activeRevisionName`，
> tag（指向 `Istio/basic`）會 auto-follow。你只需要確認 tag 已經指向新版：

```bash
# Verify tag already points to new revision (auto-followed)
oc get istiorevisiontag default -n istio-system

# If tag shows basic-v1-26-8 → proceed to restart
# If tag still shows basic-v1-24-3 → run fallback command below
```

Expected:
```
NAME      STATUS    IN USE   REVISION          AGE
default   Healthy   True     basic-v1-26-8     10s
```

**Fallback（如果 tag 冇 auto-follow）：**
> 理論上 operator 會自動更新，但如果出現唔 auto-follow 嘅情況，
> 先用呢個 command 手動 patch：

```bash
oc patch istiorevisiontag default -n istio-system --type merge \
  -p '{"spec":{"targetRef":{"kind":"IstioRevision","name":"basic-v1-26-8"}}}'
```

**Option B — Direct Label Update (if no tag):**

```bash
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc label namespace $ns istio.io/rev=basic-v1-26-8 --overwrite
done
```

### 1.7 Restart Workloads + Ingress Gateway

> **tag 已 auto-follow，restart 就會連去新版。**

> **ingress gateway 點解都要 restart？**
>
> Gateway deployment 係 operator 根據 Istio CR 管理嘅。Gateway pod 嘅 Envoy proxy
> 連邊個 control plane，係由 `istio-system` namespace 嘅 `istio.io/rev=default` label
> resolve 到 tag，而 tag 已經指向新版 revision。Restart 後 gateway 就會連去新版 istiod。

```bash
# Restart all mesh workloads — sidecar 重新注入新版
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  echo "Restarting workloads in $ns..."
  oc rollout restart deployment -n $ns
done

# Restart ingress gateway — 連去新版 istiod
oc rollout restart deployment -n istio-system -l app=istio-ingressgateway

# Restart otel-collector if deployed in mesh
oc rollout restart deployment -n istio-system -l app=otel-collector
```

### 1.8 Verify Data Plane Upgrade

```bash
# All proxies should now show 1.26.x
istioctl ps | grep VERSION
```

Expected: All `VERSION` columns show `1.26.x`.

```bash
# Policy analysis
istioctl analyze -A

# Verify old revision is being cleaned up
oc get istiorevision -n istio-system
```

### 1.9 Final Check

```bash
# Only one revision should remain (old one deleted after grace period)
oc get pods -n istio-system | grep istiod
```

If everything passes, **proceed to Step 2**.

---

## Step 2: Upgrade from 3.1 to 3.2

### 2.1 Update Operator Subscription

```bash
oc patch subscription <sub-name> \
  -n openshift-operators \
  --type merge \
  -p '{"spec":{"channel":"stable-3.2"}}'
```

### 2.2 Wait for CSV

```bash
oc get csv -n openshift-operators -w | grep -i service-mesh
```

### 2.3 Update Istio Version

> **先 check 可用版本：**
> ```bash
> oc get istio basic -n istio-system -o jsonpath='{.spec.version}' && echo ""
> # 然後 check operator 支援嘅版本
> ```

```bash
# OSSM 3.2 → Istio 1.27.x（check available versions first）
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"version":"v1.27.9"}}'
# RevisionBased: 舊版 basic-v1-26-8 仲跑緊，新版 basic-v1-27-9 並排建立
# Workload 仲連住舊版，等你驗證完先切
```

### 2.4 Update IstioCNI

```bash
oc patch istiocni default -n istio-cni --type merge \
  -p '{"spec":{"version":"v1.27.9"}}'
```

### 2.5 Verify Both Control Planes

```bash
oc get istiorevision -n istio-system
# Should show basic-v1-26-8 (InUse) + basic-v1-27-9 (NotInUse)

oc get pods -n istio-system | grep istiod
# Should show 2 istiod pods
```

### 2.6 Verify Tag + Restart

(Operator 會 auto-follow，先 verify tag 已指向新版，唔使 patch)

```bash
# Verify tag auto-followed
oc get istiorevisiontag default -n istio-system

# Fallback（如果冇 auto-follow）：
# oc patch istiorevisiontag default -n istio-system --type merge \
#   -p '{"spec":{"targetRef":{"kind":"IstioRevision","name":"basic-v1-27-9"}}}'

# Option B: Direct labels (if no tag)
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc label namespace $ns istio.io/rev=basic-v1-27-9 --overwrite
done

# Restart all workloads + ingress gateway
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc rollout restart deployment -n $ns
done
oc rollout restart deployment -n istio-system -l app=istio-ingressgateway
oc rollout restart deployment -n istio-system -l app=otel-collector
```

### 2.7 Verify

```bash
istioctl ps | grep VERSION
# All should show 1.27.x

istioctl analyze -A
```

---

## Step 3: Upgrade from 3.2 to 3.3

### 3.1 Update Operator Subscription

```bash
oc patch subscription <sub-name> \
  -n openshift-operators \
  --type merge \
  -p '{"spec":{"channel":"stable-3.3"}}'
```

### 3.2 Wait for CSV

```bash
oc get csv -n openshift-operators -w | grep -i service-mesh
```

### 3.3 Update Istio Version

> **Note:** OSSM 3.2 = Istio 1.27.x. OSSM 3.3 = Istio 1.28.x.

```bash
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"version":"v1.28.3"}}'
# RevisionBased: 舊版 basic-v1-26-8 仲跑緊，新版 basic-v1-28-3 並排建立
# Workload 仲連住舊版，等你驗證完先切
```

### 3.4 Update IstioCNI

```bash
oc patch istiocni default -n istio-cni --type merge \
  -p '{"spec":{"version":"v1.28.3"}}'
```

### 3.5 Verify Both Control Planes

```bash
oc get istiorevision -n istio-system
oc get pods -n istio-system | grep istiod
```

### 3.6 Verify Tag + Restart

(Operator auto-follow，先 verify tag 已指向新版，唔使 patch)

```bash
# Verify tag auto-followed
oc get istiorevisiontag default -n istio-system

# Fallback（如果冇 auto-follow）：
# oc patch istiorevisiontag default -n istio-system --type merge \
#   -p '{"spec":{"targetRef":{"kind":"IstioRevision","name":"basic-v1-28-3"}}}'

# Restart all workloads + ingress gateway
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc rollout restart deployment -n $ns
done
oc rollout restart deployment -n istio-system -l app=istio-ingressgateway
oc rollout restart deployment -n istio-system -l app=otel-collector
```

### 3.7 Verify

```bash
istioctl ps | grep VERSION
# All should show 1.28.x

istioctl analyze -A
```

### 3.8 Restore Grace Period (After All Upgrades Complete)

**搞掂晒三次升級後，還原 grace period 做預設值：**

```bash
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"updateStrategy":{"inactiveRevisionDeletionGracePeriodSeconds":30}}}'
```

> 如果你仲有未做嘅升級步驟，**唔好還住** — 等全部 3.0→3.1→3.2→3.3 完成先還原。

---

## 10. Post-Upgrade Validation

```bash
#!/bin/bash
# ossm3-revisionbased-validation.sh

echo "=========================================="
echo "OSSM 3.x RevisionBased Post-Upgrade Check"
echo "=========================================="
echo ""

# 1. Operator version
echo "[1] Operator Version:"
oc get csv -n openshift-operators \
  -l operators.coreos.com/openshift-service-mesh-operator \
  -o jsonpath='{.items[0].status.version}'
echo ""

# 2. Istio resource
echo "[2] Istio Resource:"
oc get istio basic -n istio-system \
  -o jsonpath='version={.spec.version} strategy={.spec.updateStrategy.type} activeRevision={.status.activeRevisionName}'
echo ""

# 3. IstioCNI version
echo "[3] IstioCNI Version:"
oc get istiocni default -n istio-cni -o jsonpath='{.spec.version}'
echo ""

# 4. IstioRevision status
echo "[4] IstioRevisions:"
oc get istiorevision -n istio-system
echo ""

# 5. IstioRevisionTag
echo "[5] IstioRevisionTag:"
oc get istiorevisiontag -n istio-system
echo ""

# 6. Istiod pods
echo "[6] Istiod Pods:"
oc get pods -n istio-system -l app=istiod -o wide
echo ""

# 7. Gateway pods
echo "[7] Gateway Pods:"
oc get pods -n istio-system -l app=istio-ingressgateway -o wide
echo ""

# 8. Sidecar versions
echo "[8] Sidecar Proxy Versions:"
istioctl version --short
echo ""

# 9. Namespace labels
echo "[9] Namespace istio.io/rev Labels:"
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  rev=$(oc get namespace $ns -o jsonpath='{.metadata.labels.istio\.io/rev}' 2>/dev/null)
  echo "  $ns: $rev"
done
echo ""

# 10. Policy analysis
echo "[10] Policy Analysis:"
istioctl analyze -A 2>&1 | tail -5
echo ""

# 11. Mesh pod count
echo "[11] Mesh Pods:"
istioctl ps | wc -l
echo " pods in mesh"
echo ""

echo "=========================================="
echo "Validation complete."
echo "=========================================="
```

---

## 11. Rollback Procedure

### ⚠️ Rollback 順序：先切 workload，再 revert version

RevisionBased rollback 嘅關鍵係：**先將 workload 切返舊版 control plane，
確認冇問題先 revert version。** 如果先 revert version，舊 revision 可能已經
被 grace period 刪除，workload 就會冇 control plane 連接。

### 11.1 Rollback a Single Step (e.g., 3.1 → 3.0)

```bash
# ===== STEP 1: Switch workloads back to OLD revision FIRST =====
# (呢步最重要！確保 workload 返去舊版 control plane)

# Option A: Switch tag back to old revision
oc patch istiorevisiontag default -n istio-system --type merge \
  -p '{"spec":{"targetRef":{"kind":"IstioRevision","name":"basic-v1-24-3"}}}'

# Option B: Switch namespace labels back
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc label namespace $ns istio.io/rev=basic-v1-24-3 --overwrite
done

# Restart workloads to reconnect to old control plane
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc rollout restart deployment -n $ns
done

# ===== STEP 2: Verify workload is back on old version =====
istioctl ps | grep VERSION
# Should all show 1.24.x

# ===== STEP 3: NOW revert Istio version (old revision still exists) =====
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"version":"v1.24.3"}}'

# ===== STEP 4: Revert IstioCNI =====
oc patch istiocni default -n istio-cni --type merge \
  -p '{"spec":{"version":"v1.24.3"}}'

# ===== STEP 5: Revert operator subscription =====
oc patch subscription <sub-name> \
  -n openshift-operators \
  --type merge \
  -p '{"spec":{"channel":"stable-3.0"}}'

# ===== STEP 6: Wait for operator to roll back =====
oc get csv -n openshift-operators -w | grep -i service-mesh
```

> **⚠️ 如果 grace period 已過期，舊 revision 可能已被刪除。**
> 檢查：`oc get istiorevision -n istio-system`
> 如果舊 revision 唔見咗，要先 revert version 等 operator 重建，然後再切 workload。

### 11.2 Full Restore from Backup (Worst Case)

```bash
oc delete istio basic -n istio-system
oc apply -f istio-backup-YYYYMMDD-HHMMSS.yaml
oc apply -f istiocni-backup-YYYYMMDD-HHMMSS.yaml
oc apply -f istiorevisiontag-default.yaml
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc rollout restart deployment -n $ns
done
```

---

## 12. Known Issues & Caveats

### Between 3.0 → 3.1
- **Jaeger default disabled** in new instances. Plan migration to Tempo + OTel.
- **Sidecar startupProbe** added by default.
- **IOR disabled** by default. Re-enable if using OpenShift Routes.

### Between 3.2 → 3.3
- **Major Istio version jump** — from 1.27.x to 1.28.x.
- Check `EnvoyFilter` compatibility across Istio versions.

### RevisionBased Specific
- **Two istiod pods running during upgrade** — ensure sufficient cluster resources.
  升級期間需要雙倍 istiod 嘅 CPU/memory。如果你嘅 pilot 已經設咗 `autoscaleMin: 2`，
  加上新 revision 嘅 2 個 pod，總共會有 4 個 istiod pod。
- **Grace period:** Old revision deleted after `inactiveRevisionDeletionGracePeriodSeconds`
  (default 30s). 如果你未切 workload 就過期，舊 revision 會被刪除。
  建議升級前設高啲：
  ```bash
  oc patch istio basic -n istio-system --type merge \
    -p '{"spec":{"updateStrategy":{"inactiveRevisionDeletionGracePeriodSeconds":300}}}'
  ```
  升級完成後改返 30。
- **IstioCNI must match** Istio version. Forgetting this causes CNI mismatches.
- **Operator upgrade ≠ mesh upgrade.** Must manually patch `spec.version`.

### General
- **Do NOT skip versions.** Always: 3.0 → 3.1 → 3.2 → 3.3.
- **Data plane restart mandatory.** Old sidecars work but miss new proxy features.
- **Telemetry CR** may need updates between Istio versions.
- **Use `vX.Y-latest` alias** for auto patch upgrades with Automatic approval.

---

## 13. Reference Commands

### Quick Status

```bash
# Operator version
oc get csv -n openshift-operators -l operators.coreos.com/openshift-service-mesh-operator -o wide

# Available channels
oc get packagemanifest servicemeshoperator3 -n openshift-marketplace -o jsonpath='{.status.channels[*].name}'

# Istio resource
oc get istio basic -n istio-system -o jsonpath='{.spec.version}'

# Active revision
oc get istio basic -n istio-system -o jsonpath='{.status.activeRevisionName}'

# All revisions
oc get istiorevision -n istio-system

# RevisionTag
oc get istiorevisiontag -n istio-system

# IstioCNI
oc get istiocni default -n istio-cni -o jsonpath='{.spec.version}'

# All sidecars
istioctl ps

# Mesh health
istioctl analyze -A
```

### Troubleshooting

```bash
# Istiod CrashLoopBackOff:
oc logs -n istio-system -l app=istiod --tail=100

# Sidecars not picking up new config:
oc get pods -n <namespace> -o yaml | grep sidecar.istio.io

# Force sidecar re-injection:
oc annotate deployment <name> -n <ns> sidecar.istio.io/restart="$(date +%s)"

# Check namespace labels:
oc get namespace <ns> -o jsonpath='{.metadata.labels}'

# IstioCNI health:
oc get pods -n istio-cni -o wide
oc logs -n istio-cni -l k8s-app=istio-cni --tail=50
```

---

## Upgrade Timeline Estimate

| Step | Estimated Time | Notes |
|---|---|---|
| Operator upgrade | 5-15 min | OLM catalog sync |
| IstioCNI update | 2-3 min | DaemonSet rolling |
| New control plane ready | 2-5 min | Both revisions running |
| Switch workloads + restart | 5-10 min | Label update + rollout |
| Validation | 5 min | istioctl ps, analyze |
| **Total per step** | **~20-35 min** | |
| **Total (3 steps)** | **~1-2 hours** | Including verification |

---

## References

- [Red Hat OSSM 3.0 Updating Guide](https://docs.redhat.com/en/documentation/red_hat_openshift_service_mesh/3.0/html/updating/index)
- [OSSM 2→3 Migration Guide](file:///home/devops/Documents/ossm2_to_ossm3/)

---

## Appendix A: Complete Upgrade Lifecycle — YAML Manifests

呢個 appendix 用完整 YAML manifest 展示每個階段嘅 cluster 狀態變化，
由 Phase 0 到一次升級完成。

### A.1 Phase 0: Initial Setup

建立 IstioRevisionTag，將 NS label 由 revision 名轉做 tag 名。

**Step 0.1 — Create IstioRevisionTag (指向 Istio CR):**

```bash
oc apply -f - <<'EOF'
apiVersion: sailoperator.io/v1
kind: IstioRevisionTag
metadata:
  name: default
  namespace: istio-system
spec:
  targetRef:
    kind: Istio
    name: basic
EOF
```

**Step 0.2 — Switch NS labels to tag name:**

```bash
# 所有 mesh namespace + istio-system (ingress gateway) 都轉 tag
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01 istio-system; do
  oc label namespace $ns istio.io/rev=default --overwrite
done
```

**Phase 0 完成後 Cluster 狀態:**

```yaml
# Istio CR (冇變過)
apiVersion: sailoperator.io/v1
kind: Istio
metadata:
  name: basic
  namespace: istio-system
spec:
  version: v1.24.3
  namespace: istio-system
  updateStrategy:
    type: RevisionBased
    updateWorkloads: false
status:
  activeRevisionName: basic-v1-24-3

# IstioRevisionTag (新建立)
apiVersion: sailoperator.io/v1
kind: IstioRevisionTag
metadata:
  name: default
  namespace: istio-system
spec:
  targetRef:
    kind: Istio          # ← 指向 Istio CR
    name: basic
status:
  revision: basic-v1-24-3   # ← 透過 Istio CR 嘅 activeRevisionName resolve
  observedGeneration: 1
  conditions:
  - type: Reconciled
    status: "True"
  - type: Healthy
    status: "True"
  - type: InUse
    status: "True"

# IstioRevision (operator 自動管理)
apiVersion: sailoperator.io/v1
kind: IstioRevision
metadata:
  name: basic-v1-24-3
  namespace: istio-system
  ownerReferences:
  - apiVersion: sailoperator.io/v1
    kind: Istio
    name: basic
spec:
  version: v1.24.3
status:
  status: Healthy
  InUse: true

# NS label — 全部用 tag 名
metadata:
  labels:
    istio.io/rev: default
```

**Tag resolve chain:**

```
istio.io/rev=default
  → IstioRevisionTag/default
    → targetRef: Istio/basic
      → status.activeRevisionName: basic-v1-24-3
        → sidecar injector 用 basic-v1-24-3
          → workload 連住舊版 istiod ✅
```

---

### A.2 Step 1.3: Patch Istio Version

```bash
oc patch istio basic -n istio-system --type merge \
  -p '{"spec":{"version":"v1.26.8"}}'
```

**Patch 後 Cluster 狀態 — Operator 自動更新 activeRevisionName 🔄**

```yaml
# Istio CR — spec.version + activeRevisionName 都更新咗
apiVersion: sailoperator.io/v1
kind: Istio
metadata:
  name: basic
  namespace: istio-system
spec:
  version: v1.26.8                    # ← 改咗
status:
  activeRevisionName: basic-v1-26-8   # ← ✅ Operator 自動更新！

# IstioRevisionTag — 冇人掂過，但 auto-follow 咗
apiVersion: sailoperator.io/v1
kind: IstioRevisionTag
metadata:
  name: default
  namespace: istio-system
spec:
  targetRef:
    kind: Istio                        # ← 冇改過，仲係 Istio/basic
    name: basic
status:
  revision: basic-v1-26-8             # ← ✅ Tag 自動跟咗去新版！
  observedGeneration: 1

# IstioRevision — 而家有兩條並排！
items:
- apiVersion: sailoperator.io/v1
  kind: IstioRevision
  metadata:
    name: basic-v1-24-3
  spec:
    version: v1.24.3
  status:
    status: Healthy
    InUse: true                         # ← workload 仲用緊

- apiVersion: sailoperator.io/v1
  kind: IstioRevision
  metadata:
    name: basic-v1-26-8                 # ← 新 revision 自動建立
    ownerReferences:
    - kind: Istio
      name: basic
  spec:
    version: v1.26.8
  status:
    status: Healthy
    InUse: false                        # ← 冇 workload 用，閒置中
```

**Operator 動作:** patch version 後 operator 見到 `spec.version` 由 `v1.24.3` → `v1.26.8`，
建立新 IstioRevision `basic-v1-26-8` 同新 istiod pod，
**同時自動更新 `activeRevisionName` → `basic-v1-26-8`**。
Tag 因為 `targetRef: Istio/basic`，auto-follow 咗去新版。
但 workload 仲未 restart，sidecar 仲係舊版，新版 istiod 閒置等切。

```bash
# istioctl ps 結果 — 冇變
PROXY NAME                              VERSION
mesh-demo-1/frontend-xxx               istio-1.24.3
mesh-demo-2/backend-yyy                istio-1.24.3

# istiod pods — 兩個並排
istiod-basic-v1-24-3-xxxxx    1/1  Running
istiod-basic-v1-26-8-yyyyy    1/1  Running   ← 新嘅，閒置
```

---

### A.3 Step 1.6: Verify Tag Auto-Follow

**Operator 會自動更新 `activeRevisionName`，唔使 patch tag！**

```bash
# Check tag already points to new revision
oc get istiorevisiontag default -n istio-system

# Fallback（如果冇 auto-follow）：
# oc patch istiorevisiontag default -n istio-system --type merge \
#   -p '{"spec":{"targetRef":{"kind":"IstioRevision","name":"basic-v1-26-8"}}}'
```

**Patch 後 Cluster 狀態 — Tag 已 auto-follow ✅**

```yaml
# IstioRevisionTag — 冇人改過 spec，但 status update 咗
apiVersion: sailoperator.io/v1
kind: IstioRevisionTag
metadata:
  name: default
  namespace: istio-system
spec:
  targetRef:
    kind: Istio                       # ← Phase 0 到而家都冇改過
    name: basic
status:
  revision: basic-v1-26-8             # ← ✅ Auto-follow 咗 activeRevisionName
  observedGeneration: 2

# Istio CR — operator 自動更新咗 activeRevision
spec:
  version: v1.26.8
status:
  activeRevisionName: basic-v1-26-8   # ← ✅ Operator 自動更新

# IstioRevision — 仲未 restart，status 未反映切換
items:
- name: basic-v1-24-3
  status:
    InUse: true                       # ← workload 仲未 restart
- name: basic-v1-26-8
  status:
    InUse: false                      # ← 未有 workload 用
```

**Tag resolve chain 而家（同 Phase 0 完全一樣，只係 revision 變咗）：**

```
istio.io/rev=default
  → IstioRevisionTag/default
    → targetRef: Istio/basic               ← 冇改過
      → status.activeRevisionName: basic-v1-26-8  ← ✅ operator 自動更新
        → sidecar injector 用 basic-v1-26-8
          ❌ 但 workload 未 restart，sidecar 仲係舊版！
```

---

### A.4 Step 1.7: Restart Workloads

```bash
# Restart all workloads (sidecar 重新注入新版)
for ns in mesh-demo-1 mesh-demo-2 mesh-demo-3 mesh-demo-4 mesh-demo-5 project-01; do
  oc rollout restart deployment -n $ns
done

# Gateway — 根據 istio-system NS 嘅 istio.io/rev=default resolve 到新版 revision
oc rollout restart deployment -n istio-system istio-ingressgateway-ossm3
oc rollout restart deployment -n istio-system otel-collector
```

**Restart 後 Cluster 狀態:**

```yaml
# IstioRevision — 角色調轉
items:
- name: basic-v1-24-3
  status:
    InUse: false                    # ← 冇 workload 用
- name: basic-v1-26-8
  status:
    InUse: true                     # ← 所有 workload 用緊
```

```bash
# istioctl ps 結果
PROXY NAME                              VERSION
mesh-demo-1/frontend-xyz99             istio-1.25.3   ← 新版！
mesh-demo-2/backend-abc88              istio-1.25.3   ← 新版！
```

**Grace period 後 operator 自動清理舊 revision:**
```
oc get istiorevision -n istio-system
NAME              STATUS    VERSION    AGE
basic-v1-26-8     Healthy   v1.26.8    5m
```

✅ **3.0 → 3.1 完成！**

---

### A.5 TargetRef 變化總結

| 階段 | targetRef `kind` | targetRef `name` | Tag 指向 | 點解？ |
|------|------------------|-------------------|----------|--------|
| Phase 0 | `Istio` | `basic` | `basic-v1-24-3` (auto-follow activeRevision) | 初始設定，tag 跟住 Istio CR |
| **Step 1.3 patch version** | `Istio` (冇變) | `basic` (冇變) | **`basic-v1-26-8`** ✅ | **operator 自動更新 activeRevision + tag auto-follow** |
| Step 1.6 verify + restart | `Istio` (冇變) | `basic` (冇變) | `basic-v1-26-8` ✅ | tag 已 auto-follow，restart 就得 |
| Step 1.7 restart complete | `Istio` (冇變) | `basic` (冇變) | `basic-v1-26-8` ✅ | workload 重新注入新版 sidecar |
| 下次升級 | `Istio` (冇變) | `basic` (冇變) | `basic-v1-27-9` ✅ | 重複同一個 pattern — patch version → operator auto-follow → restart

# Autonomous Kubernetes Self-Healing AIOps Operator

[![Go Report Card](https://goreportcard.com/badge/github.com/qadeeraay/k8s-aiops-self-healing-operator)](https://github.com/qadeeraay/k8s-aiops-self-healing-operator)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-client--go%20v0.32-blue?logo=kubernetes&logoColor=white)](pkg/watcher)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](engine/analyzer.py)
[![Architecture: Two-Tier Hybrid](https://img.shields.io/badge/Architecture-Two--Tier%20Hybrid-orange)](#system-architecture)

> **A production-grade, two-tier autonomous Kubernetes operator engineered for low-latency in-cluster event streaming, strict multi-tenant blast-radius containment, and safe remediation of stateful workloads (PostgreSQL, Hasura GraphQL, Auth, Storage) on AWS EKS.**

---

## Executive Architecture Summary

Production multi-tenant Kubernetes platforms—such as Nhost's managed developer cloud—host thousands of stateful PostgreSQL instances, Auth endpoints, and GraphQL microservices across shared EKS clusters. In these multi-tenant environments, traditional auto-remediation tools introduce severe systemic risks:
1. **API Server Polling Thrashing:** Naive controllers repeatedly poll `GET /api/v1/pods`, inducing heavy etcd serialization overhead, CPU spikes, and API rate-limiting under high pod churn.
2. **Flapping Death Spirals:** Blind restart loops on broken stateful workloads (e.g., database schema incompatibility or WAL corruption) thrash storage volumes and trigger cascading failover storms.
3. **Split-Brain & PDB Violations:** Uncoordinated hard pod deletions (`kubectl delete pod --force`) can evict an active PostgreSQL primary or drop cluster quorum below `PodDisruptionBudget` (PDB) thresholds.

To eliminate these failure modes, this operator implements a **clean two-tier hybrid architecture**:
* **Tier 1: Engine Core (Go + `client-go`):** Connects to the Kubernetes apiserver via HTTP/2 watch streams using `SharedIndexInformer` with DeltaFIFO caching. It delivers **zero polling overhead**, sub-second event interception, lock-free workqueue scheduling, thread-safe in-memory rate-limiting, and non-destructive API mutations.
* **Tier 2: Heuristic Policy Layer (Python):** A decoupled, testable decision engine (`engine/analyzer.py`) that evaluates metric telemetry (simulating Prometheus/VictoriaMetrics signals) to distinguish transient network/scheduling jitter from genuine persistent crashes.
* **Safety Gate (In-Memory Circuit Breaker):** Enforces an ironclad rate limit of **maximum 1 remediation per workload per 10 minutes**, bounding blast-radius and isolating tenant failures.

---

## System Architecture

```
+----------------------------------------------------------------------------------------------------+
|                                      KUBERNETES EKS CLUSTER                                        |
|                                                                                                    |
|   +--------------------------+       HTTP/2 Watch Stream       +-------------------------------+   |
|   | Stateful Tenant Workload | ------------------------------> |      client-go PodWatcher     |   |
|   | (PostgreSQL / GraphQL)   |      (Zero Polling Overhead)    |     (SharedIndexInformer)     |   |
|   +--------------------------+                                 +---------------+---------------+   |
+--------------------------------------------------------------------------------|-------------------+
                                                                                 | DeltaFIFO / Cache
                                                                                 v
                                                                 +-------------------------------+
                                                                 | Anomaly Filter & Classifier   |
                                                                 | - PodCrashLoopBackOff         |
                                                                 | - OOMKilled (Exit 137)        |
                                                                 | - Failed Liveness/Readiness   |
                                                                 +---------------+---------------+
                                                                                 | Dispatches Pod Key
                                                                                 v
                                                                 +-------------------------------+
                                                                 | RateLimiting WorkQueue (Go)   |
                                                                 +---------------+---------------+
                                                                                 | Worker Pull
                                                                                 v
+--------------------------------------------------------------------------------+-------------------+
|                                HEURISTIC & RECONCILIATION ENGINE                                  |
|                                                                                                    |
|    +-----------------------------+          JSON           +----------------------------------+    |
|    |   Python Policy Analyzer    | <---------------------> | In-Memory Sliding Circuit Breaker|    |
|    |   (engine/analyzer.py)      |     Telemetry Eval      | (Strict: 1 action / 10m / wrkld) |    |
|    |                             |                         +-----------------+----------------+    |
|    | - Memory >= 90% -> OOM-Fix  |                                           |                     |
|    | - Restarts >= 3 -> Isolate  |                                           | If Permitted        |
|    | - Probes < 2   -> Suppress  |                                           v                     |
+----+-----------------------------+-------------------------+---------------------------------------+
                                                             |
                                                             v
+----------------------------------------------------------------------------------------------------+
|                                SAFE KUBERNETES API MUTATION ENGINE                                 |
|                                                                                                    |
|  [Action A] PDB-Compliant Eviction via policy/v1 Eviction Subresource                              |
|             (Rejects with 429 TooManyRequests if minAvailable would be violated)                   |
|                                                                                                    |
|  [Action B] Non-Destructive Telemetry Quarantine Tagging:                                          |
|             aiops.nhost.io/quarantine: "true"                                                      |
|             aiops.nhost.io/circuit-breaker: "tripped"                                              |
|             aiops.nhost.io/remediated-at: "<timestamp>"                                            |
|                                                                                                    |
|  [Action C] Kubernetes Audit Trail: Emits corev1.Event for forensic kubectl describe pod           |
|  [Action D] High-Performance Structured Logging via standard library 'log/slog'                   |
+----------------------------------------------------------------------------------------------------+
```

---

## Component Demarcation

| Layer | Implementation | Responsibilities |
| :--- | :--- | :--- |
| **Engine Core** | Go 1.22+ & `client-go` v0.32 | Low-latency HTTP/2 watch streaming, DeltaFIFO caching, workqueue concurrency, thread-safe circuit breaker, PDB-compliant evictions, atomic JSON merge patches, and `log/slog` observability. |
| **Policy Layer** | Python 3.12+ (`engine/analyzer.py`) | Pure, side-effect-free heuristic evaluation: ingest container restart velocity, memory working set %, and consecutive probe failures. Distinguishes transient jitter from persistent failures. |
| **RBAC Matrix** | `manifests/rbac.yaml` | Minimal least-privilege `ClusterRole` granting `get, list, watch, update, patch, delete` on `pods` & `pods/status`, and `create` on `pods/eviction`. |

---

## Design Trade-offs & Production Safeguards

### 1. Zero API Polling Overhead vs. Periodic Polling
* **The Pitfall:** Many naive operators poll the Kubernetes API periodically (`for range ticker.C { client.List(...) }`). Across large clusters with 5,000+ pods, this saturates the apiserver, starves etcd I/O, and causes control-plane latency degradation.
* **Our Implementation:** We utilize `client-go`'s `SharedIndexInformer`. After an initial synchronized list, the informer receives incremental deltas via a single persistent HTTP/2 watch connection. Updates are reflected in a local, lock-free in-memory cache (`PodLister`). Zero polling calls ever hit the apiserver or etcd.

### 2. Multi-Tenant Blast-Radius Containment & Anti-Flapping Gate
* **The Pitfall:** When a broken application (e.g., malformed database migration or corrupted volume mount) continuously crashes, an automated operator that blindly restarts the pod creates an infinite flapping loop. In multi-tenant clusters, this exhausts worker node CPU/memory and drowns observability pipelines.
* **Our Implementation:** An in-memory, thread-safe **Sliding-Window Circuit Breaker** (`pkg/remediator/reconciler.go`) tracks remediation history partitioned by `namespace/workload`:
  $$\text{Remediation Budget} = \text{Maximum 1 action per workload per 10 minutes (600s)}$$
  If a workload attempts a second remediation within 10 minutes, the circuit breaker **trips**. The operator immediately halts automated mutations, tags the pod with `aiops.nhost.io/circuit-breaker: "tripped"`, and emits a Kubernetes `Warning` audit event for human SRE escalation. Workload state is strictly partitioned: a failure in `tenant-a` cannot trip or delay remediation for `tenant-b`.

### 3. Stateful Workload Safety & PDB Preservation
* **The Pitfall:** Blindly deleting pods via `kubectl delete pod <name> --grace-period=0` bypasses Kubernetes safety checks. If applied to a stateful replica (e.g., a multi-node PostgreSQL cluster managed by Patroni or CloudNativePG), it can trigger split-brain scenarios, break Raft quorum, or violate tenant SLA availability.
* **Our Implementation:** We never issue raw force deletions. Instead, the reconciler submits a `policy/v1.Eviction` API subresource call:
  ```go
  eviction := &policyv1.Eviction{
      ObjectMeta: metav1.ObjectMeta{Name: pod.Name, Namespace: pod.Namespace},
  }
  err := clientset.PolicyV1().Evictions(pod.Namespace).Evict(ctx, eviction)
  ```
  If an eviction would cause the workload to drop below its configured `PodDisruptionBudget` (`minAvailable`), the Kubernetes apiserver rejects the call with HTTP `429 TooManyRequests`. The operator intercepts this 429, preserves the running pod to maintain quorum, and annotates the pod with `aiops.nhost.io/pdb-status: "protected-by-pdb"` for operator review.

### 4. Heuristic Jitter Suppression for Distributed Services
* **The Pitfall:** Transient network partitions, AWS EKS CNI ipamd IP allocation delays, or high-load packet drops frequently cause liveness/readiness probes to drop intermittently (e.g., 1 probe failure). Reacting immediately by restarting the pod restarts an otherwise healthy container, dropping live database connections and causing user-visible 502s.
* **Our Implementation:** The Python policy module enforces a **jitter suppression window**:
  - `consecutive_probe_failures < 2` $\rightarrow$ Action: `SUPPRESS` (`"Transient network jitter"`).
  - `memory_working_set >= 90%` $\rightarrow$ Action: `TRIGGER_PREVENTATIVE_RESTART` (`"Impending OOMKill"` before Linux kernel OOM-killer fires exit 137).
  - `restart_velocity >= 3 in 5m` $\rightarrow$ Action: `ISOLATE_POD` (`"Persistent crash loop"`).

---

## Repository Structure

```
.
├── cmd/
│   └── operator/
│       └── main.go              # Go entrypoint: config loader, informer boot, signal handling
├── pkg/
│   ├── watcher/
│   │   ├── informer.go          # client-go SharedIndexInformer, DeltaFIFO, anomaly detection
│   │   └── informer_test.go     # Unit tests for informer filters & owner resolution
│   └── remediator/
│       ├── reconciler.go        # Reconciler, thread-safe Circuit Breaker, PDB eviction
│       └── reconciler_test.go   # Unit tests for sliding-window rate limiting
├── engine/
│   ├── analyzer.py              # Pluggable Python telemetry analyzer (OOM, probe, crashloop)
│   └── test_analyzer.py         # Unit tests for heuristic policy logic
├── manifests/
│   └── rbac.yaml                # Least-privilege ServiceAccount, ClusterRole, ClusterRoleBinding
├── Makefile                     # Build, test, and lint automation targets
└── README.md                    # System documentation and SRE runbook
```

---

## Quickstart Guide

### Prerequisites
* Go 1.22+
* Python 3.12+
* A local Kubernetes cluster (`kind` or `minikube`)
* `kubectl` configured with cluster admin context

### 1. Start Local Cluster
```bash
# Using kind
kind create cluster --name aiops-dev

# Verify cluster connectivity
kubectl cluster-info
```

### 2. Apply Least-Privilege RBAC Manifests
```bash
kubectl apply -f manifests/rbac.yaml
```

Output:
```
namespace/aiops-system created
serviceaccount/aiops-operator created
clusterrole.rbac.authorization.k8s.io/aiops-operator-role created
clusterrolebinding.rbac.authorization.k8s.io/aiops-operator-binding created
```

### 3. Run the Test Suites

#### Run Go Unit Tests
```bash
go test -v -race ./pkg/watcher ./pkg/remediator
```

#### Run Python Heuristic Policy Tests
```bash
python3 -m unittest discover -s engine -v
```

### 4. Build and Run the Operator Locally
```bash
# Build binary
go build -o bin/operator ./cmd/operator

# Run locally using current kubeconfig context
./bin/operator --kubeconfig=$HOME/.kube/config --workers=2 --json-log=true
```

Sample structured JSON startup log:
```json
{"time":"2026-09-18T11:55:00.123Z","level":"INFO","msg":"bootstrapping Kubernetes AIOps Self-Healing Operator","version":"2.0.0","architecture":"two-tier-hybrid","resync_period":"10m0s","reconciler_workers":2}
{"time":"2026-09-18T11:55:00.124Z","level":"INFO","msg":"using explicit kubeconfig flag","path":"/home/user/.kube/config"}
{"time":"2026-09-18T11:55:00.130Z","level":"INFO","msg":"starting SharedIndexInformer factory","component":"pod_watcher"}
{"time":"2026-09-18T11:55:00.131Z","level":"INFO","msg":"waiting for informer cache to synchronize","component":"pod_watcher"}
{"time":"2026-09-18T11:55:00.232Z","level":"INFO","msg":"informer cache successfully synchronized; zero-polling event stream active","component":"pod_watcher"}
{"time":"2026-09-18T11:55:00.233Z","level":"INFO","msg":"operator core active and watching cluster event streams; awaiting signals"}
```

### 5. Verify Standalone Telemetry Policy Analyzer
The Python heuristic module can be executed standalone or piped from external telemetry engines:

```bash
# Impending OOMKill evaluation
python3 -m engine.analyzer '{"memory_working_set_percentage": 94.2, "restart_count": 0, "consecutive_probe_failures": 0}'

# Transient probe jitter suppression
python3 -m engine.analyzer '{"memory_working_set_percentage": 50.0, "restart_count": 0, "consecutive_probe_failures": 1}'

# Persistent crash loop isolation
python3 -m engine.analyzer '{"memory_working_set_percentage": 42.0, "restart_count": 4, "consecutive_probe_failures": 0, "restart_window_seconds": 180}'
```

Sample Output:
```json
{
  "action": "TRIGGER_PREVENTATIVE_RESTART",
  "confidence": 0.94,
  "reason": "Impending OOMKill"
}
```

---

## Telemetry Evaluation Rules Reference

| Metric Signal | Threshold | Evaluated Action | SRE Rationale |
| :--- | :--- | :--- | :--- |
| `memory_working_set_percentage` | $\ge 90.0\%$ | `TRIGGER_PREVENTATIVE_RESTART` | Preempts kernel OOM-killer (exit 137), enabling orderly failover without dirty buffer corruption. |
| `container_restart_count` | $\ge 3$ in $300\text{s}$ | `ISOLATE_POD` | Prevents flapping death spirals; quarantines workload for post-mortem analysis. |
| `consecutive_probe_failures` | $< 2$ | `SUPPRESS` | Eliminates false-positive restarts caused by transient network jitter or high CPU scheduling delay. |
| `consecutive_probe_failures` | $\ge 2$ | `INVESTIGATE_PROBE_FAILURE` | Persistent probe failure exceeding jitter tolerance; flags for diagnostic cycling. |
| All Metrics | Within limits | `MONITOR` | Nominal workload operation. |

---

## License
MIT License. Crafted for enterprise cloud platform reliability engineering.

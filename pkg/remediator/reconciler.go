package remediator

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"
	"time"

	policyv1 "k8s.io/api/policy/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/kubernetes"
	corev1listers "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/workqueue"

	"github.com/qadeeraay/k8s-aiops-self-healing-operator/pkg/watcher"
)

// CircuitBreaker enforces a rate limit of max 1 remediation per workload per 10 minutes.
// ponytail: using map[string]time.Time since limit is 1 per window.
type CircuitBreaker struct {
	mu     sync.Mutex
	limit  int
	window time.Duration
	last   map[string]time.Time
}

func NewCircuitBreaker(limit int, window time.Duration) *CircuitBreaker {
	return &CircuitBreaker{limit: limit, window: window, last: make(map[string]time.Time)}
}

func (cb *CircuitBreaker) CanRemediate(namespace, workload string) (bool, string) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	key := namespace + "/" + workload
	if last, ok := cb.last[key]; ok && time.Since(last) < cb.window {
		remaining := (cb.window - time.Since(last)).Round(time.Second)
		return false, fmt.Sprintf("Circuit breaker active for %s: 1 action per %v limit (cooldown remaining: %s)", key, cb.window, remaining)
	}
	return true, "Permitted"
}

func (cb *CircuitBreaker) RecordRemediation(namespace, workload string) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.last[namespace+"/"+workload] = time.Now()
}

func (cb *CircuitBreaker) Reset(namespace, workload string) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	delete(cb.last, namespace+"/"+workload)
}

type Reconciler struct {
	clientset kubernetes.Interface
	podLister corev1listers.PodLister
	queue     workqueue.TypedRateLimitingInterface[string]
	breaker   *CircuitBreaker
	logger    *slog.Logger
}

func NewReconciler(
	clientset kubernetes.Interface,
	podLister corev1listers.PodLister,
	queue workqueue.TypedRateLimitingInterface[string],
	logger *slog.Logger,
) *Reconciler {
	return &Reconciler{
		clientset: clientset,
		podLister: podLister,
		queue:     queue,
		breaker:   NewCircuitBreaker(1, 10*time.Minute),
		logger:    logger.With("component", "reconciler"),
	}
}

func (r *Reconciler) Run(ctx context.Context, workers int) {
	defer r.queue.ShutDown()
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			wait.UntilWithContext(ctx, func(ctx context.Context) {
				for r.processNext(ctx) {
				}
			}, time.Second)
		}()
	}
	<-ctx.Done()
	wg.Wait()
}

func (r *Reconciler) processNext(ctx context.Context) bool {
	key, shutdown := r.queue.Get()
	if shutdown {
		return false
	}
	defer r.queue.Done(key)

	if err := r.reconcile(ctx, key); err != nil {
		if r.queue.NumRequeues(key) < 3 {
			r.queue.AddRateLimited(key)
			return true
		}
		r.logger.Error("reconciliation failed after retries", "key", key, "error", err)
	}
	r.queue.Forget(key)
	return true
}

func (r *Reconciler) reconcile(ctx context.Context, key string) error {
	ns, name, err := cache.SplitMetaNamespaceKey(key)
	if err != nil {
		return nil
	}

	pod, err := r.podLister.Pods(ns).Get(name)
	if apierrors.IsNotFound(err) || (pod != nil && pod.DeletionTimestamp != nil) {
		return nil
	}
	if err != nil {
		return err
	}

	anomaly, ok := watcher.DetectPodAnomaly(pod)
	if !ok {
		return nil
	}

	workload, _ := watcher.ResolveWorkloadOwner(pod)
	permitted, reason := r.breaker.CanRemediate(ns, workload)
	if !permitted {
		r.logger.Warn("remediation blocked by circuit breaker", "workload", workload, "pod", name, "reason", reason)
		_ = r.annotate(ctx, ns, name, map[string]string{
			"aiops.nhost.io/circuit-breaker": "tripped",
			"aiops.nhost.io/reason":          reason,
		})
		return nil
	}

	// Safe action: PDB-compliant graceful eviction
	_ = r.annotate(ctx, ns, name, map[string]string{
		"aiops.nhost.io/action":        "GracefulEviction",
		"aiops.nhost.io/anomaly":       string(anomaly.Anomaly),
		"aiops.nhost.io/remediated-at": time.Now().UTC().Format(time.RFC3339),
	})

	eviction := &policyv1.Eviction{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns},
	}
	if err := r.clientset.PolicyV1().Evictions(ns).Evict(ctx, eviction); err != nil {
		if apierrors.IsTooManyRequests(err) {
			r.logger.Warn("eviction blocked by PDB; preserving stateful replica", "pod", name)
			_ = r.annotate(ctx, ns, name, map[string]string{"aiops.nhost.io/pdb-status": "protected"})
			r.breaker.RecordRemediation(ns, workload)
			return nil
		}
		return fmt.Errorf("eviction failed: %w", err)
	}

	r.breaker.RecordRemediation(ns, workload)
	r.logger.Info("pod evicted safely", "namespace", ns, "workload", workload, "pod", name)
	return nil
}

func (r *Reconciler) annotate(ctx context.Context, ns, name string, annotations map[string]string) error {
	payload, _ := json.Marshal(map[string]any{"metadata": map[string]any{"annotations": annotations}})
	_, err := r.clientset.CoreV1().Pods(ns).Patch(ctx, name, types.MergePatchType, payload, metav1.PatchOptions{})
	return err
}

func (r *Reconciler) CircuitBreaker() *CircuitBreaker { return r.breaker }

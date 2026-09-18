package watcher

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	corev1listers "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/workqueue"
)

type AnomalyType string

const (
	AnomalyCrashLoopBackOff AnomalyType = "PodCrashLoopBackOff"
	AnomalyOOMKilled        AnomalyType = "OOMKilled"
	AnomalyProbeFailed      AnomalyType = "ProbeFailed"
)

type AnomalyPayload struct {
	Namespace     string      `json:"namespace"`
	PodName       string      `json:"pod_name"`
	WorkloadName  string      `json:"workload_name"`
	WorkloadKind  string      `json:"workload_kind"`
	ContainerName string      `json:"container_name"`
	Anomaly       AnomalyType `json:"anomaly"`
	Reason        string      `json:"reason"`
	Message       string      `json:"message"`
	ExitCode      int32       `json:"exit_code"`
	RestartCount  int32       `json:"restart_count"`
	ObservedAt    time.Time   `json:"observed_at"`
}

type PodWatcher struct {
	factory   informers.SharedInformerFactory
	informer  cache.SharedIndexInformer
	lister    corev1listers.PodLister
	queue     workqueue.TypedRateLimitingInterface[string]
	logger    *slog.Logger
}

func NewPodWatcher(
	clientset kubernetes.Interface,
	resync time.Duration,
	queue workqueue.TypedRateLimitingInterface[string],
	logger *slog.Logger,
) *PodWatcher {
	factory := informers.NewSharedInformerFactory(clientset, resync)
	pods := factory.Core().V1().Pods()
	pw := &PodWatcher{
		factory:  factory,
		informer: pods.Informer(),
		lister:   pods.Lister(),
		queue:    queue,
		logger:   logger.With("component", "watcher"),
	}

	pw.informer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			if pod, ok := obj.(*corev1.Pod); ok {
				pw.enqueueIfAnomalous(pod)
			}
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			oldPod, ok1 := oldObj.(*corev1.Pod)
			newPod, ok2 := newObj.(*corev1.Pod)
			if ok1 && ok2 && oldPod.ResourceVersion != newPod.ResourceVersion {
				pw.enqueueIfAnomalous(newPod)
			}
		},
	})
	return pw
}

func (pw *PodWatcher) enqueueIfAnomalous(pod *corev1.Pod) {
	if pod.DeletionTimestamp != nil || pod.Status.Phase == corev1.PodSucceeded {
		return
	}
	if anomaly, ok := DetectPodAnomaly(pod); ok {
		key, _ := cache.MetaNamespaceKeyFunc(pod)
		pw.logger.Warn("anomaly detected", "key", key, "type", anomaly.Anomaly, "reason", anomaly.Reason)
		pw.queue.Add(key)
	}
}

// DetectPodAnomaly filters specifically for CrashLoopBackOff, OOMKilled, or failed probes.
func DetectPodAnomaly(pod *corev1.Pod) (*AnomalyPayload, bool) {
	workload, kind := ResolveWorkloadOwner(pod)
	hasRestarts := false

	for _, cs := range append(pod.Status.InitContainerStatuses, pod.Status.ContainerStatuses...) {
		if cs.RestartCount > 0 {
			hasRestarts = true
		}
		if cs.State.Waiting != nil && cs.State.Waiting.Reason == "CrashLoopBackOff" {
			return &AnomalyPayload{
				Namespace: pod.Namespace, PodName: pod.Name, WorkloadName: workload, WorkloadKind: kind,
				ContainerName: cs.Name, Anomaly: AnomalyCrashLoopBackOff, Reason: cs.State.Waiting.Reason,
				Message: cs.State.Waiting.Message, RestartCount: cs.RestartCount, ObservedAt: time.Now().UTC(),
			}, true
		}
		for _, term := range []*corev1.ContainerStateTerminated{cs.State.Terminated, cs.LastTerminationState.Terminated} {
			if term != nil && (term.Reason == "OOMKilled" || term.ExitCode == 137) {
				return &AnomalyPayload{
					Namespace: pod.Namespace, PodName: pod.Name, WorkloadName: workload, WorkloadKind: kind,
					ContainerName: cs.Name, Anomaly: AnomalyOOMKilled, Reason: term.Reason,
					Message: term.Message, ExitCode: term.ExitCode, RestartCount: cs.RestartCount, ObservedAt: time.Now().UTC(),
				}, true
			}
		}
		if pod.Status.Phase == corev1.PodRunning && !cs.Ready && cs.RestartCount > 0 {
			reason := "ContainerUnreadyWithRestarts"
			if cs.State.Waiting != nil && cs.State.Waiting.Reason != "" {
				reason = cs.State.Waiting.Reason
			}
			return &AnomalyPayload{
				Namespace: pod.Namespace, PodName: pod.Name, WorkloadName: workload, WorkloadKind: kind,
				ContainerName: cs.Name, Anomaly: AnomalyProbeFailed, Reason: reason,
				RestartCount: cs.RestartCount, ObservedAt: time.Now().UTC(),
			}, true
		}
	}

	for _, cond := range pod.Status.Conditions {
		if cond.Type == corev1.PodReady && cond.Status == corev1.ConditionFalse && pod.Status.Phase == corev1.PodRunning && hasRestarts {
			return &AnomalyPayload{
				Namespace: pod.Namespace, PodName: pod.Name, WorkloadName: workload, WorkloadKind: kind,
				Anomaly: AnomalyProbeFailed, Reason: cond.Reason, Message: cond.Message, ObservedAt: time.Now().UTC(),
			}, true
		}
	}
	return nil, false
}

// ResolveWorkloadOwner resolves controller owner name (e.g. Deployment from ReplicaSet hash).
func ResolveWorkloadOwner(pod *corev1.Pod) (string, string) {
	for _, owner := range pod.OwnerReferences {
		if owner.Controller != nil && *owner.Controller {
			if owner.Kind == "ReplicaSet" {
				if idx := strings.LastIndex(owner.Name, "-"); idx > 0 {
					return owner.Name[:idx], "Deployment"
				}
			}
			return owner.Name, owner.Kind
		}
	}
	return pod.Name, "Pod"
}

func (pw *PodWatcher) Run(ctx context.Context) error {
	pw.factory.Start(ctx.Done())
	if !cache.WaitForCacheSync(ctx.Done(), pw.informer.HasSynced) {
		return fmt.Errorf("informer sync timed out")
	}
	pw.logger.Info("informer cache synced; zero polling event stream active")
	<-ctx.Done()
	return nil
}

func (pw *PodWatcher) Lister() corev1listers.PodLister { return pw.lister }
func (pw *PodWatcher) HasSynced() bool                { return pw.informer.HasSynced() }

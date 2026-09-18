package watcher

import (
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestDetectPodAnomaly_CrashLoopBackOff(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "auth-service-78f99d658-xyz12",
			Namespace: "nhost-auth",
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:         "auth",
					Ready:        false,
					RestartCount: 4,
					State: corev1.ContainerState{
						Waiting: &corev1.ContainerStateWaiting{
							Reason:  "CrashLoopBackOff",
							Message: "Back-off 5m0s restarting failed container",
						},
					},
				},
			},
		},
	}

	anomaly, detected := DetectPodAnomaly(pod)
	if !detected {
		t.Fatalf("expected anomaly to be detected for CrashLoopBackOff")
	}

	if anomaly.Anomaly != AnomalyCrashLoopBackOff {
		t.Errorf("expected AnomalyCrashLoopBackOff, got %s", anomaly.Anomaly)
	}

	if anomaly.RestartCount != 4 {
		t.Errorf("expected restart count 4, got %d", anomaly.RestartCount)
	}
}

func TestDetectPodAnomaly_OOMKilled(t *testing.T) {
	// Active termination state with exit code 137
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "graphql-engine-56c4bd6bc-q89rt",
			Namespace: "tenant-hasura",
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:         "graphql",
					Ready:        false,
					RestartCount: 2,
					State: corev1.ContainerState{
						Terminated: &corev1.ContainerStateTerminated{
							ExitCode: 137,
							Reason:   "OOMKilled",
							Message:  "Memory cgroup out of memory",
						},
					},
				},
			},
		},
	}

	anomaly, detected := DetectPodAnomaly(pod)
	if !detected {
		t.Fatalf("expected anomaly to be detected for OOMKilled exit 137")
	}

	if anomaly.Anomaly != AnomalyOOMKilled {
		t.Errorf("expected AnomalyOOMKilled, got %s", anomaly.Anomaly)
	}
}

func TestDetectPodAnomaly_OOMKilled_LastTerminationState(t *testing.T) {
	// Container restarted after OOMKill and is waiting
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "postgres-worker-0",
			Namespace: "tenant-db",
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:         "postgres",
					Ready:        false,
					RestartCount: 1,
					State: corev1.ContainerState{
						Waiting: &corev1.ContainerStateWaiting{
							Reason: "ContainerCreating",
						},
					},
					LastTerminationState: corev1.ContainerState{
						Terminated: &corev1.ContainerStateTerminated{
							ExitCode: 137,
							Reason:   "OOMKilled",
						},
					},
				},
			},
		},
	}

	anomaly, detected := DetectPodAnomaly(pod)
	if !detected {
		t.Fatalf("expected anomaly to be detected from LastTerminationState")
	}

	if anomaly.Anomaly != AnomalyOOMKilled {
		t.Errorf("expected AnomalyOOMKilled, got %s", anomaly.Anomaly)
	}
}

func TestDetectPodAnomaly_ProbeFailure(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "storage-api-74bf887d9-klm45",
			Namespace: "nhost-storage",
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:         "storage",
					Ready:        false,
					RestartCount: 2,
					State: corev1.ContainerState{
						Running: &corev1.ContainerStateRunning{
							StartedAt: metav1.NewTime(time.Now().Add(-10 * time.Minute)),
						},
					},
				},
			},
		},
	}

	anomaly, detected := DetectPodAnomaly(pod)
	if !detected {
		t.Fatalf("expected anomaly to be detected for unready container with restarts")
	}

	if anomaly.Anomaly != AnomalyProbeFailed {
		t.Errorf("expected AnomalyProbeFailed, got %s", anomaly.Anomaly)
	}
}

func TestDetectPodAnomaly_HealthyPod(t *testing.T) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "healthy-api-548c77b94-98z76",
			Namespace: "default",
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			ContainerStatuses: []corev1.ContainerStatus{
				{
					Name:         "app",
					Ready:        true,
					RestartCount: 0,
					State: corev1.ContainerState{
						Running: &corev1.ContainerStateRunning{
							StartedAt: metav1.NewTime(time.Now().Add(-1 * time.Hour)),
						},
					},
				},
			},
		},
	}

	_, detected := DetectPodAnomaly(pod)
	if detected {
		t.Errorf("expected healthy pod to not trigger any anomaly")
	}
}

func TestResolveWorkloadOwner(t *testing.T) {
	isController := true

	// Test 1: Deployment via ReplicaSet owner
	pod1 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name: "hasura-graphql-78484bf874-5k49m",
			OwnerReferences: []metav1.OwnerReference{
				{
					APIVersion: "apps/v1",
					Kind:       "ReplicaSet",
					Name:       "hasura-graphql-78484bf874",
					Controller: &isController,
				},
			},
		},
	}
	name1, kind1 := ResolveWorkloadOwner(pod1)
	if name1 != "hasura-graphql" || kind1 != "Deployment" {
		t.Errorf("expected hasura-graphql/Deployment, got %s/%s", name1, kind1)
	}

	// Test 2: StatefulSet owner
	pod2 := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name: "postgres-cluster-0",
			OwnerReferences: []metav1.OwnerReference{
				{
					APIVersion: "apps/v1",
					Kind:       "StatefulSet",
					Name:       "postgres-cluster",
					Controller: &isController,
				},
			},
		},
	}
	name2, kind2 := ResolveWorkloadOwner(pod2)
	if name2 != "postgres-cluster" || kind2 != "StatefulSet" {
		t.Errorf("expected postgres-cluster/StatefulSet, got %s/%s", name2, kind2)
	}
}

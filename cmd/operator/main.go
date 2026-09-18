package main

import (
	"context"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/util/workqueue"

	"github.com/qadeeraay/k8s-aiops-self-healing-operator/pkg/remediator"
	"github.com/qadeeraay/k8s-aiops-self-healing-operator/pkg/watcher"
)

func main() {
	kubeconfig := flag.String("kubeconfig", "", "Path to kubeconfig (default: in-cluster / ~/.kube/config)")
	resync := flag.Duration("resync-period", 10*time.Minute, "Informer resync period")
	workers := flag.Int("workers", 2, "Reconciler worker concurrency")
	flag.Parse()

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	cfg, err := buildKubeConfig(*kubeconfig)
	if err != nil {
		logger.Error("failed to load kubeconfig", "error", err)
		os.Exit(1)
	}
	cfg.QPS, cfg.Burst = 50, 100

	clientset, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		logger.Error("failed to create clientset", "error", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	queue := workqueue.NewTypedRateLimitingQueue[string](workqueue.DefaultTypedControllerRateLimiter[string]())
	podWatcher := watcher.NewPodWatcher(clientset, *resync, queue, logger)
	reconcileEngine := remediator.NewReconciler(clientset, podWatcher.Lister(), queue, logger)

	go podWatcher.Run(ctx)
	go reconcileEngine.Run(ctx, *workers)

	logger.Info("operator running", "resync", *resync, "workers", *workers)
	<-ctx.Done()
	logger.Info("shutting down")
}

func buildKubeConfig(path string) (*rest.Config, error) {
	if path != "" {
		return clientcmd.BuildConfigFromFlags("", path)
	}
	// Native client-go resolution: env -> ~/.kube/config -> in-cluster
	return clientcmd.NewNonInteractiveDeferredLoadingClientConfig(
		clientcmd.NewDefaultClientConfigLoadingRules(),
		&clientcmd.ConfigOverrides{},
	).ClientConfig()
}

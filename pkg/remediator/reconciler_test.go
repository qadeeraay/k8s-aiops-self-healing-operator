package remediator

import (
	"testing"
	"time"
)

func TestCircuitBreaker_RateLimitingAndWindow(t *testing.T) {
	// 1 remediation per 10 minutes
	cb := NewCircuitBreaker(1, 10*time.Minute)

	namespace := "tenant-db-acme"
	workload := "postgres-primary"

	// 1st attempt: Should be permitted
	permitted, reason := cb.CanRemediate(namespace, workload)
	if !permitted {
		t.Fatalf("expected 1st remediation to be permitted, got reason: %s", reason)
	}

	// Record execution
	cb.RecordRemediation(namespace, workload)

	// 2nd attempt within 10 minutes: MUST be blocked
	permitted2, reason2 := cb.CanRemediate(namespace, workload)
	if permitted2 {
		t.Fatalf("expected 2nd remediation within 10m to be blocked by circuit breaker")
	}
	if reason2 == "" {
		t.Errorf("expected explanatory reason for circuit breaker trip")
	}

	// Workload isolation: Distinct tenant workload should still be permitted
	permittedOther, _ := cb.CanRemediate("tenant-auth-beta", "auth-service")
	if !permittedOther {
		t.Fatalf("circuit breaker trip on acme db should not affect beta auth service")
	}

	// Reset: SRE manual intervention clears trip state
	cb.Reset(namespace, workload)
	permittedAfterReset, _ := cb.CanRemediate(namespace, workload)
	if !permittedAfterReset {
		t.Fatalf("expected remediation to be permitted after SRE manual reset")
	}
}

func TestCircuitBreaker_WindowExpiration(t *testing.T) {
	// Use small window for expiration test
	shortWindow := 50 * time.Millisecond
	cb := NewCircuitBreaker(1, shortWindow)

	namespace := "tenant-storage"
	workload := "minio"

	permitted, _ := cb.CanRemediate(namespace, workload)
	if !permitted {
		t.Fatalf("expected first attempt to be permitted")
	}
	cb.RecordRemediation(namespace, workload)

	// Immediate check: blocked
	blocked, _ := cb.CanRemediate(namespace, workload)
	if blocked {
		t.Fatalf("expected immediate second attempt to be blocked")
	}

	// Wait for window to expire
	time.Sleep(70 * time.Millisecond)

	permittedAfterExpiry, _ := cb.CanRemediate(namespace, workload)
	if !permittedAfterExpiry {
		t.Fatalf("expected remediation to be permitted after sliding window expired")
	}
}

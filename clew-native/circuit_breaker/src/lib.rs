//! # clew-circuit-breaker
//!
//! Sliding-window circuit breaker for provider calls (LLM, MCP, etc).
//!
//! Ported from Grok Build's `xai-circuit-breaker` design:
//! - Three states: Closed, Open, HalfOpen.
//! - Trips when `sample_count >= min_samples AND error_rate >= error_rate_threshold`.
//! - Lock-free fast path via `AtomicBool` mirror of `is_open`.
//! - HalfOpen probe reclaim: abandoned probes are auto-reclaimed after `open_duration`.
//! - Per-key registry: one breaker per (provider, model) or (mcp_server, tool).
//!
//! Replaces Clew v1's heuristic `SubagentBatch._is_rate_limit_error` string-matching
//! with a real circuit breaker that tracks per-endpoint error rates over time.

use parking_lot::Mutex;
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BreakerState {
    Closed,
    Open,
    HalfOpen,
}

impl BreakerState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Closed => "closed",
            Self::Open => "open",
            Self::HalfOpen => "half_open",
        }
    }
}

#[derive(Debug, Clone)]
pub struct BreakerConfig {
    /// Minimum samples before the breaker can trip.
    pub min_samples: usize,
    /// Error rate threshold (0.0..=1.0) above which the breaker trips.
    pub error_rate_threshold: f64,
    /// Sliding window length.
    pub window: Duration,
    /// How long to stay Open before transitioning to HalfOpen.
    pub open_duration: Duration,
}

impl Default for BreakerConfig {
    fn default() -> Self {
        Self {
            min_samples: 10,
            error_rate_threshold: 0.5,
            window: Duration::from_secs(60),
            open_duration: Duration::from_secs(15),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Sample {
    Success,
    Failure,
    RateLimited,
}

struct Inner {
    samples: VecDeque<(Instant, Sample)>,
    state: BreakerState,
    opened_at: Option<Instant>,
    /// Set when HalfOpen probe is in flight; cleared on probe result.
    probe_claimed_at: Option<Instant>,
    /// Cumulative counters (lifetime, not just the window).
    total_success: u64,
    total_failure: u64,
    total_rate_limited: u64,
}

impl Inner {
    fn new() -> Self {
        Self {
            samples: VecDeque::with_capacity(64),
            state: BreakerState::Closed,
            opened_at: None,
            probe_claimed_at: None,
            total_success: 0,
            total_failure: 0,
            total_rate_limited: 0,
        }
    }

    fn prune(&mut self, now: Instant) {
        let cutoff = now - cfg.window;
        while let Some(&(t, _)) = self.samples.front() {
            if t < cutoff {
                self.samples.pop_front();
            } else {
                break;
            }
        }
    }

    fn error_rate(&self) -> f64 {
        if self.samples.is_empty() {
            return 0.0;
        }
        let errors = self
            .samples
            .iter()
            .filter(|(_, s)| !matches!(*s, Sample::Success))
            .count();
        errors as f64 / self.samples.len() as f64
    }
}

pub struct CircuitBreaker {
    key: String,
    cfg: BreakerConfig,
    inner: Mutex<Inner>,
    /// Lock-free fast-path mirror of `state == Open`. Read with Relaxed.
    is_open_fast: AtomicBool,
    /// Monotonic counter for metric export.
    generation: AtomicU64,
}

impl CircuitBreaker {
    pub fn new(key: impl Into<String>, cfg: BreakerConfig) -> Arc<Self> {
        Arc::new(Self {
            key: key.into(),
            cfg,
            inner: Mutex::new(Inner::new()),
            is_open_fast: AtomicBool::new(false),
            generation: AtomicU64::new(0),
        })
    }

    pub fn key(&self) -> &str {
        &self.key
    }

    /// Fast-path check: should the caller short-circuit?
    /// Returns `true` if the breaker is Open (calls should fail fast).
    pub fn is_open(&self) -> bool {
        self.is_open_fast.load(Ordering::Relaxed)
    }

    pub fn state(&self) -> BreakerState {
        self.inner.lock().state
    }

    /// Attempt to claim a HalfOpen probe slot. Returns true if this call is
    /// allowed to proceed (i.e., either Closed or a fresh HalfOpen probe).
    pub fn try_claim(&self) -> bool {
        let mut inner = self.inner.lock();
        let now = Instant::now();
        inner.prune(now);

        match inner.state {
            BreakerState::Closed => true,
            BreakerState::Open => {
                // Transition to HalfOpen if open_duration has elapsed.
                if let Some(opened) = inner.opened_at {
                    if now.duration_since(opened) >= self.cfg.open_duration {
                        inner.state = BreakerState::HalfOpen;
                        inner.probe_claimed_at = Some(now);
                        self.generation.fetch_add(1, Ordering::Relaxed);
                        tracing::info!(key = %self.key, "breaker: open -> half_open");
                        true
                    } else {
                        false
                    }
                } else {
                    false
                }
            }
            BreakerState::HalfOpen => {
                // If a probe is in flight, deny new calls.
                if let Some(claimed) = inner.probe_claimed_at {
                    // Probe abandoned? Reclaim after open_duration.
                    if now.duration_since(claimed) >= self.cfg.open_duration {
                        inner.probe_claimed_at = Some(now);
                        true
                    } else {
                        false
                    }
                } else {
                    inner.probe_claimed_at = Some(now);
                    true
                }
            }
        }
    }

    /// Record a call result. `ok=true` for success, `false` for any failure.
    /// `rate_limited=true` is treated as a failure for trip purposes but is
    /// also counted separately for metrics.
    pub fn record(&self, ok: bool, rate_limited: bool) {
        let sample = if ok {
            Sample::Success
        } else if rate_limited {
            Sample::RateLimited
        } else {
            Sample::Failure
        };

        let now = Instant::now();
        let mut inner = self.inner.lock();
        inner.prune(now);

        inner.samples.push_back((now, sample));
        match sample {
            Sample::Success => inner.total_success += 1,
            Sample::Failure => inner.total_failure += 1,
            Sample::RateLimited => inner.total_rate_limited += 1,
        }

        // Clear probe claim on any result.
        inner.probe_claimed_at = None;

        // State transitions.
        match inner.state {
            BreakerState::HalfOpen => {
                if matches!(sample, Sample::Success) {
                    inner.state = BreakerState::Closed;
                    inner.opened_at = None;
                    self.is_open_fast.store(false, Ordering::Release);
                    tracing::info!(key = %self.key, "breaker: half_open -> closed");
                } else {
                    // Probe failed; re-open.
                    inner.state = BreakerState::Open;
                    inner.opened_at = Some(now);
                    self.is_open_fast.store(true, Ordering::Release);
                    tracing::warn!(key = %self.key, "breaker: half_open -> open (probe failed)");
                }
            }
            BreakerState::Closed => {
                let trip = inner.samples.len() >= self.cfg.min_samples
                    && inner.error_rate() >= self.cfg.error_rate_threshold;
                if trip {
                    inner.state = BreakerState::Open;
                    inner.opened_at = Some(now);
                    self.is_open_fast.store(true, Ordering::Release);
                    tracing::warn!(
                        key = %self.key,
                        error_rate = inner.error_rate(),
                        "breaker: closed -> open"
                    );
                }
            }
            BreakerState::Open => {
                // Should not happen — record() should only be called after try_claim.
                // Defensive: leave as-is.
            }
        }

        self.generation.fetch_add(1, Ordering::Relaxed);
    }

    /// Get a snapshot of metrics for observability.
    pub fn metrics(&self) -> BreakerMetrics {
        let inner = self.inner.lock();
        let successes = inner
            .samples
            .iter()
            .filter(|(_, s)| matches!(*s, Sample::Success))
            .count();
        let failures = inner
            .samples
            .iter()
            .filter(|(_, s)| matches!(*s, Sample::Failure))
            .count();
        let rate_limited = inner
            .samples
            .iter()
            .filter(|(_, s)| matches!(*s, Sample::RateLimited))
            .count();
        let total = inner.samples.len();
        BreakerMetrics {
            key: self.key.clone(),
            state: inner.state,
            window_samples: total,
            window_successes: successes,
            window_failures: failures,
            window_rate_limited: rate_limited,
            lifetime_success: inner.total_success,
            lifetime_failure: inner.total_failure,
            lifetime_rate_limited: inner.total_rate_limited,
            generation: self.generation.load(Ordering::Relaxed),
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct BreakerMetrics {
    pub key: String,
    pub state: BreakerState,
    pub window_samples: usize,
    pub window_successes: usize,
    pub window_failures: usize,
    pub window_rate_limited: usize,
    pub lifetime_success: u64,
    pub lifetime_failure: u64,
    pub lifetime_rate_limited: u64,
    pub generation: u64,
}

// ---------------------------------------------------------------------------
// Registry: one breaker per (provider, model) or (mcp_server, tool).
// ---------------------------------------------------------------------------

pub struct CircuitBreakerRegistry {
    breakers: Mutex<HashMap<String, Arc<CircuitBreaker>>>,
    default_cfg: BreakerConfig,
}

impl Default for CircuitBreakerRegistry {
    fn default() -> Self {
        Self::new(BreakerConfig::default())
    }
}

impl CircuitBreakerRegistry {
    pub fn new(default_cfg: BreakerConfig) -> Self {
        Self {
            breakers: Mutex::new(HashMap::new()),
            default_cfg,
        }
    }

    /// Get or create a breaker for the given key.
    pub fn get(&self, key: impl Into<String>) -> Arc<CircuitBreaker> {
        let key = key.into();
        let mut breakers = self.breakers.lock();
        if let Some(b) = breakers.get(&key) {
            return b.clone();
        }
        let b = CircuitBreaker::new(key.clone(), self.default_cfg.clone());
        breakers.insert(key, b.clone());
        b
    }

    /// Override config for a specific key. If the breaker already exists,
    /// it is replaced (state is lost — by design, callers should set this
    /// before traffic starts).
    pub fn configure(&self, key: impl Into<String>, cfg: BreakerConfig) -> Arc<CircuitBreaker> {
        let key = key.into();
        let mut breakers = self.breakers.lock();
        let b = CircuitBreaker::new(key.clone(), cfg);
        breakers.insert(key, b.clone());
        b
    }

    pub fn all_metrics(&self) -> Vec<BreakerMetrics> {
        let breakers = self.breakers.lock();
        breakers.values().map(|b| b.metrics()).collect()
    }
}

// ---------------------------------------------------------------------------
// Retry policy: maps HTTP status to a disposition.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RetryDisposition {
    Retryable,
    AuthRefresh,
    Terminal,
}

pub struct RetryPolicy;

impl RetryPolicy {
    /// Provider-side policy: 429 and 5xx are retryable, everything else terminal.
    pub fn server(status: u16) -> RetryDisposition {
        match status {
            429 => RetryDisposition::Retryable,
            500..=599 => RetryDisposition::Retryable,
            401 | 403 => RetryDisposition::AuthRefresh,
            _ => RetryDisposition::Terminal,
        }
    }

    /// Storage-side policy: 4xx (except auth) are terminal-drop, 5xx retryable.
    pub fn client_storage(status: u16) -> RetryDisposition {
        match status {
            400 | 403 | 404 => RetryDisposition::Terminal,
            401 => RetryDisposition::AuthRefresh,
            _ => RetryDisposition::Retryable,
        }
    }
}

// ---------------------------------------------------------------------------
// SystemTime helpers (used by metric export).
// ---------------------------------------------------------------------------

#[allow(dead_code)]
pub fn now_unix_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn breaker_opens_after_threshold() {
        let cfg = BreakerConfig {
            min_samples: 4,
            error_rate_threshold: 0.5,
            window: Duration::from_secs(60),
            open_duration: Duration::from_secs(1),
        };
        let b = CircuitBreaker::new("test", cfg);

        // 2 success, 3 failure = 60% error rate over 5 samples
        for _ in 0..2 {
            b.record(true, false);
        }
        for _ in 0..3 {
            b.record(false, false);
        }

        assert_eq!(b.state(), BreakerState::Open);
        assert!(b.is_open());
    }

    #[test]
    fn breaker_does_not_trip_below_min_samples() {
        let cfg = BreakerConfig {
            min_samples: 10,
            error_rate_threshold: 0.5,
            window: Duration::from_secs(60),
            open_duration: Duration::from_secs(1),
        };
        let b = CircuitBreaker::new("test", cfg);
        for _ in 0..5 {
            b.record(false, false);
        }
        // Only 5 samples, below min_samples of 10.
        assert_eq!(b.state(), BreakerState::Closed);
    }

    #[test]
    fn registry_dedupes_by_key() {
        let r = CircuitBreakerRegistry::default();
        let a = r.get("openai/gpt-4o");
        let b = r.get("openai/gpt-4o");
        assert!(Arc::ptr_eq(&a, &b));
    }
}

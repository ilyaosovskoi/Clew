//! PyO3 bindings for the circuit breaker.

use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use std::sync::Arc;
use std::time::Duration;

use clew_circuit_breaker::{BreakerConfig, BreakerMetrics, BreakerState, CircuitBreaker,
                            CircuitBreakerRegistry, RetryDisposition, RetryPolicy};

#[pyclass(name = "CircuitBreaker", module = "clew_native.circuit_breaker")]
pub struct PyCircuitBreaker {
    inner: Arc<CircuitBreaker>,
}

#[pymethods]
impl PyCircuitBreaker {
    #[getter]
    fn key(&self) -> String {
        self.inner.key().to_string()
    }

    #[getter]
    fn is_open(&self) -> bool {
        self.inner.is_open()
    }

    #[getter]
    fn state(&self) -> String {
        match self.inner.state() {
            BreakerState::Closed => "closed",
            BreakerState::Open => "open",
            BreakerState::HalfOpen => "half_open",
        }
        .to_string()
    }

    /// Try to claim a slot for a new call. Returns True if the call may proceed.
    #[pyo3(name = "try_claim")]
    fn try_claim(&self) -> bool {
        self.inner.try_claim()
    }

    /// Record a call result. `ok=True` for success, `false` for any failure.
    /// `rate_limited=True` is treated as a failure but counted separately.
    #[pyo3(name = "record")]
    fn record(&self, ok: bool, rate_limited: bool) {
        self.inner.record(ok, rate_limited);
    }

    /// Snapshot of current metrics.
    #[pyo3(name = "metrics")]
    fn metrics(&self) -> PyResult<PyObject> {
        let m = self.inner.metrics();
        Python::with_gil(|py| metrics_to_dict(py, &m))
    }
}

#[pyclass(name = "CircuitBreakerRegistry", module = "clew_native.circuit_breaker")]
pub struct PyCircuitBreakerRegistry {
    inner: CircuitBreakerRegistry,
}

#[pymethods]
impl PyCircuitBreakerRegistry {
    #[new]
    #[pyo3(signature = (min_samples=10, error_rate_threshold=0.5, window_secs=60, open_duration_secs=15))]
    fn new(
        min_samples: usize,
        error_rate_threshold: f64,
        window_secs: u64,
        open_duration_secs: u64,
    ) -> Self {
        let cfg = BreakerConfig {
            min_samples,
            error_rate_threshold,
            window: Duration::from_secs(window_secs),
            open_duration: Duration::from_secs(open_duration_secs),
        };
        Self {
            inner: CircuitBreakerRegistry::new(cfg),
        }
    }

    /// Get or create a breaker for the given key.
    #[pyo3(name = "get")]
    fn get(&self, key: &str) -> PyCircuitBreaker {
        PyCircuitBreaker {
            inner: self.inner.get(key),
        }
    }

    /// Get all metrics (one entry per registered breaker).
    #[pyo3(name = "all_metrics")]
    fn all_metrics(&self) -> PyResult<Vec<PyObject>> {
        let ms = self.inner.all_metrics();
        Python::with_gil(|py| {
            ms.iter().map(|m| metrics_to_dict(py, m)).collect()
        })
    }
}

#[pyfunction]
#[pyo3(name = "retry_disposition_server")]
fn py_retry_server(status: u16) -> &'static str {
    match RetryPolicy::server(status) {
        RetryDisposition::Retryable => "retryable",
        RetryDisposition::AuthRefresh => "auth_refresh",
        RetryDisposition::Terminal => "terminal",
    }
}

#[pyfunction]
#[pyo3(name = "retry_disposition_client_storage")]
fn py_retry_client(status: u16) -> &'static str {
    match RetryPolicy::client_storage(status) {
        RetryDisposition::Retryable => "retryable",
        RetryDisposition::AuthRefresh => "auth_refresh",
        RetryDisposition::Terminal => "terminal",
    }
}

fn metrics_to_dict(py: Python<'_>, m: &BreakerMetrics) -> PyResult<PyObject> {
    let d = pyo3::types::PyDict::new_bound(py);
    d.set_item("key", &m.key)?;
    d.set_item("state", m.state.as_str())?;
    d.set_item("window_samples", m.window_samples)?;
    d.set_item("window_successes", m.window_successes)?;
    d.set_item("window_failures", m.window_failures)?;
    d.set_item("window_rate_limited", m.window_rate_limited)?;
    d.set_item("lifetime_success", m.lifetime_success)?;
    d.set_item("lifetime_failure", m.lifetime_failure)?;
    d.set_item("lifetime_rate_limited", m.lifetime_rate_limited)?;
    d.set_item("generation", m.generation)?;
    Ok(d.into())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCircuitBreaker>()?;
    m.add_class::<PyCircuitBreakerRegistry>()?;
    m.add_function(wrap_pyfunction!(py_retry_server, m)?)?;
    m.add_function(wrap_pyfunction!(py_retry_client, m)?)?;
    Ok(())
}

#[allow(dead_code)]
fn _unused(_: PyRuntimeError) {}

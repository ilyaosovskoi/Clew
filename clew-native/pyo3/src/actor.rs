//! PyO3 bindings for the actor module — CancelToken and helpers.

use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use std::sync::Arc;

use clew_actor::CancelToken;

#[pyclass(name = "CancelToken", module = "clew_native.actor")]
pub struct PyCancelToken {
    inner: CancelToken,
}

#[pymethods]
impl PyCancelToken {
    #[new]
    fn new() -> Self {
        Self {
            inner: CancelToken::new(),
        }
    }

    #[pyo3(name = "is_cancelled")]
    fn is_cancelled(&self) -> bool {
        self.inner.is_cancelled()
    }

    #[pyo3(name = "cancel")]
    #[pyo3(signature = (reason=""))]
    fn cancel(&self, reason: &str) {
        self.inner.cancel(reason.to_string());
    }

    #[getter]
    fn reason(&self) -> Option<String> {
        self.inner.reason()
    }

    /// Spawn a child token that is automatically cancelled when this token is cancelled.
    /// Returns immediately; the cancellation propagation is async.
    #[pyo3(name = "child")]
    fn child(&self) -> PyResult<PyCancelToken> {
        // We need a tokio runtime to spawn the listener task.
        let rt = crate::runtime();
        let _guard = rt.enter();
        let child = self.inner.child();
        Ok(PyCancelToken { inner: child })
    }
}

impl Default for PyCancelToken {
    fn default() -> Self {
        Self::new()
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCancelToken>()?;
    Ok(())
}

#[allow(dead_code)]
fn _unused(_: PyRuntimeError, _: Arc<()>) {}

//! # clew-native
//!
//! PyO3 bindings: exposes Rust subsystems to Python as the `clew_native` extension module.
//!
//! Submodules:
//! - `clew_native.sandbox` — process sandbox
//! - `clew_native.circuit_breaker` — provider call circuit breaker
//! - `clew_native.interjection` — mid-turn user interjection buffer
//! - `clew_native.compaction` — three-tier compaction engine
//! - `clew_native.actor` — CancelToken, Mailbox
//!
//! All functions are designed to **fail gracefully** — if Rust fails, the
//! Python caller falls back to a pure-Python implementation (see
//! `clew.agent.native`).

use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use std::sync::Arc;

// Re-export the bindings submodules.
mod sandbox;
mod circuit_breaker;
mod interjection;
mod compaction;
mod actor;

/// Bootstrap: ensure a tokio runtime is available for the lifetime of the
/// process. Multiple Python calls into Rust will reuse this runtime.
static RUNTIME: once_cell::sync::OnceCell<tokio::runtime::Runtime> = once_cell::sync::OnceCell::new();

pub fn runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("failed to build tokio runtime for clew-native")
    })
}

/// Top-level module: `clew_native`.
#[pymodule]
fn clew_native(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Initialize logging (best-effort).
    let _ = tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .try_init();

    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    // Register submodules.
    let sandbox_mod = PyModule::new_bound(py, "sandbox")?;
    sandbox::register(&sandbox_mod)?;
    m.add_submodule(&sandbox_mod)?;

    let breaker_mod = PyModule::new_bound(py, "circuit_breaker")?;
    circuit_breaker::register(&breaker_mod)?;
    m.add_submodule(&breaker_mod)?;

    let interjection_mod = PyModule::new_bound(py, "interjection")?;
    interjection::register(&interjection_mod)?;
    m.add_submodule(&interjection_mod)?;

    let compaction_mod = PyModule::new_bound(py, "compaction")?;
    compaction::register(&compaction_mod)?;
    m.add_submodule(&compaction_mod)?;

    let actor_mod = PyModule::new_bound(py, "actor")?;
    actor::register(&actor_mod)?;
    m.add_submodule(&actor_mod)?;

    Ok(())
}

/// Convenience: convert a Rust error into a Python RuntimeError.
pub fn err<E: std::fmt::Display>(e: E) -> PyErr {
    PyRuntimeError::new_err(e.to_string())
}

// Re-export the tracing init dependency for the bootstrap macro.
mod tracing_subscriber {
    pub use ::tracing_subscriber::*;
}

//! PyO3 bindings for the sandbox module.

use pyo3::prelude::*;
use pyo3::types::PyList;
use std::path::PathBuf;

use clew_sandbox::{apply_profile, current_profile, describe_state, path_would_be_writable,
                    SandboxConfig, SandboxError, SandboxProfile};

#[pyfunction]
#[pyo3(name = "apply_profile")]
fn py_apply_profile(
    profile: &str,
    workspace_root: Option<&str>,
    allowed_egress: Option<Vec<String>>,
    extra_readonly_paths: Option<Vec<String>>,
    extra_readwrite_paths: Option<Vec<String>>,
) -> PyResult<()> {
    let cfg = SandboxConfig {
        profile: SandboxProfile::from_str_lossy(profile),
        workspace_root: workspace_root.map(PathBuf::from),
        allowed_egress: allowed_egress.unwrap_or_default(),
        extra_readonly_paths: extra_readonly_paths
            .unwrap_or_default()
            .into_iter()
            .map(PathBuf::from)
            .collect(),
        extra_readwrite_paths: extra_readwrite_paths
            .unwrap_or_default()
            .into_iter()
            .map(PathBuf::from)
            .collect(),
    };
    apply_profile(&cfg).map_err(crate::err)
}

#[pyfunction]
#[pyo3(name = "current_profile")]
fn py_current_profile() -> Option<String> {
    current_profile().map(|p| p.as_str().to_string())
}

#[pyfunction]
#[pyo3(name = "describe_state")]
fn py_describe_state() -> String {
    describe_state()
}

#[pyfunction]
#[pyo3(name = "path_would_be_writable")]
fn py_path_would_be_writable(
    profile: &str,
    workspace_root: Option<&str>,
    path: &str,
    extra_readwrite_paths: Option<Vec<String>>,
) -> PyResult<bool> {
    let cfg = SandboxConfig {
        profile: SandboxProfile::from_str_lossy(profile),
        workspace_root: workspace_root.map(PathBuf::from),
        allowed_egress: Vec::new(),
        extra_readonly_paths: Vec::new(),
        extra_readwrite_paths: extra_readwrite_paths
            .unwrap_or_default()
            .into_iter()
            .map(PathBuf::from)
            .collect(),
    };
    Ok(path_would_be_writable(&cfg, std::path::Path::new(path)))
}

#[pyfunction]
#[pyo3(name = "supported_platform")]
fn py_supported_platform() -> bool {
    cfg!(any(target_os = "linux", target_os = "macos"))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_apply_profile, m)?)?;
    m.add_function(wrap_pyfunction!(py_current_profile, m)?)?;
    m.add_function(wrap_pyfunction!(py_describe_state, m)?)?;
    m.add_function(wrap_pyfunction!(py_path_would_be_writable, m)?)?;
    m.add_function(wrap_pyfunction!(py_supported_platform, m)?)?;
    // Expose profiles as constants.
    m.add("PROFILE_OFF", "off")?;
    m.add("PROFILE_WORKSPACE", "workspace")?;
    m.add("PROFILE_READ_ONLY", "read-only")?;
    m.add("PROFILE_STRICT", "strict")?;
    Ok(())
}

// Suppress unused-import warnings; some of these are used only via doc-links.
#[allow(dead_code)]
fn _unused(_: SandboxError, _: &Bound<'_, PyList>) {}

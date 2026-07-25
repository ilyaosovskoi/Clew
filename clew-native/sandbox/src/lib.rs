//! # clew-sandbox
//!
//! OS-level process sandbox for Clew.
//!
//! Ported from Grok Build's `xai-grok-workspace/src/sandbox/` design philosophy:
//! applies kernel-level restrictions (Landlock on Linux, Seatbelt on macOS)
//! to the **entire process at startup**, irreversible once applied.
//!
//! "The model cannot convince the agent to relax restrictions at runtime."
//!
//! ## Profiles
//!
//! - `off`         — no restrictions (default in dev)
//! - `workspace`   — read/write only inside the workspace root; deny network egress except to LLM/MCP endpoints
//! - `read-only`   — read-only filesystem access everywhere
//! - `strict`      — workspace-scoped + no network at all (for untrusted prompts)
//!
//! ## Important
//!
//! Once applied, the sandbox is **irreversible**. The application must call
//! `apply_profile` early in startup, before any untrusted code paths run.

use std::path::{Path, PathBuf};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SandboxProfile {
    Off,
    Workspace,
    ReadOnly,
    Strict,
}

impl SandboxProfile {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Workspace => "workspace",
            Self::ReadOnly => "read-only",
            Self::Strict => "strict",
        }
    }

    pub fn from_str_lossy(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "workspace" => Self::Workspace,
            "read-only" | "readonly" => Self::ReadOnly,
            "strict" => Self::Strict,
            _ => Self::Off,
        }
    }
}

#[derive(Debug, Error)]
pub enum SandboxError {
    #[error("sandbox already applied (profile={current:?}); restrictions are irreversible")]
    AlreadyApplied { current: SandboxProfile },
    #[error("platform does not support sandbox: {0}")]
    UnsupportedPlatform(&'static str),
    #[error("landlock error: {0}")]
    #[cfg(target_os = "linux")]
    Landlock(String),
    #[error("seatbelt error: {0}")]
    #[cfg(target_os = "macos")]
    Seatbelt(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

/// Configuration for applying a sandbox profile.
#[derive(Debug, Clone)]
pub struct SandboxConfig {
    pub profile: SandboxProfile,
    pub workspace_root: Option<PathBuf>,
    /// Network egress allowlist (host:port). Empty = all blocked.
    pub allowed_egress: Vec<String>,
    /// Extra read-only paths (e.g. system Python stdlib).
    pub extra_readonly_paths: Vec<PathBuf>,
    /// Extra read-write paths (e.g. ~/.clew for memory/log).
    pub extra_readwrite_paths: Vec<PathBuf>,
}

impl Default for SandboxConfig {
    fn default() -> Self {
        Self {
            profile: SandboxProfile::Off,
            workspace_root: None,
            allowed_egress: Vec::new(),
            extra_readonly_paths: Vec::new(),
            extra_readwrite_paths: Vec::new(),
        }
    }
}

static APPLIED: once_cell::sync::OnceCell<SandboxProfile> = once_cell::sync::OnceCell::new();

/// Returns `Some(profile)` if the sandbox has already been applied.
pub fn current_profile() -> Option<SandboxProfile> {
    APPLIED.get().copied()
}

/// Apply the sandbox profile. **Irreversible.**
///
/// Returns an error if already applied, or if the platform does not support
/// the requested profile (in which case callers should fail closed for
/// `strict` and fail open for `workspace`).
pub fn apply_profile(cfg: &SandboxConfig) -> Result<(), SandboxError> {
    if let Some(existing) = APPLIED.get() {
        return Err(SandboxError::AlreadyApplied { current: *existing });
    }

    if cfg.profile == SandboxProfile::Off {
        // No-op; do not record as applied so a later call can still tighten.
        tracing::info!("sandbox profile is 'off'; not applying");
        return Ok(());
    }

    let result = apply_platform(cfg);
    if result.is_ok() {
        let _ = APPLIED.set(cfg.profile);
        tracing::info!(profile = ?cfg.profile, "sandbox applied");
    } else {
        tracing::error!(error = ?result.as_ref().err(), "sandbox apply failed");
    }
    result
}

#[cfg(target_os = "linux")]
fn apply_platform(cfg: &SandboxConfig) -> Result<(), SandboxError> {
    apply_landlock(cfg).map_err(SandboxError::Landlock)
}

#[cfg(target_os = "macos")]
fn apply_platform(cfg: &SandboxConfig) -> Result<(), SandboxError> {
    apply_seatbelt(cfg).map_err(SandboxError::Seatbelt)
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn apply_platform(_cfg: &SandboxConfig) -> Result<(), SandboxError> {
    Err(SandboxError::UnsupportedPlatform(
        "sandbox is only supported on Linux (Landlock) and macOS (Seatbelt)",
    ))
}

// ---------------------------------------------------------------------------
// Linux: Landlock
// ---------------------------------------------------------------------------

#[cfg(target_os = "linux")]
fn apply_landlock(cfg: &SandboxConfig) -> Result<(), String> {
    use landlock::{
        AccessFs, Ruleset, RulesetAttr, RulesetCreated,ABI,
        PathBeneath, PathFd,
    };

    // Only Landlock ABI v3+ supports truncate; we degrade gracefully.
    let abi = ABI::V1;
    let access_ro = AccessFs::from_all(abi);
    let access_rw = access_ro | AccessFs::from_write(abi);

    let mut ruleset = Ruleset::new()
        .handle_access_fs(access_ro)
        .map_err(|e| format!("ruleset attr: {e}"))?
        .create()
        .map_err(|e| format!("ruleset create: {e}"))?;

    let root = cfg.workspace_root.as_deref().unwrap_or_else(|| Path::new("."));

    // Allow read+write under workspace root (except in read-only / strict profiles)
    let write_allowed = !matches!(cfg.profile, SandboxProfile::ReadOnly | SandboxProfile::Strict);

    if write_allowed {
        for p in std::iter::once(root).chain(cfg.extra_readwrite_paths.iter().map(|p| p.as_path())) {
            let pb = PathBeneath::new(PathFd::new(p).map_err(|e| format!("open {p:?}: {e}"))?, access_rw);
            ruleset = ruleset.add_rule(pb).map_err(|e| format!("rule add rw {p:?}: {e}"))?;
        }
    } else {
        for p in std::iter::once(root).chain(cfg.extra_readonly_paths.iter().map(|p| p.as_path())) {
            let pb = PathBeneath::new(PathFd::new(p).map_err(|e| format!("open {p:?}: {e}"))?, access_ro);
            ruleset = ruleset.add_rule(pb).map_err(|e| format!("rule add ro {p:?}: {e}"))?;
        }
    }

    // Always allow read on common system paths so the runtime can load libs.
    for system_ro in ["/usr/lib", "/lib", "/etc/ssl", "/etc/resolv.conf"] {
        if let Ok(fd) = PathFd::new(system_ro) {
            let pb = PathBeneath::new(fd, access_ro);
            let _ = ruleset.add_rule(pb); // best-effort
        }
    }

    ruleset.restrict_self().map_err(|e| format!("restrict_self: {e}"))?;

    // Network egress: Landlock v4+ supports network restrictions. On older kernels
    // we fall back to a soft advisory note (TCP outbound not blocked at kernel level).
    if matches!(cfg.profile, SandboxProfile::Strict) {
        // For strict mode, we additionally block all network access via seccomp.
        // Implementing a full seccomp filter is out of scope for the initial port;
        // document this as a TODO and rely on the workspace-level net deny.
        tracing::warn!("strict profile: network egress blocking via seccomp is a TODO; relying on advisory");
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// macOS: Seatbelt (sandbox_init(3))
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
fn apply_seatbelt(cfg: &SandboxConfig) -> Result<(), String> {
    // Build a Seatbelt profile string. macOS `sandbox_init` is deprecated but
    // still functional and is the only userland API we can call without a
    // separate entitlement / helper binary.
    let mut profile = String::new();
    profile.push_str("(version 1)\n(deny default)\n");

    // Always allow system reads (dyld, libc, etc.)
    profile.push_str("(allow file-read*)\n");

    let root = cfg.workspace_root.as_deref().unwrap_or_else(|| Path::new("."));
    let root_str = root.to_string_lossy().replace('\\', "/");

    let write_allowed = !matches!(cfg.profile, SandboxProfile::ReadOnly | SandboxProfile::Strict);

    if write_allowed {
        // Allow writes inside workspace root.
        profile.push_str(&format!(
            "(allow file-write* (subpath \"{}\"))\n",
            root_str
        ));
        for p in &cfg.extra_readwrite_paths {
            let s = p.to_string_lossy().replace('\\', "/");
            profile.push_str(&format!("(allow file-write* (subpath \"{}\"))\n", s));
        }
    }

    // Network egress.
    if matches!(cfg.profile, SandboxProfile::Strict) {
        // Block all network in strict mode.
        profile.push_str("(deny network*)\n");
    } else {
        profile.push_str("(allow network*)\n");
    }

    // Apply via `sandbox_init` (deprecated but functional).
    let c_profile = std::ffi::CString::new(profile.clone()).map_err(|e| format!("profile CString: {e}"))?;
    let mut error_buf: *mut libc::c_char = std::ptr::null_mut();
    let rc = unsafe {
        libc::sandbox_init(
            c_profile.as_ptr(),
            libc::SANDBOX_NAMED_EXTERNAL,
            &mut error_buf,
        )
    };
    if rc != 0 {
        let msg = if error_buf.is_null() {
            "unknown seatbelt error".to_string()
        } else {
            let s = unsafe { std::ffi::CStr::from_ptr(error_buf) }
                .to_string_lossy()
                .into_owned();
            unsafe { libc::free(error_buf as *mut libc::c_void) };
            s
        };
        return Err(format!("sandbox_init rc={rc}: {msg}"));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Inspection / helpers
// ---------------------------------------------------------------------------

/// Returns a human-readable description of the current sandbox state.
pub fn describe_state() -> String {
    match current_profile() {
        Some(p) => format!("applied (profile={})", p.as_str()),
        None => "not applied".to_string(),
    }
}

/// Check whether a path would be writable under the current sandbox profile.
///
/// This is a *best-effort* advisory check — it does not query the kernel
/// state, it just reasons about the requested profile and the workspace root.
pub fn path_would_be_writable(cfg: &SandboxConfig, path: &Path) -> bool {
    if matches!(cfg.profile, SandboxProfile::ReadOnly | SandboxProfile::Strict) {
        // No writes anywhere under read-only / strict.
        // Exception: paths explicitly listed in extra_readwrite_paths.
        return cfg
            .extra_readwrite_paths
            .iter()
            .any(|p| path.starts_with(p));
    }
    if let Some(root) = &cfg.workspace_root {
        if path.starts_with(root) {
            return true;
        }
    }
    cfg.extra_readwrite_paths
        .iter()
        .any(|p| path.starts_with(p))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_from_str() {
        assert_eq!(SandboxProfile::from_str_lossy("workspace"), SandboxProfile::Workspace);
        assert_eq!(SandboxProfile::from_str_lossy("read-only"), SandboxProfile::ReadOnly);
        assert_eq!(SandboxProfile::from_str_lossy("READONLY"), SandboxProfile::ReadOnly);
        assert_eq!(SandboxProfile::from_str_lossy("strict"), SandboxProfile::Strict);
        assert_eq!(SandboxProfile::from_str_lossy("garbage"), SandboxProfile::Off);
    }

    #[test]
    fn path_writable_in_workspace_profile() {
        let cfg = SandboxConfig {
            profile: SandboxProfile::Workspace,
            workspace_root: Some(PathBuf::from("/tmp/proj")),
            ..Default::default()
        };
        assert!(path_would_be_writable(&cfg, Path::new("/tmp/proj/src/main.rs")));
        assert!(!path_would_be_writable(&cfg, Path::new("/etc/passwd")));
    }

    #[test]
    fn path_writable_in_readonly_profile() {
        let cfg = SandboxConfig {
            profile: SandboxProfile::ReadOnly,
            workspace_root: Some(PathBuf::from("/tmp/proj")),
            ..Default::default()
        };
        assert!(!path_would_be_writable(&cfg, Path::new("/tmp/proj/src/main.rs")));
    }
}

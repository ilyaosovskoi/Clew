# clew-native — Rust workspace for Clew v2.0

Native Rust acceleration for Clew. Exposes kernel-level sandbox, circuit
breaker, interjection buffer, three-tier compaction engine, and cancel-token
actor helpers to Python via PyO3.

## Workspace members

| Crate | Purpose |
|---|---|
| `sandbox` | OS-level process sandbox (Landlock on Linux, Seatbelt on macOS). Irreversible. |
| `circuit_breaker` | Sliding-window circuit breaker per (provider, model) key. Lock-free fast path. |
| `interjection` | Mid-turn user interjection buffer with UTF-8-safe truncation. |
| `compaction` | Three-tier compaction engine (code/intra/inter). Transport-agnostic. |
| `actor` | CancelToken (AbortSignal-pattern) and Mailbox helpers. |
| `pyo3` | PyO3 bindings — produces the `clew_native` cdylib Python extension. |

## Building

```bash
# Install maturin (Python side)
pip install maturin

# Build & install the extension in development mode
cd clew-native
maturin develop --release -m pyo3/Cargo.toml

# Verify
python -c "from clew.agent.native import NATIVE_AVAILABLE; print('native:', NATIVE_AVAILABLE)"
# Should print: native: True
```

For a production build (optimized, stripped):
```bash
maturin build --release -m pyo3/Cargo.toml
# produces a wheel in target/wheels/
pip install target/wheels/clew_native-*.whl
```

## Testing

Each crate has unit tests. Run them all:
```bash
cd clew-native
cargo test --workspace
```

Or per-crate:
```bash
cargo test -p clew-circuit-breaker
cargo test -p clew-interjection
cargo test -p clew-compaction
cargo test -p clew-sandbox
cargo test -p clew-actor
```

## Platform support

| Subsystem | Linux | macOS | Windows |
|---|---|---|---|
| `sandbox` | Landlock (kernel ≥5.13) | Seatbelt | Not supported (fails closed for `strict`, open for `workspace`) |
| `circuit_breaker` | ✓ | ✓ | ✓ |
| `interjection` | ✓ | ✓ | ✓ |
| `compaction` | ✓ | ✓ | ✓ |
| `actor` | ✓ | ✓ | ✓ |

## Design notes

- **No `unsafe`** in user-facing code (only in PyO3 boilerplate).
- **`panic = "abort"`** in dev and release profiles. A panic in native
  code terminates the process; we don't try to unwind across the FFI
  boundary (which is UB).
- **`jemalloc`** as the global allocator in release builds for
  predictable performance under load.
- **`tokio` multi-threaded runtime** is created lazily on first use
  (see `pyo3/src/lib.rs::runtime()`), so importing `clew_native` doesn't
  spawn worker threads until needed.

## Adding a new native subsystem

1. Create a new crate under `clew-native/<name>/` with `Cargo.toml` and `src/lib.rs`.
2. Add it to `[workspace] members` in `clew-native/Cargo.toml`.
3. Add it as a dependency of `clew-native/pyo3/Cargo.toml`.
4. Create `clew-native/pyo3/src/<name>.rs` with PyO3 bindings.
5. Register the submodule in `clew-native/pyo3/src/lib.rs`:
   ```rust
   mod <name>;
   // ...
   let <name>_mod = PyModule::new_bound(py, "<name>")?;
   <name>::register(&<name>_mod)?;
   m.add_submodule(&<name>_mod)?;
   ```
6. Create a Python wrapper in `clew/agent/<name>.py` that loads via `clew.agent.native`.
7. Create a pure-Python fallback in `clew/agent/_fallback_<name>.py`.
8. Update `clew/agent/native.py` to expose `get_<name>()`.
9. Update `clew/agent/__init__.py` to export the public API.

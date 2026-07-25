//! PyO3 bindings for the interjection buffer.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use clew_interjection::{InterjectionBuffer, LARGE_PROMPT_THRESHOLD};

#[pyclass(name = "InterjectionBuffer", module = "clew_native.interjection")]
pub struct PyInterjectionBuffer {
    inner: InterjectionBuffer,
}

#[pymethods]
impl PyInterjectionBuffer {
    #[new]
    fn new() -> Self {
        Self {
            inner: InterjectionBuffer::new(),
        }
    }

    /// Push a new interjection. Returns its assigned id.
    #[pyo3(name = "push")]
    #[pyo3(signature = (text, attachment=None))]
    fn push(&self, text: &str, attachment: Option<String>) -> u64 {
        self.inner.push_with_attachment(text, attachment)
    }

    /// Drain all pending interjections, returning a list of dicts.
    #[pyo3(name = "drain")]
    fn drain(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        let entries = self.inner.drain();
        entries
            .into_iter()
            .map(|e| {
                let d = PyDict::new_bound(py);
                d.set_item("id", e.id)?;
                d.set_item("received_at_unix_millis", e.received_at_unix_millis)?;
                d.set_item("raw_text", e.raw_text)?;
                d.set_item("truncated", e.truncated)?;
                d.set_item("attachment", e.attachment)?;
                Ok(d.into())
            })
            .collect()
    }

    /// Drain all pending interjections as a single formatted message body.
    /// Returns None if the buffer is empty.
    #[pyo3(name = "drain_formatted")]
    fn drain_formatted(&self) -> Option<String> {
        self.inner.drain_formatted()
    }

    /// Peek count without draining.
    #[pyo3(name = "pending_count")]
    fn pending_count(&self) -> usize {
        self.inner.pending_count()
    }
}

#[pyfunction]
#[pyo3(name = "render_entry")]
fn py_render_entry(text: &str, truncated: bool) -> String {
    let entry = clew_interjection::InterjectionEntry {
        id: 0,
        received_at_unix_millis: 0,
        raw_text: text.to_string(),
        truncated,
        attachment: None,
    };
    entry.render()
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyInterjectionBuffer>()?;
    m.add_function(wrap_pyfunction!(py_render_entry, m)?)?;
    m.add("LARGE_PROMPT_THRESHOLD", LARGE_PROMPT_THRESHOLD)?;
    Ok(())
}

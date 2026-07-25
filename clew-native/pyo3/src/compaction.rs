//! PyO3 bindings for the compaction engine.

use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use pyo3::types::{PyDict, PyList};
use std::sync::Arc;

use clew_compaction::{
    CompactionEngine, CompactionError, CompactionObserver, CompactionPolicy,
    CompactionStrategy, ConversationItem, DefaultTokenCounter, ItemTokenCounter,
};

#[pyclass(name = "ConversationItem", module = "clew_native.compaction")]
#[derive(Clone)]
pub struct PyConversationItem {
    pub inner: ConversationItem,
}

#[pymethods]
impl PyConversationItem {
    #[new]
    #[pyo3(signature = (role, content, tokens=0))]
    fn new(role: &str, content: &str, tokens: usize) -> PyResult<Self> {
        let item = match role {
            "user" => ConversationItem::User {
                content: content.to_string(),
                tokens,
            },
            "assistant" => ConversationItem::Assistant {
                content: content.to_string(),
                tokens,
                tool_calls: Vec::new(),
            },
            "tool" => ConversationItem::Tool {
                tool_call_id: String::new(),
                content: content.to_string(),
                tokens,
            },
            "system" => ConversationItem::System {
                content: content.to_string(),
                tokens,
            },
            _ => return Err(PyRuntimeError::new_err(format!("unknown role: {}", role))),
        };
        Ok(Self { inner: item })
    }

    #[getter]
    fn role(&self) -> &'static str {
        self.inner.role_str()
    }

    #[getter]
    fn tokens(&self) -> usize {
        self.inner.tokens()
    }

    #[getter]
    fn content(&self) -> String {
        match &self.inner {
            ConversationItem::User { content, .. } => content.clone(),
            ConversationItem::Assistant { content, .. } => content.clone(),
            ConversationItem::Tool { content, .. } => content.clone(),
            ConversationItem::System { content, .. } => content.clone(),
        }
    }
}

#[pyclass(name = "CompactionPolicy", module = "clew_native.compaction")]
pub struct PyCompactionPolicy {
    pub inner: CompactionPolicy,
}

#[pymethods]
impl PyCompactionPolicy {
    #[staticmethod]
    #[pyo3(name = "code")]
    fn code() -> Self {
        Self {
            inner: CompactionPolicy {
                strategy: CompactionStrategy::Code,
                ..Default::default()
            },
        }
    }

    #[staticmethod]
    #[pyo3(name = "intra")]
    #[pyo3(signature = (keep_recent=6))]
    fn intra(keep_recent: usize) -> Self {
        Self {
            inner: CompactionPolicy {
                strategy: CompactionStrategy::Intra,
                keep_recent,
                ..Default::default()
            },
        }
    }

    #[staticmethod]
    #[pyo3(name = "inter")]
    #[pyo3(signature = (keep_recent=6, chunk_size=10))]
    fn inter(keep_recent: usize, chunk_size: usize) -> Self {
        let _ = chunk_size;
        Self {
            inner: CompactionPolicy {
                strategy: CompactionStrategy::Inter,
                keep_recent,
                ..Default::default()
            },
        }
    }
}

/// Python-side sampler callback. The host provides a callable that takes
/// (prompt, list_of_items) and returns a summary string.
pub struct PySampler {
    callback: PyObject,
}

impl PySampler {
    pub fn new(callback: PyObject) -> Self {
        Self { callback }
    }
}

impl ItemTokenCounter for DefaultTokenCounter {
    // Already implemented in the compaction crate; this is a re-export marker.
}

impl clew_compaction::CompactionSampler for PySampler {
    fn summarize(
        &self,
        prompt: &str,
        items: &[&ConversationItem],
    ) -> Result<String, CompactionError> {
        Python::with_gil(|py| {
            let py_items = PyList::new_bound(py, items.iter().map(|it| {
                let d = PyDict::new_bound(py);
                let _ = d.set_item("role", it.role_str());
                let content = match it {
                    ConversationItem::User { content, .. } => content,
                    ConversationItem::Assistant { content, .. } => content,
                    ConversationItem::Tool { content, .. } => content,
                    ConversationItem::System { content, .. } => content,
                };
                let _ = d.set_item("content", content);
                d.into()
            }));
            let result = self
                .callback
                .call1(py, (prompt, py_items))
                .map_err(|e| CompactionError::Sampler(format!("Python sampler raised: {}", e)))?;
            let s: String = result
                .extract::<String>(py)
                .map_err(|e| CompactionError::Sampler(format!("sampler did not return str: {}", e)))?;
            Ok(s)
        })
    }
}

#[pyclass(name = "CompactionEngine", module = "clew_native.compaction")]
pub struct PyCompactionEngine {
    sampler: Arc<PySampler>,
}

#[pymethods]
impl PyCompactionEngine {
    #[new]
    fn new(sampler: PyObject) -> Self {
        Self {
            sampler: Arc::new(PySampler::new(sampler)),
        }
    }

    /// Run code_compaction: full-replace. Returns (summary, fresh_items).
    #[pyo3(name = "code_compact")]
    fn code_compact(&self, py: Python<'_>, items: Vec<PyConversationItem>) -> PyResult<(String, Vec<PyConversationItem>)> {
        let counter = DefaultTokenCounter;
        let engine = CompactionEngine::new(&counter, self.sampler.as_ref());
        let raw_items: Vec<ConversationItem> = items.iter().map(|i| i.inner.clone()).collect();
        let (summary, fresh) = engine
            .code_compact(&raw_items)
            .map_err(crate::err)?;
        let py_fresh: Vec<PyConversationItem> = fresh
            .into_iter()
            .map(|i| PyConversationItem { inner: i })
            .collect();
        Ok((summary, py_fresh))
    }

    /// Run intra_compaction: tail-keep.
    #[pyo3(name = "intra_compact")]
    fn intra_compact(&self, items: Vec<PyConversationItem>, keep_recent: usize) -> PyResult<(String, Vec<PyConversationItem>)> {
        let counter = DefaultTokenCounter;
        let engine = CompactionEngine::new(&counter, self.sampler.as_ref());
        let raw_items: Vec<ConversationItem> = items.iter().map(|i| i.inner.clone()).collect();
        let (summary, fresh) = engine
            .intra_compact(&raw_items, keep_recent)
            .map_err(crate::err)?;
        let py_fresh: Vec<PyConversationItem> = fresh
            .into_iter()
            .map(|i| PyConversationItem { inner: i })
            .collect();
        Ok((summary, py_fresh))
    }

    /// Run inter_compaction: chunked, between-turn.
    #[pyo3(name = "inter_compact")]
    fn inter_compact(&self, items: Vec<PyConversationItem>, chunk_size: usize, keep_recent: usize) -> PyResult<(String, Vec<PyConversationItem>)> {
        let counter = DefaultTokenCounter;
        let engine = CompactionEngine::new(&counter, self.sampler.as_ref());
        let raw_items: Vec<ConversationItem> = items.iter().map(|i| i.inner.clone()).collect();
        let (summary, fresh) = engine
            .inter_compact(&raw_items, chunk_size, keep_recent)
            .map_err(crate::err)?;
        let py_fresh: Vec<PyConversationItem> = fresh
            .into_iter()
            .map(|i| PyConversationItem { inner: i })
            .collect();
        Ok((summary, py_fresh))
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyConversationItem>()?;
    m.add_class::<PyCompactionPolicy>()?;
    m.add_class::<PyCompactionEngine>()?;
    Ok(())
}

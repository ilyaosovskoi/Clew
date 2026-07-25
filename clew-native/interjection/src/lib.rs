//! # clew-interjection
//!
//! Mid-turn user interjection buffer. Ported from Grok Build's
//! `xai-interjection-core` design:
//!
//! - User can queue a message while a turn is in flight.
//! - Messages are buffered (FIFO) and drained at safe points.
//! - Each drained entry is framed as a synthetic user message.
//! - Truncation is UTF-8-safe at the boundary.
//! - One message per entry, never merged.
//! - "The model decides how to weigh it against in-flight work."
//!
//! Example framing:
//! ```text
//! The user sent a message while you were working:
//! <user_query>
//! please also consider edge case X
//! </user_query>
//! ```

use parking_lot::Mutex;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

/// Maximum size of an interjection body before truncation kicks in.
pub const LARGE_PROMPT_THRESHOLD: usize = 25_000;

#[derive(Debug, Clone)]
pub struct PendingInterjection {
    pub id: u64,
    pub received_at_unix_millis: u64,
    pub text: String,
    /// Optional attachment (image URL, file path, etc.) — opaque to the buffer.
    pub attachment: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct InterjectionEntry {
    pub id: u64,
    pub received_at_unix_millis: u64,
    pub raw_text: String,
    pub truncated: bool,
    pub attachment: Option<String>,
}

impl InterjectionEntry {
    /// Render the entry as a synthetic user message body.
    pub fn render(&self) -> String {
        let body = if self.truncated {
            format!(
                "{}\n\n[truncated — original was {} chars]",
                &self.raw_text,
                self.raw_text.len()
            )
        } else {
            self.raw_text.clone()
        };
        format!(
            "The user sent a message while you were working:\n<user_query>\n{}\n</user_query>",
            body
        )
    }
}

/// Lock-free-ish queue: push from any thread, drain from the agent loop.
#[derive(Clone)]
pub struct InterjectionBuffer {
    inner: Arc<Mutex<VecDeque<PendingInterjection>>>,
    next_id: Arc<AtomicU64>,
}

impl Default for InterjectionBuffer {
    fn default() -> Self {
        Self::new()
    }
}

impl InterjectionBuffer {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(VecDeque::new())),
            next_id: Arc::new(AtomicU64::new(1)),
        }
    }

    /// Push a new interjection. Returns its assigned id.
    pub fn push(&self, text: impl Into<String>) -> u64 {
        self.push_with_attachment(text, None)
    }

    pub fn push_with_attachment(
        &self,
        text: impl Into<String>,
        attachment: Option<String>,
    ) -> u64 {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let entry = PendingInterjection {
            id,
            received_at_unix_millis: now_unix_millis(),
            text: text.into(),
            attachment,
        };
        let mut inner = self.inner.lock();
        inner.push_back(entry);
        tracing::debug!(interjection_id = id, "interjection queued");
        id
    }

    /// Drain all pending interjections, returning framed entries.
    /// Each entry is truncated UTF-8-safe at LARGE_PROMPT_THRESHOLD.
    pub fn drain(&self) -> Vec<InterjectionEntry> {
        let mut inner = self.inner.lock();
        let drained: Vec<_> = inner.drain(..).collect();
        drop(inner);

        drained
            .into_iter()
            .map(|p| {
                let (raw_text, truncated) = truncate_utf8_safe(&p.text, LARGE_PROMPT_THRESHOLD);
                InterjectionEntry {
                    id: p.id,
                    received_at_unix_millis: p.received_at_unix_millis,
                    raw_text,
                    truncated,
                    attachment: p.attachment,
                }
            })
            .collect()
    }

    /// Peek without draining.
    pub fn pending_count(&self) -> usize {
        self.inner.lock().len()
    }

    /// Render all pending interjections as a single synthetic message block.
    /// Returns `None` if there are no pending entries.
    pub fn drain_formatted(&self) -> Option<String> {
        let entries = self.drain();
        if entries.is_empty() {
            return None;
        }
        let mut out = String::new();
        for entry in entries {
            out.push_str(&entry.render());
            out.push_str("\n\n");
        }
        // Strip trailing whitespace.
        let trimmed = out.trim_end().to_string();
        Some(trimmed)
    }
}

/// Truncate `s` to `max_chars` but never split a multi-byte UTF-8 sequence.
/// Returns `(truncated_text, was_truncated)`.
pub fn truncate_utf8_safe(s: &str, max_chars: usize) -> (String, bool) {
    if s.chars().count() <= max_chars {
        return (s.to_string(), false);
    }
    let mut end_byte = 0;
    let mut count = 0;
    for (i, _) in s.char_indices() {
        if count >= max_chars {
            end_byte = i;
            break;
        }
        count += 1;
    }
    (s[..end_byte].to_string(), true)
}

fn now_unix_millis() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_and_drain() {
        let buf = InterjectionBuffer::new();
        buf.push("hello");
        buf.push("world");
        let drained = buf.drain();
        assert_eq!(drained.len(), 2);
        assert_eq!(drained[0].raw_text, "hello");
        assert_eq!(drained[1].raw_text, "world");
    }

    #[test]
    fn render_framing() {
        let entry = InterjectionEntry {
            id: 1,
            received_at_unix_millis: 0,
            raw_text: "consider X".to_string(),
            truncated: false,
            attachment: None,
        };
        let rendered = entry.render();
        assert!(rendered.contains("The user sent a message while you were working:"));
        assert!(rendered.contains("<user_query>"));
        assert!(rendered.contains("consider X"));
    }

    #[test]
    fn utf8_safe_truncation() {
        let s = "café résumé".repeat(5000); // plenty of multi-byte chars
        let (truncated, was_truncated) = truncate_utf8_safe(&s, 100);
        assert!(was_truncated);
        // Truncated string must still be valid UTF-8.
        assert!(std::str::from_utf8(truncated.as_bytes()).is_ok());
    }

    #[test]
    fn drain_formatted_combines_entries() {
        let buf = InterjectionBuffer::new();
        buf.push("first");
        buf.push("second");
        let formatted = buf.drain_formatted().unwrap();
        assert!(formatted.contains("first"));
        assert!(formatted.contains("second"));
        // Drain again — should be empty.
        assert!(buf.drain_formatted().is_none());
    }
}

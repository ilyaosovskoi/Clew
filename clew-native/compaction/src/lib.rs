//! # clew-compaction
//!
//! Three-tier conversation compaction engine. Ported from Grok Build's
//! `xai-grok-compaction` crate design:
//!
//! - **code_compaction**: full-session replace — summarize entire history,
//!   rebuild fresh. Used for grok-build style sessions.
//! - **intra_compaction**: tail-keep, per-turn — summarize tool-call history
//!   of the current turn while keeping the tail.
//! - **inter_compaction**: chunked, between-turn summarization pipeline
//!   (shared by Basic and DivideAndConquer strategies).
//!
//! The engine is transport-agnostic — host-specific triggers, transport,
//! and persistence stay in the host. This crate provides only the trait
//! seams and the core logic.

use ahash::AHashMap;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Conversation item model (host-agnostic).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "role", rename_all = "snake_case")]
pub enum ConversationItem {
    User { content: String, tokens: usize },
    Assistant { content: String, tokens: usize, tool_calls: Vec<ToolCallSummary> },
    Tool { tool_call_id: String, content: String, tokens: usize },
    System { content: String, tokens: usize },
}

impl ConversationItem {
    pub fn tokens(&self) -> usize {
        match self {
            Self::User { tokens, .. } => *tokens,
            Self::Assistant { tokens, .. } => *tokens,
            Self::Tool { tokens, .. } => *tokens,
            Self::System { tokens, .. } => *tokens,
        }
    }

    pub fn role_str(&self) -> &'static str {
        match self {
            Self::User { .. } => "user",
            Self::Assistant { .. } => "assistant",
            Self::Tool { .. } => "tool",
            Self::System { .. } => "system",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallSummary {
    pub id: String,
    pub name: String,
    pub args_summary: String,
}

// ---------------------------------------------------------------------------
// Trait seams (host implements these).
// ---------------------------------------------------------------------------

/// Trait the host implements to count tokens for its specific model family.
pub trait ItemTokenCounter: Send + Sync {
    fn count_tokens(&self, content: &str) -> usize;
    fn count_item(&self, item: &ConversationItem) -> usize {
        // Default: trust the .tokens field if non-zero, else re-count content.
        let t = item.tokens();
        if t > 0 {
            t
        } else {
            match item {
                ConversationItem::User { content, .. } => self.count_tokens(content),
                ConversationItem::Assistant { content, .. } => self.count_tokens(content),
                ConversationItem::Tool { content, .. } => self.count_tokens(content),
                ConversationItem::System { content, .. } => self.count_tokens(content),
            }
        }
    }
}

/// Default counter: 1 token ≈ 4 chars.
pub struct DefaultTokenCounter;
impl ItemTokenCounter for DefaultTokenCounter {
    fn count_tokens(&self, content: &str) -> usize {
        (content.len() + 3) / 4
    }
}

/// Trait the host implements to actually run the summarization LLM call.
/// Returns the summary text.
pub trait CompactionSampler: Send + Sync {
    fn summarize(&self, prompt: &str, items: &[&ConversationItem]) -> Result<String, CompactionError>;
}

/// Trait the host implements to observe compaction progress.
pub trait CompactionObserver: Send + Sync {
    fn on_intra_compaction(&self, _summary: &str) {}
    fn on_inter_compaction(&self, _summary: &str) {}
    fn on_code_compaction(&self, _summary: &str) {}
}

// ---------------------------------------------------------------------------
// Errors.
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum CompactionError {
    #[error("sampler error: {0}")]
    Sampler(String),
    #[error("not enough items to compact (got {0}, need {1})")]
    InsufficientItems(usize, usize),
    #[error("would exceed budget: have {have}, need {need}")]
    BudgetExceeded { have: usize, need: usize },
}

// ---------------------------------------------------------------------------
// Compaction policies.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CompactionStrategy {
    /// Full-replace: summarize everything and rebuild fresh history.
    Code,
    /// Tail-keep, per-turn.
    Intra,
    /// Chunked, between-turn.
    Inter,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompactionPolicy {
    pub strategy: CompactionStrategy,
    /// Auto-compact when total tokens exceed this fraction of the context window.
    #[serde(default = "default_auto_compact_threshold")]
    pub auto_compact_threshold_percent: u32,
    /// Wall-clock budget for a single compaction pass.
    #[serde(default = "default_wall_clock_budget")]
    pub wall_clock_budget_secs: u64,
    /// Two-pass: speculatively summarize prefix in background, then summarize
    /// NOTE₁ + recent tail at compaction time.
    #[serde(default)]
    pub two_pass_enabled: bool,
    /// Keep last N messages intact (not summarized).
    #[serde(default = "default_keep_recent")]
    pub keep_recent: usize,
}

fn default_auto_compact_threshold() -> u32 {
    85
}
fn default_wall_clock_budget() -> u64 {
    300
}
fn default_keep_recent() -> usize {
    6
}

impl Default for CompactionPolicy {
    fn default() -> Self {
        Self {
            strategy: CompactionStrategy::Inter,
            auto_compact_threshold_percent: default_auto_compact_threshold(),
            wall_clock_budget_secs: default_wall_clock_budget(),
            two_pass_enabled: false,
            keep_recent: default_keep_recent(),
        }
    }
}

// ---------------------------------------------------------------------------
// Engine.
// ---------------------------------------------------------------------------

pub struct CompactionEngine<'a> {
    pub counter: &'a dyn ItemTokenCounter,
    pub sampler: &'a dyn CompactionSampler,
    pub observer: Option<&'a dyn CompactionObserver>,
}

impl<'a> CompactionEngine<'a> {
    pub fn new(
        counter: &'a dyn ItemTokenCounter,
        sampler: &'a dyn CompactionSampler,
    ) -> Self {
        Self {
            counter,
            sampler,
            observer: None,
        }
    }

    pub fn with_observer(mut self, o: &'a dyn CompactionObserver) -> Self {
        self.observer = Some(o);
        self
    }

    /// Decide whether compaction should fire given the current token usage.
    pub fn should_compact(&self, total_tokens: usize, context_window: usize) -> bool {
        if context_window == 0 {
            return false;
        }
        let threshold = context_window
            * self_auto_compact_threshold_default() as usize
            / 100;
        total_tokens >= threshold
    }

    /// Run code_compaction: full-replace. Returns (summary, fresh_history).
    pub fn code_compact(
        &self,
        items: &[ConversationItem],
    ) -> Result<(String, Vec<ConversationItem>), CompactionError> {
        if items.is_empty() {
            return Err(CompactionError::InsufficientItems(0, 1));
        }
        let prompt = "Summarize the following entire conversation. Preserve:
- the user's original goal
- all files modified (paths + intent)
- key decisions made
- errors encountered and how they were resolved
- the current state of progress

Keep the summary under 1000 words.";
        let refs: Vec<&ConversationItem> = items.iter().collect();
        let summary = self
            .sampler
            .summarize(prompt, &refs)
            .map_err(|e| CompactionError::Sampler(e.to_string()))?;

        if let Some(o) = self.observer {
            o.on_code_compaction(&summary);
        }

        // Fresh history: a single System message with the summary.
        let fresh = vec![ConversationItem::System {
            content: format!("[CONVERSATION SUMMARY]\n{}", summary),
            tokens: self.counter.count_tokens(&summary),
        }];
        Ok((summary, fresh))
    }

    /// Run intra_compaction: summarize tool-call history of the current turn,
    /// keep the tail (last `keep_recent` items).
    pub fn intra_compact(
        &self,
        items: &[ConversationItem],
        keep_recent: usize,
    ) -> Result<(String, Vec<ConversationItem>), CompactionError> {
        if items.len() <= keep_recent {
            return Err(CompactionError::InsufficientItems(
                items.len(),
                keep_recent + 1,
            ));
        }
        let split = items.len() - keep_recent;
        let to_summarize = &items[..split];
        let tail = &items[split..];

        let prompt = "Summarize the tool-call history of the current turn. Preserve:
- Task/Intent
- Key Findings
- Files/Code touched
- Errors/Fixes
- Actions Taken
- Current Progress

If the tool-call history contains a previous compaction summary, you MUST
incorporate ALL information from that previous summary. Use internal thinking
channel. Preserve verbatim data (URLs, file paths, code snippets).";
        let refs: Vec<&ConversationItem> = to_summarize.iter().collect();
        let summary = self
            .sampler
            .summarize(prompt, &refs)
            .map_err(|e| CompactionError::Sampler(e.to_string()))?;

        if let Some(o) = self.observer {
            o.on_intra_compaction(&summary);
        }

        // New history: [summary, ...tail].
        let mut new = Vec::with_capacity(1 + tail.len());
        new.push(ConversationItem::System {
            content: format!("[PREVIOUS TURN SUMMARY]\n{}", summary),
            tokens: self.counter.count_tokens(&summary),
        });
        new.extend_from_slice(tail);
        Ok((summary, new))
    }

    /// Run inter_compaction: chunked, between-turn summarization.
    /// Each chunk of `chunk_size` items is summarized separately, then
    /// summaries are concatenated. The last `keep_recent` items are kept verbatim.
    pub fn inter_compact(
        &self,
        items: &[ConversationItem],
        chunk_size: usize,
        keep_recent: usize,
    ) -> Result<(String, Vec<ConversationItem>), CompactionError> {
        if items.len() <= keep_recent + chunk_size {
            return Err(CompactionError::InsufficientItems(
                items.len(),
                keep_recent + chunk_size + 1,
            ));
        }
        let split = items.len() - keep_recent;
        let to_summarize = &items[..split];
        let tail = &items[split..];

        let mut chunk_summaries: Vec<String> = Vec::new();
        for chunk in to_summarize.chunks(chunk_size) {
            let refs: Vec<&ConversationItem> = chunk.iter().collect();
            let s = self
                .sampler
                .summarize(
                    "Summarize this conversation chunk concisely (under 200 words). Preserve key decisions and file paths.",
                    &refs,
                )
                .map_err(|e| CompactionError::Sampler(e.to_string()))?;
            chunk_summaries.push(s);
        }

        let combined = chunk_summaries.join("\n\n---\n\n");

        if let Some(o) = self.observer {
            o.on_inter_compaction(&combined);
        }

        let mut new = Vec::with_capacity(1 + tail.len());
        new.push(ConversationItem::System {
            content: format!("[CONVERSATION HISTORY SUMMARY]\n{}", combined),
            tokens: self.counter.count_tokens(&combined),
        });
        new.extend_from_slice(tail);
        Ok((combined, new))
    }
}

fn self_auto_compact_threshold_default() -> u32 {
    85
}

// ---------------------------------------------------------------------------
// Metrics export.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct CompactionMetrics {
    pub kind: &'static str,
    pub input_items: usize,
    pub output_items: usize,
    pub input_tokens: usize,
    pub output_tokens: usize,
    pub duration_ms: u64,
}

pub struct CompactionMetricsRegistry {
    inner: parking_lot::Mutex<AHashMap<&'static str, Vec<CompactionMetrics>>>,
}

impl Default for CompactionMetricsRegistry {
    fn default() -> Self {
        Self {
            inner: parking_lot::Mutex::new(AHashMap::new()),
        }
    }
}

impl CompactionMetricsRegistry {
    pub fn record(&self, m: CompactionMetrics) {
        let mut inner = self.inner.lock();
        inner.entry(m.kind).or_default().push(m);
        // Cap each kind to the last 100 entries.
        if let Some(v) = inner.get_mut(m.kind) {
            if v.len() > 100 {
                v.drain(..v.len() - 100);
            }
        }
    }

    pub fn all(&self) -> Vec<CompactionMetrics> {
        let inner = self.inner.lock();
        let mut out: Vec<CompactionMetrics> = Vec::new();
        for v in inner.values() {
            out.extend(v.iter().cloned());
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct DummySampler;
    impl CompactionSampler for DummySampler {
        fn summarize(&self, _prompt: &str, items: &[&ConversationItem]) -> Result<String, CompactionError> {
            let mut s = String::from("[SUMMARY] ");
            for it in items {
                s.push_str(it.role_str());
                s.push(' ');
            }
            Ok(s)
        }
    }

    fn make_items(n: usize) -> Vec<ConversationItem> {
        (0..n)
            .map(|i| ConversationItem::User {
                content: format!("msg {}", i),
                tokens: 10,
            })
            .collect()
    }

    #[test]
    fn code_compaction_rebuilds_fresh_history() {
        let counter = DefaultTokenCounter;
        let sampler = DummySampler;
        let engine = CompactionEngine::new(&counter, &sampler);
        let items = make_items(5);
        let (summary, fresh) = engine.code_compact(&items).unwrap();
        assert!(summary.contains("SUMMARY"));
        assert_eq!(fresh.len(), 1);
        assert!(matches!(fresh[0], ConversationItem::System { .. }));
    }

    #[test]
    fn intra_compaction_keeps_tail() {
        let counter = DefaultTokenCounter;
        let sampler = DummySampler;
        let engine = CompactionEngine::new(&counter, &sampler);
        let items = make_items(10);
        let (_summary, new) = engine.intra_compact(&items, 4).unwrap();
        // 1 summary + 4 tail = 5
        assert_eq!(new.len(), 5);
        // Tail must be intact (last 4 messages).
        for (i, item) in new[1..].iter().enumerate() {
            if let ConversationItem::User { content, .. } = item {
                assert!(content.contains(&format!("msg {}", 6 + i)));
            }
        }
    }

    #[test]
    fn inter_compaction_chunks_and_keeps_tail() {
        let counter = DefaultTokenCounter;
        let sampler = DummySampler;
        let engine = CompactionEngine::new(&counter, &sampler);
        let items = make_items(20);
        let (_summary, new) = engine.inter_compact(&items, 3, 5).unwrap();
        // 1 combined summary + 5 tail = 6
        assert_eq!(new.len(), 6);
    }
}

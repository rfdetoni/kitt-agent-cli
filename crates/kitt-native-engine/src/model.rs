use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SearchOptions {
    pub regex: bool,
    pub case_sensitive: bool,
    pub max_results: usize,
    pub max_per_file: usize,
    pub context_lines: usize,
    pub token_budget: usize,
    pub include_hidden: bool,
}

impl SearchOptions {
    pub fn normalized(mut self) -> Self {
        if self.max_results == 0 { self.max_results = 50; }
        if self.max_per_file == 0 { self.max_per_file = 8; }
        if self.token_budget == 0 { self.token_budget = 1200; }
        self.max_results = self.max_results.min(500);
        self.max_per_file = self.max_per_file.min(100);
        self.context_lines = self.context_lines.min(12);
        self.token_budget = self.token_budget.min(32_000);
        self
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchHit {
    pub path: String,
    pub line: usize,
    pub column: usize,
    pub text: String,
    pub before: Vec<String>,
    pub after: Vec<String>,
    pub score: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResponse {
    pub hits: Vec<SearchHit>,
    pub matched_files: usize,
    pub total_matches_seen: usize,
    pub omitted_matches: usize,
    pub estimated_tokens: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Symbol {
    pub id: String,
    pub path: String,
    pub name: String,
    pub qualified_name: String,
    pub kind: String,
    pub start_line: usize,
    pub end_line: usize,
    pub start_byte: usize,
    pub end_byte: usize,
    pub source_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SymbolReference {
    pub path: String,
    pub line: usize,
    pub containing_symbol: Option<String>,
    pub target_name: String,
    pub kind: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SymbolRead {
    pub symbol: Symbol,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EditRequest {
    pub symbol_id: String,
    pub replacement: String,
    pub expected_hash: Option<String>,
    pub validate_syntax: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EditResponse {
    pub path: String,
    pub old_hash: String,
    pub new_hash: String,
    pub changed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompressionResponse {
    pub output: String,
    pub family: String,
    pub changed: bool,
    pub raw_bytes: usize,
    pub output_bytes: usize,
    pub omitted_lines: usize,
    pub raw_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum LeaseMode { Read, Write }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LeaseView {
    pub resource_id: String,
    pub owner_id: String,
    pub mode: LeaseMode,
    pub intent: String,
    pub expires_at_ms: u64,
}

pub fn lease_conflicts(existing: &LeaseView, requested_owner: &str, requested_mode: LeaseMode) -> bool {
    if existing.owner_id == requested_owner { return false; }
    matches!(existing.mode, LeaseMode::Write) || matches!(requested_mode, LeaseMode::Write)
}

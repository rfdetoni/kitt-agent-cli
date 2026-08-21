mod edit;
mod language;
pub mod model;
mod output;
mod search;
mod symbols;

use anyhow::{Result, anyhow};
use model::{
    CompressionResponse, EditRequest, EditResponse, SearchOptions, SearchResponse, Symbol,
    SymbolRead, SymbolReference,
};
use std::path::{Path, PathBuf};

pub struct NativeEngine {
    root: PathBuf,
}

impl NativeEngine {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let root = root
            .as_ref()
            .canonicalize()
            .map_err(|e| anyhow!("invalid repository root: {e}"))?;
        Ok(Self { root })
    }
    pub fn root(&self) -> &Path {
        &self.root
    }
    pub fn search(&self, query: &str, options: SearchOptions) -> Result<SearchResponse> {
        search::search(&self.root, query, options)
    }
    pub fn find_symbols(&self, query: &str, limit: usize) -> Result<Vec<Symbol>> {
        symbols::find_symbols(&self.root, query, limit)
    }
    pub fn read_symbol(&self, id: &str) -> Result<Option<SymbolRead>> {
        symbols::read_symbol(&self.root, id)
    }
    pub fn references(&self, id_or_name: &str, limit: usize) -> Result<Vec<SymbolReference>> {
        symbols::find_references(&self.root, id_or_name, limit)
    }
    pub fn dependency_edges(
        &self,
        max_symbols: usize,
    ) -> Result<std::collections::HashMap<String, Vec<String>>> {
        symbols::dependency_edges(&self.root, max_symbols)
    }
    pub fn replace_symbol(&self, request: EditRequest) -> Result<EditResponse> {
        edit::replace_symbol(&self.root, request)
    }
}

pub fn compress_process_output(
    argv: &[String],
    stdout: &str,
    stderr: &str,
    returncode: i32,
) -> CompressionResponse {
    output::compress(argv, stdout, stderr, returncode)
}

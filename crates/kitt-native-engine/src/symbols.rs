use crate::language::{grammar, identify, kind_label, name_of, symbol_kinds};
use crate::model::{Symbol, SymbolRead, SymbolReference};
use anyhow::{Context, Result};
use ignore::WalkBuilder;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use tree_sitter::{Node, Parser};

fn hash_bytes(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn visit_symbols(
    node: Node<'_>,
    source: &[u8],
    path: &str,
    kinds: &[&str],
    parents: &mut Vec<String>,
    out: &mut Vec<Symbol>,
) {
    let is_symbol = kinds.iter().any(|kind| *kind == node.kind());
    let mut pushed = false;
    if is_symbol {
        if let Some(name) = name_of(node, source) {
            let qualified = if parents.is_empty() {
                name.clone()
            } else {
                format!("{}::{}", parents.join("::"), name)
            };
            let start = node.start_position().row + 1;
            let end = node.end_position().row + 1;
            let slice = source.get(node.byte_range()).unwrap_or_default();
            let id = format!("{}::{}", path, qualified);
            out.push(Symbol {
                id,
                path: path.to_string(),
                name: name.clone(),
                qualified_name: qualified,
                kind: kind_label(node.kind()),
                start_line: start,
                end_line: end,
                start_byte: node.start_byte(),
                end_byte: node.end_byte(),
                source_hash: hash_bytes(slice),
            });
            parents.push(name);
            pushed = true;
        }
    }
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        visit_symbols(child, source, path, kinds, parents, out);
    }
    if pushed {
        parents.pop();
    }
}

pub fn symbols_in_file(root: &Path, relative: &str) -> Result<Vec<Symbol>> {
    let path = root.join(relative);
    let Some(lang_id) = identify(&path) else {
        return Ok(Vec::new());
    };
    let source = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
    let mut parser = Parser::new();
    parser.set_language(&grammar(lang_id)?)?;
    let tree = parser
        .parse(&source, None)
        .context("parser returned no tree")?;
    let mut out = Vec::new();
    visit_symbols(
        tree.root_node(),
        &source,
        &relative.replace('\\', "/"),
        symbol_kinds(lang_id),
        &mut Vec::new(),
        &mut out,
    );
    Ok(out)
}

pub fn scan_symbols(root: &Path, max_files: usize) -> Result<Vec<Symbol>> {
    let mut out = Vec::new();
    let mut count = 0usize;
    for entry in WalkBuilder::new(root)
        .hidden(true)
        .git_ignore(true)
        .build()
        .filter_map(Result::ok)
    {
        if count >= max_files {
            break;
        }
        if !entry.file_type().map(|t| t.is_file()).unwrap_or(false) {
            continue;
        }
        let path = entry.path();
        if identify(path).is_none() {
            continue;
        }
        let rel = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        if let Ok(mut syms) = symbols_in_file(root, &rel) {
            out.append(&mut syms);
        }
        count += 1;
    }
    Ok(out)
}

pub fn find_symbols(root: &Path, query: &str, limit: usize) -> Result<Vec<Symbol>> {
    let q = query.to_ascii_lowercase();
    let mut syms = scan_symbols(root, 100_000)?;
    syms.retain(|s| {
        s.name.to_ascii_lowercase().contains(&q)
            || s.qualified_name.to_ascii_lowercase().contains(&q)
            || s.id.to_ascii_lowercase().contains(&q)
    });
    syms.sort_by_key(|s| {
        let exact =
            s.name.eq_ignore_ascii_case(query) || s.qualified_name.eq_ignore_ascii_case(query);
        (!exact, s.qualified_name.len(), s.path.clone())
    });
    syms.truncate(limit.clamp(1, 500));
    Ok(syms)
}

pub fn read_symbol(root: &Path, symbol_id: &str) -> Result<Option<SymbolRead>> {
    let path = symbol_id.split("::").next().unwrap_or("");
    if path.is_empty() {
        return Ok(None);
    }
    let source = fs::read(root.join(path))?;
    let symbols = symbols_in_file(root, path)?;
    let Some(symbol) = symbols.into_iter().find(|s| s.id == symbol_id) else {
        return Ok(None);
    };
    let text = String::from_utf8_lossy(
        source
            .get(symbol.start_byte..symbol.end_byte)
            .unwrap_or_default(),
    )
    .to_string();
    Ok(Some(SymbolRead {
        symbol,
        source: text,
    }))
}

fn containing_symbol(symbols: &[Symbol], line: usize) -> Option<&Symbol> {
    symbols
        .iter()
        .filter(|s| s.start_line <= line && line <= s.end_line)
        .min_by_key(|s| s.end_line.saturating_sub(s.start_line))
}

pub fn find_references(root: &Path, target: &str, limit: usize) -> Result<Vec<SymbolReference>> {
    let target_name = target.rsplit("::").next().unwrap_or(target);
    let word = regex::Regex::new(&format!(r"\b{}\b", regex::escape(target_name)))?;
    let mut out = Vec::new();
    for entry in WalkBuilder::new(root)
        .hidden(true)
        .git_ignore(true)
        .build()
        .filter_map(Result::ok)
    {
        if out.len() >= limit {
            break;
        }
        if !entry.file_type().map(|t| t.is_file()).unwrap_or(false) {
            continue;
        }
        let path = entry.path();
        if identify(path).is_none() {
            continue;
        }
        let rel = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        let text = match fs::read_to_string(path) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let syms = symbols_in_file(root, &rel).unwrap_or_default();
        for (idx, line) in text.lines().enumerate() {
            if out.len() >= limit {
                break;
            }
            if word.is_match(line) {
                let line_no = idx + 1;
                if syms
                    .iter()
                    .any(|s| s.name == target_name && s.start_line == line_no)
                {
                    continue;
                }
                out.push(SymbolReference {
                    path: rel.clone(),
                    line: line_no,
                    containing_symbol: containing_symbol(&syms, line_no).map(|s| s.id.clone()),
                    target_name: target_name.to_string(),
                    kind: "lexical_ast_reference".to_string(),
                });
            }
        }
    }
    Ok(out)
}

pub fn dependency_edges(root: &Path, max_symbols: usize) -> Result<HashMap<String, Vec<String>>> {
    let symbols = scan_symbols(root, 100_000)?;
    let mut by_name: HashMap<String, Vec<&Symbol>> = HashMap::new();
    for s in &symbols {
        by_name.entry(s.name.clone()).or_default().push(s);
    }
    let callish = regex::Regex::new(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\(|\.)")?;
    let mut graph = HashMap::new();
    for s in symbols.iter().take(max_symbols) {
        let Some(read) = read_symbol(root, &s.id)? else {
            continue;
        };
        let mut deps = Vec::new();
        for capture in callish.captures_iter(&read.source) {
            let Some(name) = capture.get(1).map(|m| m.as_str()) else {
                continue;
            };
            if name == s.name {
                continue;
            }
            if let Some(candidates) = by_name.get(name) {
                if candidates.len() == 1 {
                    deps.push(candidates[0].id.clone());
                }
            }
        }
        deps.sort();
        deps.dedup();
        if !deps.is_empty() {
            graph.insert(s.id.clone(), deps);
        }
    }
    Ok(graph)
}

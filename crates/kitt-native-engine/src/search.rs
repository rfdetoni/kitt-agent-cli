use crate::model::{SearchHit, SearchOptions, SearchResponse};
use anyhow::{Context, Result};
use ignore::WalkBuilder;
use regex::{Regex, RegexBuilder};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

fn estimate_tokens(text: &str) -> usize {
    text.chars().count().div_ceil(4)
}

fn compact_line(line: &str, byte_col: usize, needle_len: usize) -> String {
    const MAX: usize = 360;
    if line.chars().count() <= MAX {
        return line.to_string();
    }
    let chars: Vec<char> = line.chars().collect();
    let col = line[..byte_col.min(line.len())].chars().count();
    let width = needle_len.clamp(1, 80);
    let left = col.saturating_sub(140);
    let right = (col + width + 140).min(chars.len());
    format!(
        "{}{}{}",
        if left > 0 { "…" } else { "" },
        chars[left..right].iter().collect::<String>(),
        if right < chars.len() { "…" } else { "" }
    )
}

fn build_regex(query: &str, opts: &SearchOptions) -> Result<Regex> {
    let pattern = if opts.regex {
        query.to_string()
    } else {
        regex::escape(query)
    };
    RegexBuilder::new(&pattern)
        .case_insensitive(!opts.case_sensitive)
        .build()
        .context("invalid search pattern")
}

pub fn search(root: &Path, query: &str, options: SearchOptions) -> Result<SearchResponse> {
    let opts = options.normalized();
    let regex = build_regex(query, &opts)?;
    let mut walker = WalkBuilder::new(root);
    walker
        .hidden(!opts.include_hidden)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true);

    let mut hits = Vec::new();
    let mut total_seen = 0usize;
    let mut per_file: HashMap<PathBuf, usize> = HashMap::new();
    let mut matched_files = HashSet::new();
    let mut tokens = 0usize;

    'files: for entry in walker.build().filter_map(Result::ok) {
        if !entry.file_type().map(|t| t.is_file()).unwrap_or(false) {
            continue;
        }
        let path = entry.path();
        let meta = match entry.metadata() {
            Ok(v) => v,
            Err(_) => continue,
        };
        if meta.len() > 4 * 1024 * 1024 {
            continue;
        }
        let bytes = match fs::read(path) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if bytes.iter().take(8192).any(|b| *b == 0) {
            continue;
        }
        let text = String::from_utf8_lossy(&bytes);
        let lines: Vec<&str> = text.lines().collect();
        let rel = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        for (idx, line) in lines.iter().enumerate() {
            let Some(m) = regex.find(line) else {
                continue;
            };
            total_seen += 1;
            let used = per_file.entry(path.to_path_buf()).or_default();
            if *used >= opts.max_per_file {
                continue;
            }
            if hits.len() >= opts.max_results {
                break 'files;
            }
            let start = idx.saturating_sub(opts.context_lines);
            let end = (idx + opts.context_lines + 1).min(lines.len());
            let before = lines[start..idx]
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>();
            let after = lines[idx + 1..end]
                .iter()
                .map(|s| s.to_string())
                .collect::<Vec<_>>();
            let rendered = compact_line(line, m.start(), m.end().saturating_sub(m.start()));
            let hit_tokens = estimate_tokens(&rendered)
                + before.iter().map(|x| estimate_tokens(x)).sum::<usize>()
                + after.iter().map(|x| estimate_tokens(x)).sum::<usize>()
                + 12;
            if !hits.is_empty() && tokens + hit_tokens > opts.token_budget {
                break 'files;
            }
            tokens += hit_tokens;
            *used += 1;
            matched_files.insert(rel.clone());
            hits.push(SearchHit {
                path: rel.clone(),
                line: idx + 1,
                column: line[..m.start()].chars().count() + 1,
                text: rendered,
                before,
                after,
                score: 1.0 / (1.0 + idx as f32 / 10_000.0),
            });
        }
    }
    Ok(SearchResponse {
        omitted_matches: total_seen.saturating_sub(hits.len()),
        matched_files: matched_files.len(),
        total_matches_seen: total_seen,
        estimated_tokens: tokens,
        hits,
    })
}

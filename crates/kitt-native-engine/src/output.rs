use crate::model::CompressionResponse;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

const DEFAULT_TOKEN_BUDGET: usize = 1200;
const MIN_TOKEN_BUDGET: usize = 64;
const MAX_TOKEN_BUDGET: usize = 32_000;

fn sha(text: &str) -> String { hex::encode(Sha256::digest(text.as_bytes())) }
fn estimate_tokens(text: &str) -> usize { text.len().div_ceil(4) }

fn normalized_program(value: &str) -> String {
    let raw = value.rsplit(['/', '\\']).next().unwrap_or(value).to_ascii_lowercase();
    for suffix in [".exe", ".cmd", ".bat"] {
        if let Some(stripped) = raw.strip_suffix(suffix) { return stripped.to_string(); }
    }
    raw
}

fn effective_command(argv: &[String]) -> String {
    if argv.is_empty() { return String::new(); }
    let first = normalized_program(&argv[0]);
    if matches!(first.as_str(), "python" | "python3" | "py") && argv.get(1).map(String::as_str) == Some("-m") {
        return argv.get(2).map(|v| normalized_program(v)).unwrap_or_default();
    }
    if matches!(first.as_str(), "uv" | "poetry" | "pipenv" | "rye") && argv.get(1).map(|v| v.eq_ignore_ascii_case("run")) == Some(true) {
        return argv.get(2).map(|v| normalized_program(v)).unwrap_or(first);
    }
    first
}

fn family(argv: &[String]) -> &'static str {
    match effective_command(argv).as_str() {
        "grep" | "rg" | "ripgrep" => "search",
        "git" | "gh" | "glab" => "vcs",
        "mvn" | "mvnw" | "gradle" | "gradlew" | "cargo" | "go" | "pytest" | "npm" | "pnpm" | "yarn" | "bun" | "npx" | "jest" | "vitest" | "playwright" | "rspec" | "phpunit" | "composer" | "dotnet" | "make" | "cmake" | "ninja" | "sbt" => "build_test",
        "ruff" | "mypy" | "eslint" | "biome" | "prettier" | "tsc" | "shellcheck" | "hadolint" | "golangci-lint" | "checkstyle" | "spotbugs" | "pmd" => "diagnostics",
        "docker" | "podman" | "kubectl" | "oc" | "terraform" | "terragrunt" | "pulumi" | "helm" | "aws" | "gcloud" | "az" => "infra",
        "ls" | "find" | "tree" | "wc" | "cat" | "head" | "tail" => "listing",
        _ => "generic",
    }
}

fn byte_budget(token_budget: usize) -> usize {
    token_budget.clamp(MIN_TOKEN_BUDGET, MAX_TOKEN_BUDGET).saturating_mul(4)
}

fn truncate_utf8_with_suffix(value: &str, max_bytes: usize, suffix: &str) -> String {
    if value.len() <= max_bytes { return value.to_string(); }
    if max_bytes == 0 { return String::new(); }
    let suffix = if suffix.len() < max_bytes { suffix } else { "" };
    let mut keep = max_bytes.saturating_sub(suffix.len()).min(value.len());
    while keep > 0 && !value.is_char_boundary(keep) { keep -= 1; }
    let mut out = value[..keep].to_string();
    out.push_str(suffix);
    out
}

fn finalize(raw: &str, candidate: String, family: &str, omitted: usize, token_budget: usize) -> CompressionResponse {
    let budget = byte_budget(token_budget);
    let output = if raw.len() <= budget {
        raw.to_string()
    } else {
        let source = if candidate.trim().is_empty() { raw } else { candidate.as_str() };
        truncate_utf8_with_suffix(source, budget, "\n… output truncated to KITT token budget")
    };
    CompressionResponse {
        changed: output != raw,
        output_bytes: output.len(), raw_bytes: raw.len(), output,
        family: family.to_string(), omitted_lines: omitted, raw_sha256: sha(raw),
    }
}

fn compress_search(raw: &str) -> (String, usize) {
    let re = Regex::new(r"^(.*?):(\d+)(?::\d+)?:?(.*)$").unwrap();
    let mut grouped: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut total = 0usize;
    for line in raw.lines() {
        let Some(c) = re.captures(line) else { continue; };
        total += 1;
        let items = grouped.entry(c.get(1).unwrap().as_str().to_string()).or_default();
        if items.len() < 8 {
            items.push(format!("{}:{}", c.get(2).unwrap().as_str(), c.get(3).map(|m| m.as_str().trim()).unwrap_or("")));
        }
    }
    if grouped.is_empty() { return (raw.to_string(), 0); }
    let mut out = String::new();
    let mut emitted = 0usize;
    for (file, lines) in grouped.into_iter().take(40) {
        out.push_str(&file); out.push('\n');
        for line in lines { emitted += 1; out.push_str("  "); out.push_str(&line); out.push('\n'); }
    }
    let omitted = total.saturating_sub(emitted);
    if omitted > 0 { out.push_str(&format!("… {omitted} additional matches omitted")); }
    (out, omitted)
}

fn command_has(argv: &[String], needle: &str) -> bool { argv.iter().any(|a| a.eq_ignore_ascii_case(needle)) }

fn compress_git_status(raw: &str) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().filter(|l| !l.trim().is_empty()).collect();
    if lines.len() <= 24 { return (raw.to_string(), 0); }
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for line in &lines {
        let status = line.get(..2).unwrap_or("??").trim();
        *counts.entry(if status.is_empty() { "changed".into() } else { status.into() }).or_default() += 1;
    }
    let keep = lines.len().min(20);
    let mut out = lines[..keep].join("\n");
    out.push_str("\n[KITT status summary:");
    for (status, count) in counts { out.push_str(&format!(" {status}={count}")); }
    let omitted = lines.len().saturating_sub(keep);
    if omitted > 0 { out.push_str(&format!("; omitted={omitted}")); }
    out.push(']');
    (out, omitted)
}

fn is_diff_metadata(line: &str) -> bool {
    line.starts_with("diff --git ") || line.starts_with("index ") || line.starts_with("--- ") || line.starts_with("+++ ") || line.starts_with("@@") || line.starts_with("new file mode ") || line.starts_with("deleted file mode ") || line.starts_with("old mode ") || line.starts_with("new mode ") || line.starts_with("similarity index ") || line.starts_with("dissimilarity index ") || line.starts_with("rename from ") || line.starts_with("rename to ") || line.starts_with("copy from ") || line.starts_with("copy to ") || line.starts_with("Binary files ")
}

fn compress_git_diff(raw: &str) -> (String, usize) {
    let total = raw.lines().count();
    let mut selected = Vec::new();
    for line in raw.lines() {
        let changed = (line.starts_with('+') && !line.starts_with("+++")) || (line.starts_with('-') && !line.starts_with("---"));
        if is_diff_metadata(line) || changed { selected.push(line); }
    }
    if selected.is_empty() { return (raw.to_string(), 0); }
    let omitted = total.saturating_sub(selected.len());
    let mut out = selected.join("\n");
    if omitted > 0 { out.push_str(&format!("\n… {omitted} unchanged diff context line(s) omitted")); }
    (out, omitted)
}

fn compress_vcs(argv: &[String], raw: &str) -> (String, usize) {
    if effective_command(argv) == "git" {
        if command_has(argv, "status") { return compress_git_status(raw); }
        if command_has(argv, "diff") { return compress_git_diff(raw); }
    }
    compress_generic(raw)
}

fn failure_marker(lower: &str) -> bool {
    ["error", "failed", "failure", "exception", "assert", "traceback", "caused by", "build failure", "compilation failure", "compilation error", "failures:", "errors:", "panic:"].iter().any(|m| lower.contains(m))
}

fn success_summary(lower: &str) -> bool {
    let mut parts = lower.split_whitespace();
    let pytest_summary = parts
        .next()
        .map(|value| !value.is_empty() && value.bytes().all(|b| b.is_ascii_digit()))
        .unwrap_or(false)
        && parts.next() == Some("passed");
    pytest_summary
        || lower.contains("test result:")
        || lower.contains("tests run:")
        || lower.contains("build success")
        || lower.contains("build successful")
        || lower.starts_with("tests:")
        || lower.contains("finished in ")
}

fn compress_build(raw: &str, success: bool) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().collect();
    if lines.len() <= 32 { return (raw.to_string(), 0); }
    let mut indexes = BTreeSet::new();
    if success {
        for (index, line) in lines.iter().enumerate().rev().take(80) {
            if success_summary(&line.to_ascii_lowercase()) { indexes.insert(index); }
            if indexes.len() >= 16 { break; }
        }
        if indexes.is_empty() { indexes.extend(lines.len().saturating_sub(20)..lines.len()); }
    } else {
        for (index, line) in lines.iter().enumerate() {
            let lower = line.to_ascii_lowercase();
            if failure_marker(&lower) || lower.contains(" at ") || lower.starts_with("  file ") {
                indexes.extend(index.saturating_sub(1)..(index + 3).min(lines.len()));
            }
            if indexes.len() >= 140 { break; }
        }
        if indexes.is_empty() { indexes.extend(lines.len().saturating_sub(48)..lines.len()); }
    }
    let selected: Vec<&str> = indexes.iter().filter_map(|i| lines.get(*i).copied()).collect();
    let omitted = lines.len().saturating_sub(selected.len());
    let mut out = selected.join("\n");
    if omitted > 0 { out.push_str(&format!("\n… {omitted} routine line(s) omitted")); }
    (out, omitted)
}

fn compress_generic(raw: &str) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().collect();
    if lines.len() <= 80 { return (raw.to_string(), 0); }
    let head = 40usize.min(lines.len());
    let tail = 24usize.min(lines.len().saturating_sub(head));
    let omitted = lines.len().saturating_sub(head + tail);
    let mut out = lines[..head].join("\n");
    if omitted > 0 { out.push_str(&format!("\n… {omitted} line(s) omitted …\n")); }
    if tail > 0 { out.push_str(&lines[lines.len() - tail..].join("\n")); }
    (out, omitted)
}

pub fn compress_with_budget(argv: &[String], stdout: &str, stderr: &str, returncode: i32, token_budget: usize) -> CompressionResponse {
    let raw = if stderr.is_empty() { stdout.to_string() } else if stdout.is_empty() { stderr.to_string() } else { format!("{stdout}\n{stderr}") };
    let fam = family(argv);
    if raw.is_empty() || estimate_tokens(&raw) <= token_budget.clamp(MIN_TOKEN_BUDGET, MAX_TOKEN_BUDGET) {
        return finalize(&raw, raw.clone(), fam, 0, token_budget);
    }
    let (candidate, omitted) = match fam {
        "search" => compress_search(&raw),
        "build_test" | "diagnostics" | "infra" => compress_build(&raw, returncode == 0),
        "vcs" => compress_vcs(argv, &raw),
        _ => compress_generic(&raw),
    };
    finalize(&raw, candidate, fam, omitted, token_budget)
}

pub fn compress(argv: &[String], stdout: &str, stderr: &str, returncode: i32) -> CompressionResponse {
    compress_with_budget(argv, stdout, stderr, returncode, DEFAULT_TOKEN_BUDGET)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hard_budget_applies_when_candidate_is_not_better() {
        let raw = "x".repeat(20_000);
        let r = compress_with_budget(&["custom".into()], &raw, "", 0, 64);
        assert!(r.changed); assert!(r.output.len() <= 64 * 4);
    }

    #[test]
    fn unicode_budget_is_byte_bounded_and_valid_utf8() {
        let raw = "é".repeat(20_000);
        let r = compress_with_budget(&["custom".into()], &raw, "", 0, 64);
        assert!(r.changed); assert!(r.output.len() <= 64 * 4); assert!(std::str::from_utf8(r.output.as_bytes()).is_ok());
    }

    #[test]
    fn successful_pytest_keeps_summary_not_pass_spam() {
        let mut rows = (0..500).map(|i| format!("tests/test_{i}.py::test_case PASSED")).collect::<Vec<_>>();
        rows.push("500 passed in 2.10s".into());
        let raw = rows.join("\n");
        let r = compress_with_budget(&["pytest".into(), "-v".into()], &raw, "", 0, 128);
        assert!(r.output.contains("500 passed")); assert!(!r.output.contains("tests/test_499.py"));
    }

    #[test]
    fn wrapper_command_is_classified() {
        let raw = (0..300).map(|i| format!("tests/test_{i}.py::test_case PASSED")).chain(std::iter::once("300 passed in 1.20s".to_string())).collect::<Vec<_>>().join("\n");
        let r = compress_with_budget(&["uv".into(), "run".into(), "pytest".into(), "-v".into()], &raw, "", 0, 128);
        assert_eq!(r.family, "build_test"); assert!(r.output.contains("300 passed"));
    }

    #[test]
    fn git_diff_preserves_duplicate_changed_lines() {
        let mut rows = vec!["diff --git a/a.py b/a.py", "--- a/a.py", "+++ b/a.py", "@@ -1 +1 @@", "-same", "+same", "diff --git a/b.py b/b.py", "--- a/b.py", "+++ b/b.py", "@@ -1 +1 @@", "-same", "+same"].into_iter().map(str::to_string).collect::<Vec<_>>();
        rows.extend((0..200).map(|i| format!(" context {i}")));
        let r = compress_with_budget(&["git".into(), "diff".into()], &rows.join("\n"), "", 0, 128);
        assert_eq!(r.output.matches("-same").count(), 2); assert_eq!(r.output.matches("+same").count(), 2);
    }

    #[test]
    fn search_counts_matches_in_non_emitted_files() {
        let raw = (0..60).map(|i| format!("file-{i}.py:1:match")).collect::<Vec<_>>().join("\n");
        let r = compress_with_budget(&["rg".into(), "match".into()], &raw, "", 0, 256);
        assert!(r.omitted_lines >= 20);
    }
}

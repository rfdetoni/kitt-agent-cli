use crate::model::CompressionResponse;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};

const DEFAULT_TOKEN_BUDGET: usize = 1200;

fn sha(text: &str) -> String {
    hex::encode(Sha256::digest(text.as_bytes()))
}

fn estimate_tokens(text: &str) -> usize {
    text.chars().count().div_ceil(4)
}

fn normalized_command(argv: &[String]) -> String {
    let raw = argv
        .first()
        .map(|s| s.rsplit(['/', '\\']).next().unwrap_or(s))
        .unwrap_or("")
        .to_ascii_lowercase();
    for suffix in [".exe", ".cmd", ".bat"] {
        if let Some(stripped) = raw.strip_suffix(suffix) {
            return stripped.to_string();
        }
    }
    raw
}

fn family(argv: &[String]) -> &'static str {
    let cmd = normalized_command(argv);
    let inferred = if matches!(cmd.as_str(), "python" | "python3" | "py")
        && argv.get(1).map(String::as_str) == Some("-m")
    {
        argv.get(2).map(|s| s.as_str()).unwrap_or("")
    } else {
        cmd.as_str()
    };
    match inferred {
        "grep" | "rg" | "ripgrep" => "search",
        "git" | "gh" | "glab" => "vcs",
        "mvn" | "mvnw" | "gradle" | "gradlew" | "cargo" | "go" | "pytest" | "npm" | "pnpm"
        | "yarn" | "bun" | "npx" | "jest" | "vitest" | "playwright" | "rspec" | "phpunit"
        | "composer" | "dotnet" | "make" | "cmake" | "ninja" | "sbt" => "build_test",
        "ruff" | "mypy" | "eslint" | "biome" | "prettier" | "tsc" | "shellcheck" | "hadolint"
        | "golangci-lint" | "checkstyle" | "spotbugs" | "pmd" => "diagnostics",
        "docker" | "podman" | "kubectl" | "oc" | "terraform" | "terragrunt" | "pulumi" | "helm"
        | "aws" | "gcloud" | "az" => "infra",
        "ls" | "find" | "tree" | "wc" | "cat" | "head" | "tail" => "listing",
        _ => "generic",
    }
}

fn token_budget_chars(token_budget: usize) -> usize {
    token_budget.clamp(64, 32_000).saturating_mul(4)
}

fn bound_candidate(candidate: String, token_budget: usize) -> String {
    let max_chars = token_budget_chars(token_budget);
    if candidate.chars().count() <= max_chars {
        return candidate;
    }
    let suffix = "\n… output truncated to KITT token budget; raw output retained";
    let suffix_chars = suffix.chars().count();
    let keep = max_chars.saturating_sub(suffix_chars);
    let mut out: String = candidate.chars().take(keep).collect();
    if keep > 0 {
        out.push_str(suffix);
    }
    out
}

fn never_worse(
    raw: &str,
    candidate: String,
    family: &str,
    omitted: usize,
    token_budget: usize,
) -> CompressionResponse {
    let candidate = bound_candidate(candidate, token_budget);
    let output = if !candidate.trim().is_empty() && candidate.len() < raw.len() {
        candidate
    } else {
        raw.to_string()
    };
    CompressionResponse {
        changed: output.len() < raw.len(),
        output_bytes: output.len(),
        raw_bytes: raw.len(),
        output,
        family: family.to_string(),
        omitted_lines: if omitted > 0 { omitted } else { 0 },
        raw_sha256: sha(raw),
    }
}

fn compress_search(raw: &str) -> (String, usize) {
    let re = Regex::new(r"^(.*?):(\d+)(?::\d+)?:?(.*)$").unwrap();
    let mut grouped: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut seen = 0usize;
    for line in raw.lines() {
        if let Some(c) = re.captures(line) {
            seen += 1;
            let file = c.get(1).unwrap().as_str().to_string();
            let items = grouped.entry(file).or_default();
            if items.len() < 8 {
                items.push(format!(
                    "{}:{}",
                    c.get(2).unwrap().as_str(),
                    c.get(3).map(|m| m.as_str().trim()).unwrap_or("")
                ));
            }
        }
    }
    if grouped.is_empty() {
        return (raw.to_string(), 0);
    }
    let retained: usize = grouped.values().map(Vec::len).sum();
    let mut out = String::new();
    for (file, lines) in grouped.into_iter().take(40) {
        out.push_str(&file);
        out.push('\n');
        for line in lines {
            out.push_str("  ");
            out.push_str(&line);
            out.push('\n');
        }
    }
    let omitted = seen.saturating_sub(retained);
    if omitted > 0 {
        out.push_str(&format!("… {omitted} additional matches omitted"));
    }
    (out, omitted)
}

fn command_has(argv: &[String], needle: &str) -> bool {
    argv.iter().any(|arg| arg.eq_ignore_ascii_case(needle))
}

fn compress_git_status(raw: &str) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().filter(|line| !line.trim().is_empty()).collect();
    if lines.len() <= 24 {
        return (raw.to_string(), 0);
    }
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for line in &lines {
        let status = line.get(..2).unwrap_or("??").trim();
        *counts.entry(if status.is_empty() { "changed".into() } else { status.into() }).or_default() += 1;
    }
    let keep = lines.len().min(20);
    let mut out = lines[..keep].join("\n");
    out.push_str("\n[KITT status summary:");
    for (status, count) in counts {
        out.push_str(&format!(" {status}={count}"));
    }
    let omitted = lines.len().saturating_sub(keep);
    if omitted > 0 {
        out.push_str(&format!("; omitted={omitted}"));
    }
    out.push(']');
    (out, omitted)
}

fn compress_git_diff(raw: &str) -> (String, usize) {
    let total = raw.lines().count();
    let mut selected = Vec::new();
    let mut seen = HashSet::new();
    for line in raw.lines() {
        let keep = line.starts_with("diff --git ")
            || line.starts_with("@@")
            || (line.starts_with('+') && !line.starts_with("+++"))
            || (line.starts_with('-') && !line.starts_with("---"))
            || line.starts_with("Binary files ");
        if keep && seen.insert(line.to_string()) {
            selected.push(line);
        }
    }
    if selected.is_empty() {
        return (raw.to_string(), 0);
    }
    let omitted = total.saturating_sub(selected.len());
    let mut out = selected.join("\n");
    if omitted > 0 {
        out.push_str(&format!("\n… {omitted} diff boilerplate/context lines omitted"));
    }
    (out, omitted)
}

fn compress_vcs(argv: &[String], raw: &str) -> (String, usize) {
    if normalized_command(argv) == "git" {
        if command_has(argv, "status") {
            return compress_git_status(raw);
        }
        if command_has(argv, "diff") {
            return compress_git_diff(raw);
        }
    }
    compress_generic(raw)
}

fn interesting_build_line(lower: &str, success: bool) -> bool {
    let strong = [
        "error", "failed", "failure", "exception", "assert", "traceback", "caused by",
        "test result", "tests run", "build success", "build failure", "failures:",
        "errors:", "compilation failure", "compilation error",
    ];
    if strong.iter().any(|marker| lower.contains(marker)) {
        return true;
    }
    if !success {
        return lower.contains("passed") || lower.contains(" at ") || lower.starts_with("  file ");
    }
    false
}

fn compress_build(raw: &str, success: bool) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().collect();
    if lines.len() <= 32 {
        return (raw.to_string(), 0);
    }

    let mut selected = Vec::new();
    let mut seen = HashSet::new();
    let mut repeat_counts: HashMap<String, usize> = HashMap::new();

    for (i, line) in lines.iter().enumerate() {
        let lower = line.to_ascii_lowercase();
        if interesting_build_line(&lower, success) {
            let start = if success { i } else { i.saturating_sub(1) };
            let end = if success { (i + 1).min(lines.len()) } else { (i + 3).min(lines.len()) };
            for item in &lines[start..end] {
                let key = item.trim().to_string();
                if key.is_empty() {
                    continue;
                }
                *repeat_counts.entry(key.clone()).or_default() += 1;
                if seen.insert(key) {
                    selected.push((*item).to_string());
                }
            }
        }
        if selected.len() >= if success { 32 } else { 120 } {
            break;
        }
    }

    if success {
        for line in lines.iter().rev().take(12).rev() {
            let lower = line.to_ascii_lowercase();
            if (lower.contains("passed")
                || lower.contains("success")
                || lower.contains("test result")
                || lower.contains("finished"))
                && seen.insert(line.trim().to_string())
            {
                selected.push((*line).to_string());
            }
        }
    } else if selected.is_empty() {
        selected.extend(lines.iter().rev().take(48).rev().map(|value| (*value).to_string()));
    }

    let duplicate_lines: usize = repeat_counts.values().map(|count| count.saturating_sub(1)).sum();
    let omitted = lines.len().saturating_sub(selected.len());
    let mut out = selected.join("\n");
    if duplicate_lines > 0 {
        out.push_str(&format!("\n[KITT deduplicated {duplicate_lines} repeated diagnostic lines]"));
    }
    if omitted > 0 {
        out.push_str(&format!("\n… {omitted} routine lines omitted"));
    }
    (out, omitted)
}

fn compress_generic(raw: &str) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().collect();
    if lines.len() <= 80 {
        return (raw.to_string(), 0);
    }
    let head = 40usize.min(lines.len());
    let tail = 24usize.min(lines.len().saturating_sub(head));
    let omitted = lines.len().saturating_sub(head + tail);
    let mut out = lines[..head].join("\n");
    if omitted > 0 {
        out.push_str(&format!("\n… {omitted} lines omitted …\n"));
    }
    if tail > 0 {
        out.push_str(&lines[lines.len() - tail..].join("\n"));
    }
    (out, omitted)
}

pub fn compress_with_budget(
    argv: &[String],
    stdout: &str,
    stderr: &str,
    returncode: i32,
    token_budget: usize,
) -> CompressionResponse {
    let raw = if stderr.is_empty() {
        stdout.to_string()
    } else if stdout.is_empty() {
        stderr.to_string()
    } else {
        format!("{}\n{}", stdout, stderr)
    };
    let fam = family(argv);
    if raw.is_empty() {
        return never_worse(&raw, raw.clone(), fam, 0, token_budget);
    }
    if estimate_tokens(&raw) <= token_budget.clamp(64, 32_000) {
        return never_worse(&raw, raw.clone(), fam, 0, token_budget);
    }

    let (candidate, omitted) = match fam {
        "search" => compress_search(&raw),
        "build_test" | "diagnostics" | "infra" => compress_build(&raw, returncode == 0),
        "vcs" => compress_vcs(argv, &raw),
        "listing" => compress_generic(&raw),
        _ => compress_generic(&raw),
    };
    never_worse(&raw, candidate, fam, omitted, token_budget)
}

pub fn compress(
    argv: &[String],
    stdout: &str,
    stderr: &str,
    returncode: i32,
) -> CompressionResponse {
    compress_with_budget(argv, stdout, stderr, returncode, DEFAULT_TOKEN_BUDGET)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn never_expands() {
        let raw = (0..400)
            .map(|i| format!("noise {i}"))
            .collect::<Vec<_>>()
            .join("\n");
        let r = compress_with_budget(&["mvn".into(), "test".into()], &raw, "", 0, 64);
        assert!(r.output.len() <= raw.len());
    }

    #[test]
    fn successful_pytest_drops_pass_spam() {
        let mut rows = (0..500)
            .map(|i| format!("tests/test_{i}.py::test_case PASSED"))
            .collect::<Vec<_>>();
        rows.push("500 passed in 2.10s".into());
        let raw = rows.join("\n");
        let r = compress_with_budget(&["pytest".into(), "-v".into()], &raw, "", 0, 128);
        assert!(r.changed);
        assert!(r.output.contains("500 passed"));
        assert!(r.output.len() < raw.len() / 4);
    }

    #[test]
    fn git_diff_keeps_changes_and_drops_boilerplate() {
        let mut rows = vec![
            "diff --git a/a.py b/a.py".to_string(),
            "index 123..456 100644".to_string(),
            "--- a/a.py".to_string(),
            "+++ b/a.py".to_string(),
            "@@ -1,3 +1,3 @@".to_string(),
            " unchanged".to_string(),
            "-old".to_string(),
            "+new".to_string(),
        ];
        rows.extend((0..200).map(|i| format!(" context {i}")));
        let raw = rows.join("\n");
        let r = compress_with_budget(&["git".into(), "diff".into()], &raw, "", 0, 128);
        assert!(r.changed);
        assert!(r.output.contains("diff --git"));
        assert!(r.output.contains("@@"));
        assert!(r.output.contains("-old"));
        assert!(r.output.contains("+new"));
        assert!(!r.output.contains("index 123"));
    }

    #[test]
    fn budget_caps_long_generic_output() {
        let raw = (0..1000)
            .map(|i| format!("row-{i} {}", "x".repeat(40)))
            .collect::<Vec<_>>()
            .join("\n");
        let r = compress_with_budget(&["custom".into()], &raw, "", 0, 64);
        assert!(r.changed);
        assert!(r.output.chars().count() <= 64 * 4);
    }
}

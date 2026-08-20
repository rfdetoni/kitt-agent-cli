use crate::model::CompressionResponse;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};

fn sha(text: &str) -> String { hex::encode(Sha256::digest(text.as_bytes())) }
fn family(argv: &[String]) -> &'static str {
    let cmd = argv.first().map(|s| s.rsplit(|c| c == '/' || c == '\\').next().unwrap_or(s)).unwrap_or("");
    match cmd {
        "grep" | "rg" | "ripgrep" => "search",
        "git" | "gh" | "glab" => "vcs",
        "mvn" | "mvnw" | "gradle" | "gradlew" | "cargo" | "go" | "pytest" |
        "npm" | "pnpm" | "yarn" | "bun" | "npx" | "jest" | "vitest" | "playwright" |
        "rspec" | "phpunit" | "composer" | "dotnet" | "make" | "cmake" | "ninja" | "sbt" => "build_test",
        "ruff" | "mypy" | "eslint" | "biome" | "prettier" | "tsc" | "shellcheck" |
        "hadolint" | "golangci-lint" | "checkstyle" | "spotbugs" | "pmd" => "diagnostics",
        "docker" | "podman" | "kubectl" | "oc" | "terraform" | "terragrunt" | "pulumi" |
        "helm" | "aws" | "gcloud" | "az" => "infra",
        "ls" | "find" | "tree" | "wc" | "cat" | "head" | "tail" => "listing",
        _ => "generic",
    }
}

fn never_worse(raw: &str, candidate: String, family: &str, omitted: usize) -> CompressionResponse {
    let output = if !candidate.trim().is_empty() && candidate.len() < raw.len() { candidate } else { raw.to_string() };
    CompressionResponse {
        changed: output.len() < raw.len(), output_bytes: output.len(), raw_bytes: raw.len(), output,
        family: family.to_string(), omitted_lines: if omitted > 0 { omitted } else { 0 }, raw_sha256: sha(raw),
    }
}

fn compress_search(raw: &str) -> (String, usize) {
    let re = Regex::new(r"^(.*?):(\d+)(?::\d+)?:?(.*)$").unwrap();
    let mut grouped: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut omitted = 0usize;
    for line in raw.lines() {
        if let Some(c) = re.captures(line) {
            let file = c.get(1).unwrap().as_str().to_string();
            let items = grouped.entry(file).or_default();
            if items.len() < 8 { items.push(format!("{}:{}", c.get(2).unwrap().as_str(), c.get(3).map(|m| m.as_str().trim()).unwrap_or(""))); }
            else { omitted += 1; }
        }
    }
    if grouped.is_empty() { return (raw.to_string(), 0); }
    let mut out = String::new();
    for (file, lines) in grouped.into_iter().take(40) {
        out.push_str(&file); out.push('\n');
        for line in lines { out.push_str("  "); out.push_str(&line); out.push('\n'); }
    }
    if omitted > 0 { out.push_str(&format!("… {} additional matches omitted; raw output retained by KITT\n", omitted)); }
    (out, omitted)
}

fn compress_build(raw: &str, success: bool) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().collect();
    let markers = ["error", "failed", "failure", "exception", "assert", "tests run", "test result", "build success", "build failure", "warning", "caused by", "traceback"];
    let mut selected = Vec::new();
    let mut seen = HashSet::new();
    for (i, line) in lines.iter().enumerate() {
        let lower = line.to_ascii_lowercase();
        if markers.iter().any(|m| lower.contains(m)) || (!success && lower.contains(" at ")) {
            let start = i.saturating_sub(1); let end = (i + 2).min(lines.len());
            for item in &lines[start..end] {
                if seen.insert((*item).to_string()) { selected.push((*item).to_string()); }
            }
        }
        if selected.len() >= 140 { break; }
    }
    if selected.is_empty() { selected.extend(lines.iter().rev().take(40).rev().map(|x| (*x).to_string())); }
    let omitted = lines.len().saturating_sub(selected.len());
    let mut out = selected.join("\n");
    if omitted > 0 { out.push_str(&format!("\n… {} routine lines omitted; raw output retained by KITT", omitted)); }
    (out, omitted)
}

fn compress_generic(raw: &str) -> (String, usize) {
    let lines: Vec<&str> = raw.lines().collect();
    if lines.len() <= 120 { return (raw.to_string(), 0); }
    let mut out = lines[..70].join("\n");
    out.push_str(&format!("\n… {} lines omitted …\n", lines.len() - 110));
    out.push_str(&lines[lines.len()-40..].join("\n"));
    (out, lines.len() - 110)
}

pub fn compress(argv: &[String], stdout: &str, stderr: &str, returncode: i32) -> CompressionResponse {
    let raw = if stderr.is_empty() { stdout.to_string() } else if stdout.is_empty() { stderr.to_string() } else { format!("{}\n{}", stdout, stderr) };
    if raw.is_empty() { return never_worse(&raw, raw.clone(), family(argv), 0); }
    let fam = family(argv);
    let (candidate, omitted) = match fam {
        "search" => compress_search(&raw),
        "build_test" | "diagnostics" | "infra" => compress_build(&raw, returncode == 0),
        "vcs" => if raw.lines().count() > 180 { compress_generic(&raw) } else { (raw.clone(), 0) },
        "listing" => if raw.lines().count() > 160 { compress_generic(&raw) } else { (raw.clone(), 0) },
        _ => compress_generic(&raw),
    };
    never_worse(&raw, candidate, fam, omitted)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn never_expands() {
        let raw=(0..400).map(|i| format!("noise {}", i)).collect::<Vec<_>>().join("\n");
        let r=compress(&["mvn".into(),"test".into()], &raw, "", 0);
        assert!(r.output.len() <= raw.len());
    }
}

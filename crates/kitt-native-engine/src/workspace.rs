use crate::model::{FileListResponse, FileReadResponse};
use anyhow::{Result, anyhow};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::UNIX_EPOCH;

const DEFAULT_MAX_FILE_BYTES: usize = 4 * 1024 * 1024;

fn estimated_tokens(text: &str) -> usize {
    text.chars().count().div_ceil(4)
}

fn validate_relative(relative: &str) -> Result<PathBuf> {
    let path = Path::new(relative);
    if path.is_absolute() {
        return Err(anyhow!("absolute paths are not allowed"));
    }
    let mut clean = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(value) => clean.push(value),
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(anyhow!("path traversal is not allowed"));
            }
        }
    }
    Ok(clean)
}

fn contained_existing(root: &Path, relative: &str) -> Result<(PathBuf, String)> {
    let clean = validate_relative(relative)?;
    let target = root.join(&clean);
    let mut cursor = root.to_path_buf();
    for component in clean.components() {
        cursor.push(component.as_os_str());
        let metadata = fs::symlink_metadata(&cursor)
            .map_err(|e| anyhow!("cannot inspect '{}': {e}", relative))?;
        if metadata.file_type().is_symlink() {
            return Err(anyhow!("symlink paths are not allowed"));
        }
    }
    let canonical = target
        .canonicalize()
        .map_err(|e| anyhow!("cannot resolve '{}': {e}", relative))?;
    if !canonical.starts_with(root) {
        return Err(anyhow!("path escapes repository root"));
    }
    let display = canonical
        .strip_prefix(root)
        .map_err(|_| anyhow!("path escapes repository root"))?
        .to_string_lossy()
        .replace('\\', "/");
    Ok((canonical, if display.is_empty() { ".".into() } else { display }))
}

fn mtime_ns(metadata: &fs::Metadata) -> u64 {
    metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|value| value.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or(0)
}

fn sha256_bytes(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

pub fn read_file(
    root: &Path,
    relative: &str,
    start_line: usize,
    end_line: Option<usize>,
    max_bytes: usize,
    token_budget: usize,
) -> Result<FileReadResponse> {
    let (path, display) = contained_existing(root, relative)?;
    let metadata = fs::metadata(&path)?;
    if !metadata.is_file() {
        return Err(anyhow!("path is not a regular file"));
    }

    if metadata.len() > DEFAULT_MAX_FILE_BYTES as u64 {
        return Err(anyhow!(
            "file exceeds read limit: {} > {} bytes",
            metadata.len(),
            DEFAULT_MAX_FILE_BYTES
        ));
    }

    let bytes = fs::read(&path)?;
    let full_file_hash = sha256_bytes(&bytes);
    let text = String::from_utf8_lossy(&bytes);
    let lines: Vec<&str> = text.lines().collect();
    let total_lines = lines.len();

    let start = start_line.max(1).saturating_sub(1).min(total_lines);
    let requested_end = end_line
        .unwrap_or(start.saturating_add(200))
        .max(start)
        .min(start.saturating_add(5000))
        .min(total_lines);

    let token_budget = token_budget.clamp(64, 32_000);
    let char_budget = token_budget.saturating_mul(4);
    let mut selected = Vec::new();
    let mut chars = 0usize;
    let mut cursor = start;

    while cursor < requested_end {
        let line = lines[cursor];
        let extra = line.chars().count() + usize::from(!selected.is_empty());
        if !selected.is_empty() && chars.saturating_add(extra) > char_budget {
            break;
        }
        if selected.is_empty() && extra > char_budget {
            let prefix: String = line.chars().take(char_budget).collect();
            selected.push(prefix);
            cursor += 1;
            break;
        }
        selected.push(line.to_string());
        chars += extra;
        cursor += 1;
    }

    let mut content = selected.join("\n");
    let returned_end = if cursor > start { cursor } else { start };
    let mut truncated = returned_end < requested_end || requested_end < total_lines;
    if max_bytes > 0 && content.len() > max_bytes {
        let mut keep = max_bytes;
        while keep > 0 && !content.is_char_boundary(keep) {
            keep -= 1;
        }
        content.truncate(keep);
        truncated = true;
    }
    let next_start_line = if truncated {
        Some(returned_end.saturating_add(1))
    } else {
        None
    };

    Ok(FileReadResponse {
        path: display,
        content_hash: sha256_bytes(content.as_bytes()),
        full_file_hash,
        content,
        start_line: start.saturating_add(1),
        end_line: returned_end,
        total_lines,
        omitted_lines: total_lines.saturating_sub(returned_end),
        next_start_line,
        estimated_tokens: estimated_tokens(&selected.join("\n")),
        file_size: metadata.len(),
        mtime_ns: mtime_ns(&metadata),
    })
}

pub fn list_files(
    root: &Path,
    relative: &str,
    limit: usize,
    token_budget: usize,
) -> Result<FileListResponse> {
    let (directory, _) = contained_existing(root, relative)?;
    if !directory.is_dir() {
        return Err(anyhow!("path is not a directory"));
    }

    let limit = limit.clamp(1, 500);
    let char_budget = token_budget.clamp(64, 8_000).saturating_mul(4);
    let mut candidates = Vec::new();

    for entry in fs::read_dir(&directory)? {
        let entry = match entry {
            Ok(value) => value,
            Err(_) => continue,
        };
        let file_type = match entry.file_type() {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !file_type.is_file() || file_type.is_symlink() {
            continue;
        }
        let canonical = match entry.path().canonicalize() {
            Ok(value) if value.starts_with(root) => value,
            _ => continue,
        };
        let relative = match canonical.strip_prefix(root) {
            Ok(value) => value.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };
        candidates.push(relative);
    }
    candidates.sort();

    let total = candidates.len();
    let mut files = Vec::new();
    let mut chars = 0usize;
    for value in candidates.into_iter().take(limit) {
        let extra = value.chars().count() + usize::from(!files.is_empty());
        if !files.is_empty() && chars.saturating_add(extra) > char_budget {
            break;
        }
        chars += extra;
        files.push(value);
    }

    let omitted = total.saturating_sub(files.len());
    let joined = files.join("\n");
    Ok(FileListResponse {
        files,
        omitted,
        estimated_tokens: estimated_tokens(&joined),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn temp_root() -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "kitt-native-workspace-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        root.canonicalize().unwrap()
    }

    #[test]
    fn read_is_token_bounded_and_contained() {
        let root = temp_root();
        let mut f = fs::File::create(root.join("many.txt")).unwrap();
        for i in 0..500 {
            writeln!(f, "line-{i:04} {}", "x".repeat(40)).unwrap();
        }
        let response = read_file(&root, "many.txt", 1, Some(500), 4 * 1024 * 1024, 64).unwrap();
        assert!(response.estimated_tokens <= 80);
        assert!(response.next_start_line.is_some());
        assert!(read_file(&root, "../outside", 1, None, 1024, 64).is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn list_is_sorted_and_bounded() {
        let root = temp_root();
        for name in ["c.py", "a.py", "b.py"] {
            fs::write(root.join(name), name).unwrap();
        }
        let response = list_files(&root, ".", 2, 64).unwrap();
        assert_eq!(response.files, vec!["a.py", "b.py"]);
        assert_eq!(response.omitted, 1);
        let _ = fs::remove_dir_all(root);
    }
}

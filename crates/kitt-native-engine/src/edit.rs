use crate::language::{grammar, identify};
use crate::model::{EditRequest, EditResponse};
use crate::symbols::read_symbol;
use anyhow::{Context, Result, anyhow};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::path::Path;
use tempfile::NamedTempFile;
use tree_sitter::Parser;

fn hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn validate(path: &Path, source: &[u8]) -> Result<()> {
    let Some(id) = identify(path) else {
        return Ok(());
    };
    let mut parser = Parser::new();
    parser.set_language(&grammar(id)?)?;
    let tree = parser
        .parse(source, None)
        .context("syntax parser returned no tree")?;
    if tree.root_node().has_error() {
        return Err(anyhow!("replacement introduces syntax errors"));
    }
    Ok(())
}

pub fn replace_symbol(root: &Path, request: EditRequest) -> Result<EditResponse> {
    let current =
        read_symbol(root, &request.symbol_id)?.ok_or_else(|| anyhow!("symbol not found"))?;
    if let Some(expected) = &request.expected_hash {
        if expected != &current.symbol.source_hash {
            return Err(anyhow!("optimistic edit conflict: symbol hash changed"));
        }
    }
    let path = root.join(&current.symbol.path);
    let metadata = fs::metadata(&path)?;
    let mut bytes = fs::read(&path)?;
    let old_hash = current.symbol.source_hash.clone();
    if current.source == request.replacement {
        return Ok(EditResponse {
            path: current.symbol.path,
            old_hash: old_hash.clone(),
            new_hash: old_hash,
            changed: false,
        });
    }
    bytes.splice(
        current.symbol.start_byte..current.symbol.end_byte,
        request.replacement.as_bytes().iter().copied(),
    );
    if request.validate_syntax {
        validate(&path, &bytes)?;
    }
    let parent = path.parent().ok_or_else(|| anyhow!("file has no parent"))?;
    let mut temp = NamedTempFile::new_in(parent)?;
    temp.write_all(&bytes)?;
    temp.flush()?;
    temp.as_file().sync_all()?;
    temp.as_file().set_permissions(metadata.permissions())?;
    temp.persist(&path)
        .map_err(|e| e.error)
        .with_context(|| format!("persist {}", path.display()))?;

    // Do not re-resolve the old symbol id after persistence: a valid structural
    // edit may intentionally rename or remove the symbol.  The edit is already
    // durable at this point, so reporting an error would be a false failure.
    let new_hash = hash(request.replacement.as_bytes());
    Ok(EditResponse {
        path: current.symbol.path,
        old_hash,
        new_hash,
        changed: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn rename_does_not_report_failure_after_persist() {
        let dir = tempdir().unwrap();
        let file = dir.path().join("sample.py");
        fs::write(&file, "def before():\n    return 1\n").unwrap();
        let current = read_symbol(dir.path(), "sample.py::before")
            .unwrap()
            .unwrap();
        let result = replace_symbol(
            dir.path(),
            EditRequest {
                symbol_id: "sample.py::before".to_string(),
                replacement: "def after():\n    return 2".to_string(),
                expected_hash: Some(current.symbol.source_hash),
                validate_syntax: true,
            },
        )
        .unwrap();
        assert!(result.changed);
        assert!(fs::read_to_string(file).unwrap().contains("def after"));
    }
}

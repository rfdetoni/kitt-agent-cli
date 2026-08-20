use anyhow::{anyhow, Result};
use std::path::Path;
use tree_sitter::Language;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LanguageId {
    Python,
    Java,
    JavaScript,
    TypeScript,
    Tsx,
    Rust,
    Go,
}

pub fn identify(path: &Path) -> Option<LanguageId> {
    match path
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "py" => Some(LanguageId::Python),
        "java" => Some(LanguageId::Java),
        "js" | "jsx" | "mjs" | "cjs" => Some(LanguageId::JavaScript),
        "ts" => Some(LanguageId::TypeScript),
        "tsx" => Some(LanguageId::Tsx),
        "rs" => Some(LanguageId::Rust),
        "go" => Some(LanguageId::Go),
        _ => None,
    }
}

pub fn grammar(id: LanguageId) -> Result<Language> {
    let lang = match id {
        LanguageId::Python => tree_sitter_python::LANGUAGE.into(),
        LanguageId::Java => tree_sitter_java::LANGUAGE.into(),
        LanguageId::JavaScript => tree_sitter_javascript::LANGUAGE.into(),
        LanguageId::TypeScript => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
        LanguageId::Tsx => tree_sitter_typescript::LANGUAGE_TSX.into(),
        LanguageId::Rust => tree_sitter_rust::LANGUAGE.into(),
        LanguageId::Go => tree_sitter_go::LANGUAGE.into(),
    };
    Ok(lang)
}

pub fn symbol_kinds(id: LanguageId) -> &'static [&'static str] {
    match id {
        LanguageId::Python => &["class_definition", "function_definition"],
        LanguageId::Java => &[
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
            "method_declaration",
            "constructor_declaration",
        ],
        LanguageId::JavaScript | LanguageId::TypeScript | LanguageId::Tsx => &[
            "class_declaration",
            "function_declaration",
            "method_definition",
            "interface_declaration",
            "type_alias_declaration",
            "lexical_declaration",
        ],
        LanguageId::Rust => &[
            "function_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "impl_item",
            "type_item",
        ],
        LanguageId::Go => &[
            "function_declaration",
            "method_declaration",
            "type_declaration",
        ],
    }
}

pub fn kind_label(node_kind: &str) -> String {
    node_kind
        .trim_end_matches("_declaration")
        .trim_end_matches("_definition")
        .trim_end_matches("_item")
        .replace('_', " ")
}

pub fn name_of<'a>(node: tree_sitter::Node<'a>, source: &'a [u8]) -> Option<String> {
    for field in ["name", "declarator", "type"] {
        if let Some(n) = node.child_by_field_name(field) {
            let text = n.utf8_text(source).ok()?.trim();
            if !text.is_empty() {
                let token = text
                    .split(|c: char| c.is_whitespace() || c == '(' || c == '<' || c == '=')
                    .next()
                    .unwrap_or(text);
                if !token.is_empty() {
                    return Some(token.to_string());
                }
            }
        }
    }
    // Some declaration wrappers keep the useful name in a named child.
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        if matches!(
            child.kind(),
            "identifier" | "type_identifier" | "property_identifier"
        ) {
            let text = child
                .utf8_text(source)
                .map_err(|_| anyhow!("invalid utf8"))
                .ok()?;
            if !text.is_empty() {
                return Some(text.to_string());
            }
        }
    }
    None
}

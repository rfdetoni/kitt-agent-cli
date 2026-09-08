use kitt_native_engine::model::{EditRequest, SearchOptions};
use kitt_native_engine::{
    NativeEngine, compress_process_output_with_budget,
};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

fn json<T: serde::Serialize>(value: &T) -> PyResult<String> {
    serde_json::to_string(value).map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

#[pyclass]
struct Engine {
    inner: NativeEngine,
}

#[pymethods]
impl Engine {
    #[new]
    fn new(root: String) -> PyResult<Self> {
        NativeEngine::new(root)
            .map(|inner| Self { inner })
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }

    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature=(query, regex=false, case_sensitive=false, max_results=50, max_per_file=8, context_lines=1, token_budget=1200))]
    fn search(
        &self,
        query: String,
        regex: bool,
        case_sensitive: bool,
        max_results: usize,
        max_per_file: usize,
        context_lines: usize,
        token_budget: usize,
    ) -> PyResult<String> {
        let options = SearchOptions {
            regex,
            case_sensitive,
            max_results,
            max_per_file,
            context_lines,
            token_budget,
            include_hidden: false,
        };
        json(
            &self
                .inner
                .search(&query, options)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    #[pyo3(signature=(query, limit=50))]
    fn find_symbols(&self, query: String, limit: usize) -> PyResult<String> {
        json(
            &self
                .inner
                .find_symbols(&query, limit)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    fn read_symbol(&self, symbol_id: String) -> PyResult<String> {
        json(
            &self
                .inner
                .read_symbol(&symbol_id)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    #[pyo3(signature=(symbol_id, limit=100))]
    fn references(&self, symbol_id: String, limit: usize) -> PyResult<String> {
        json(
            &self
                .inner
                .references(&symbol_id, limit)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    #[pyo3(signature=(max_symbols=10000))]
    fn dependency_edges(&self, max_symbols: usize) -> PyResult<String> {
        json(
            &self
                .inner
                .dependency_edges(max_symbols)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    #[pyo3(signature=(symbol_id, replacement, expected_hash=None, validate_syntax=true))]
    fn replace_symbol(
        &self,
        symbol_id: String,
        replacement: String,
        expected_hash: Option<String>,
        validate_syntax: bool,
    ) -> PyResult<String> {
        let request = EditRequest {
            symbol_id,
            replacement,
            expected_hash,
            validate_syntax,
        };
        json(
            &self
                .inner
                .replace_symbol(request)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    #[pyo3(signature=(path, start_line=1, end_line=None, max_bytes=4194304, token_budget=1200))]
    fn read_file(
        &self,
        path: String,
        start_line: usize,
        end_line: Option<usize>,
        max_bytes: usize,
        token_budget: usize,
    ) -> PyResult<String> {
        json(
            &self
                .inner
                .read_file(&path, start_line, end_line, max_bytes, token_budget)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }

    #[pyo3(signature=(path, limit=100, token_budget=600))]
    fn list_files(
        &self,
        path: String,
        limit: usize,
        token_budget: usize,
    ) -> PyResult<String> {
        json(
            &self
                .inner
                .list_files(&path, limit, token_budget)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?,
        )
    }
}

#[pyfunction]
#[pyo3(signature=(argv, stdout, stderr, returncode, token_budget=1200))]
fn compress_output(
    argv: Vec<String>,
    stdout: String,
    stderr: String,
    returncode: i32,
    token_budget: usize,
) -> PyResult<String> {
    json(&compress_process_output_with_budget(
        &argv,
        &stdout,
        &stderr,
        returncode,
        token_budget,
    ))
}

#[pymodule]
fn kitt_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Engine>()?;
    m.add_function(wrap_pyfunction!(compress_output, m)?)?;
    m.add("ENGINE_VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

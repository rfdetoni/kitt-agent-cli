"""A small, deterministic interpreter for a safe subset of Python.

This module intentionally never calls ``eval`` or ``exec``.  It is used by a
separate worker process so model supplied source code cannot directly reach
Python builtins, imports, the filesystem, the network, or subprocess APIs.
"""

from __future__ import annotations

import ast
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


class SafePythonError(Exception):
    """Base error returned for rejected or failed safe-Python programs."""


class SafePythonValidationError(SafePythonError):
    """The source uses syntax or capabilities outside the safe subset."""


class SafePythonLimitError(SafePythonError):
    """The program exceeded a configured execution limit."""


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


@dataclass(frozen=True)
class SafeRuntimeLimits:
    max_steps: int = 50_000
    max_ast_nodes: int = 4_000
    max_collection_items: int = 10_000
    max_output_chars: int = 32_768
    max_value_bytes: int = 8 * 1024 * 1024
    timeout_seconds: float = 2.0


@dataclass(frozen=True)
class SafeRuntimeResult:
    stdout: str
    result: Any
    steps: int
    truncated: bool


class _SafeModule:
    def __init__(self, name: str, members: Mapping[str, Any]):
        self.name = name
        self.members = dict(members)


@dataclass(frozen=True)
class _SafeMethod:
    owner: Any
    name: str


def _bounded_range(max_items: int) -> Callable[..., range]:
    def safe_range(*args: int) -> range:
        value = range(*args)
        if len(value) > max_items:
            raise SafePythonLimitError(
                f"range contains {len(value)} items; limit is {max_items}."
            )
        return value

    return safe_range


def _json_size(value: Any, limit: int) -> int:
    """Return a conservative serialized size while rejecting cyclic values."""
    try:
        raw = json.dumps(_to_jsonable(value), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise SafePythonValidationError(f"Value cannot be represented safely: {exc}") from exc
    size = len(raw.encode("utf-8"))
    if size > limit:
        raise SafePythonLimitError(f"Runtime values use {size} bytes; limit is {limit}.")
    return size


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value
    if isinstance(value, (Decimal, Fraction)):
        return str(value)
    if isinstance(value, (list, tuple, range)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, set):
        return [_to_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    raise SafePythonValidationError(f"Unsupported result type: {type(value).__name__}")


class SafePythonInterpreter:
    """Interpret a deliberately small Python subset with explicit capabilities."""

    _BINARY_OPERATORS: Dict[type, Callable[[Any, Any], Any]] = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a**b,
        ast.BitAnd: lambda a, b: a & b,
        ast.BitOr: lambda a, b: a | b,
        ast.BitXor: lambda a, b: a ^ b,
    }
    _UNARY_OPERATORS: Dict[type, Callable[[Any], Any]] = {
        ast.UAdd: lambda value: +value,
        ast.USub: lambda value: -value,
        ast.Not: lambda value: not value,
        ast.Invert: lambda value: ~value,
    }
    _COMPARE_OPERATORS: Dict[type, Callable[[Any, Any], bool]] = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
        ast.Is: lambda a, b: a is b,
        ast.IsNot: lambda a, b: a is not b,
    }
    _STRING_METHODS = {
        "capitalize", "casefold", "count", "endswith", "find", "index",
        "isalnum", "isalpha", "isdigit", "islower", "isspace", "istitle",
        "isupper", "join", "lower", "lstrip", "partition", "replace",
        "rfind", "rindex", "rpartition", "rsplit", "rstrip", "split",
        "splitlines", "startswith", "strip", "swapcase", "title", "upper",
    }
    _LIST_METHODS = {"append", "clear", "copy", "count", "extend", "index", "insert", "pop", "remove", "reverse", "sort"}
    _DICT_METHODS = {"clear", "copy", "get", "items", "keys", "pop", "setdefault", "update", "values"}
    _SET_METHODS = {"add", "clear", "copy", "difference", "discard", "intersection", "issubset", "issuperset", "remove", "union", "update"}

    def __init__(self, limits: Optional[SafeRuntimeLimits] = None):
        self.limits = limits or SafeRuntimeLimits()
        self.steps = 0
        self.deadline = 0.0
        self.environment: Dict[str, Any] = {}
        self.output: List[str] = []
        self.output_chars = 0
        self.output_truncated = False
        self._builtins = self._build_builtins()
        self._modules = self._build_modules()

    def _build_builtins(self) -> Dict[str, Callable[..., Any]]:
        return {
            "abs": abs,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "print": self._safe_print,
            "range": _bounded_range(self.limits.max_collection_items),
            "reversed": reversed,
            "round": round,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "zip": zip,
            "Decimal": Decimal,
            "Fraction": Fraction,
        }

    @staticmethod
    def _build_modules() -> Dict[str, _SafeModule]:
        return {
            "math": _SafeModule("math", {
                name: getattr(math, name)
                for name in (
                    "ceil", "comb", "copysign", "e", "exp", "fabs", "factorial",
                    "floor", "fmod", "frexp", "fsum", "gcd", "hypot", "isclose",
                    "isfinite", "isinf", "isnan", "isqrt", "lcm", "ldexp", "log",
                    "log10", "log2", "modf", "perm", "pi", "pow", "prod", "remainder",
                    "sqrt", "tau", "trunc",
                )
            }),
            "statistics": _SafeModule("statistics", {
                name: getattr(statistics, name)
                for name in (
                    "fmean", "geometric_mean", "harmonic_mean", "mean", "median",
                    "median_grouped", "median_high", "median_low", "mode", "multimode",
                    "pstdev", "pvariance", "quantiles", "stdev", "variance",
                )
            }),
            "json": _SafeModule("json", {
                "dumps": lambda value: json.dumps(_to_jsonable(value), ensure_ascii=False),
                "loads": json.loads,
            }),
        }

    def execute(self, code: str, inputs: Optional[Mapping[str, Any]] = None, result_var: str = "_result") -> SafeRuntimeResult:
        if not isinstance(code, str):
            raise SafePythonValidationError("code must be a string.")
        if not result_var.isidentifier() or result_var.startswith("__"):
            raise SafePythonValidationError("result_var must be a safe identifier.")

        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise SafePythonValidationError(f"Syntax error at line {exc.lineno}: {exc.msg}") from exc

        node_count = sum(1 for _ in ast.walk(tree))
        if node_count > self.limits.max_ast_nodes:
            raise SafePythonLimitError(f"AST has {node_count} nodes; limit is {self.limits.max_ast_nodes}.")

        self.steps = 0
        self.deadline = time.monotonic() + self.limits.timeout_seconds
        self.output = []
        self.output_chars = 0
        self.output_truncated = False
        self.environment = {"inputs": _to_jsonable(dict(inputs or {}))}
        self.environment.update(self._modules)

        for statement in tree.body:
            self._exec_statement(statement)
            self._check_environment_size()

        result = self.environment.get(result_var)
        _json_size(result, self.limits.max_value_bytes)
        return SafeRuntimeResult(
            stdout="".join(self.output),
            result=_to_jsonable(result),
            steps=self.steps,
            truncated=self.output_truncated,
        )

    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.limits.max_steps:
            raise SafePythonLimitError(f"Step limit exceeded ({self.limits.max_steps}).")
        if time.monotonic() > self.deadline:
            raise SafePythonLimitError("Execution deadline exceeded.")

    def _check_environment_size(self) -> None:
        visible = {key: value for key, value in self.environment.items() if not isinstance(value, _SafeModule)}
        _json_size(visible, self.limits.max_value_bytes)

    def _safe_print(self, *values: Any, sep: str = " ", end: str = "\n") -> None:
        text = sep.join(str(_to_jsonable(value)) for value in values) + end
        remaining = self.limits.max_output_chars - self.output_chars
        if remaining <= 0:
            self.output_truncated = True
            return
        if len(text) > remaining:
            self.output.append(text[:remaining])
            self.output_chars += remaining
            self.output_truncated = True
            return
        self.output.append(text)
        self.output_chars += len(text)

    def _exec_statement(self, node: ast.stmt) -> None:
        self._tick()
        if isinstance(node, ast.Expr):
            self._eval(node.value)
            return
        if isinstance(node, ast.Assign):
            value = self._eval(node.value)
            for target in node.targets:
                self._assign(target, value)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                raise SafePythonValidationError("Annotation-only assignments are not supported.")
            self._assign(node.target, self._eval(node.value))
            return
        if isinstance(node, ast.AugAssign):
            current = self._read_target(node.target)
            operator = self._BINARY_OPERATORS.get(type(node.op))
            if operator is None:
                raise SafePythonValidationError(f"Operator {type(node.op).__name__} is not allowed.")
            self._assign(node.target, operator(current, self._eval(node.value)))
            return
        if isinstance(node, ast.If):
            branch = node.body if self._eval(node.test) else node.orelse
            for statement in branch:
                self._exec_statement(statement)
            return
        if isinstance(node, ast.For):
            iterable = self._materialize_iterable(self._eval(node.iter))
            for item in iterable:
                self._tick()
                self._assign(node.target, item)
                try:
                    for statement in node.body:
                        self._exec_statement(statement)
                except _ContinueSignal:
                    continue
                except _BreakSignal:
                    break
            else:
                for statement in node.orelse:
                    self._exec_statement(statement)
            return
        if isinstance(node, ast.Break):
            raise _BreakSignal()
        if isinstance(node, ast.Continue):
            raise _ContinueSignal()
        if isinstance(node, ast.Pass):
            return
        raise SafePythonValidationError(f"Statement {type(node).__name__} is not allowed.")

    def _eval(self, node: ast.AST) -> Any:
        self._tick()
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (type(None), bool, int, float, str)):
                return node.value
            raise SafePythonValidationError(f"Constant {type(node.value).__name__} is not allowed.")
        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                raise SafePythonValidationError("Dunder names are not allowed.")
            if node.id in self.environment:
                return self.environment[node.id]
            if node.id in self._builtins:
                return self._builtins[node.id]
            raise SafePythonValidationError(f"Unknown name: {node.id}")
        if isinstance(node, ast.List):
            return self._bounded_collection([self._eval(item) for item in node.elts])
        if isinstance(node, ast.Tuple):
            return tuple(self._bounded_collection([self._eval(item) for item in node.elts]))
        if isinstance(node, ast.Set):
            return set(self._bounded_collection([self._eval(item) for item in node.elts]))
        if isinstance(node, ast.Dict):
            result = {self._eval(key): self._eval(value) for key, value in zip(node.keys, node.values)}
            self._ensure_collection_limit(result)
            return result
        if isinstance(node, ast.BinOp):
            operator = self._BINARY_OPERATORS.get(type(node.op))
            if operator is None:
                raise SafePythonValidationError(f"Operator {type(node.op).__name__} is not allowed.")
            left, right = self._eval(node.left), self._eval(node.right)
            if isinstance(node.op, ast.Pow) and isinstance(right, int) and abs(right) > 10_000:
                raise SafePythonLimitError("Exponent is too large.")
            value = operator(left, right)
            self._ensure_value_shape(value)
            return value
        if isinstance(node, ast.UnaryOp):
            operator = self._UNARY_OPERATORS.get(type(node.op))
            if operator is None:
                raise SafePythonValidationError(f"Operator {type(node.op).__name__} is not allowed.")
            return operator(self._eval(node.operand))
        if isinstance(node, ast.BoolOp):
            values = node.values
            current = self._eval(values[0])
            for candidate in values[1:]:
                if isinstance(node.op, ast.And) and not current:
                    return current
                if isinstance(node.op, ast.Or) and current:
                    return current
                current = self._eval(candidate)
            return current
        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            for operator_node, comparator in zip(node.ops, node.comparators):
                operator = self._COMPARE_OPERATORS.get(type(operator_node))
                if operator is None:
                    raise SafePythonValidationError(f"Comparison {type(operator_node).__name__} is not allowed.")
                right = self._eval(comparator)
                if not operator(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test) else node.orelse)
        if isinstance(node, ast.Subscript):
            owner = self._eval(node.value)
            key = self._eval_slice(node.slice)
            return owner[key]
        if isinstance(node, ast.Attribute):
            return self._resolve_attribute(self._eval(node.value), node.attr)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.ListComp):
            return self._bounded_collection(self._eval_comprehension(node.elt, node.generators))
        if isinstance(node, ast.SetComp):
            return set(self._bounded_collection(self._eval_comprehension(node.elt, node.generators)))
        if isinstance(node, ast.DictComp):
            pairs = self._eval_comprehension_pair(node.key, node.value, node.generators)
            result = dict(pairs)
            self._ensure_collection_limit(result)
            return result
        if isinstance(node, ast.JoinedStr):
            return "".join(str(self._eval(value)) for value in node.values)
        if isinstance(node, ast.FormattedValue):
            value = self._eval(node.value)
            if node.format_spec is not None:
                spec = self._eval(node.format_spec)
                return format(value, spec)
            return repr(value) if node.conversion == 114 else str(value)
        raise SafePythonValidationError(f"Expression {type(node).__name__} is not allowed.")

    def _call(self, node: ast.Call) -> Any:
        if any(keyword.arg is None for keyword in node.keywords):
            raise SafePythonValidationError("**kwargs expansion is not allowed.")
        function = self._eval(node.func)
        args = [self._eval(arg) for arg in node.args]
        kwargs = {keyword.arg: self._eval(keyword.value) for keyword in node.keywords}
        if len(args) + len(kwargs) > 32:
            raise SafePythonLimitError("Too many call arguments.")

        if isinstance(function, _SafeMethod):
            method = getattr(function.owner, function.name)
            value = method(*args, **kwargs)
        elif function in self._builtins.values() or any(function in module.members.values() for module in self._modules.values()):
            value = function(*args, **kwargs)
        else:
            raise SafePythonValidationError("Calling this object is not allowed.")
        self._ensure_value_shape(value)
        return value

    def _resolve_attribute(self, owner: Any, name: str) -> Any:
        if name.startswith("_"):
            raise SafePythonValidationError("Private and dunder attributes are not allowed.")
        if isinstance(owner, _SafeModule):
            if name not in owner.members:
                raise SafePythonValidationError(f"{owner.name}.{name} is not allowed.")
            return owner.members[name]
        allowed: Iterable[str]
        if isinstance(owner, str):
            allowed = self._STRING_METHODS
        elif isinstance(owner, list):
            allowed = self._LIST_METHODS
        elif isinstance(owner, dict):
            allowed = self._DICT_METHODS
        elif isinstance(owner, set):
            allowed = self._SET_METHODS
        else:
            raise SafePythonValidationError(f"Attributes on {type(owner).__name__} are not allowed.")
        if name not in allowed:
            raise SafePythonValidationError(f"Method {type(owner).__name__}.{name} is not allowed.")
        return _SafeMethod(owner, name)

    def _eval_slice(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Slice):
            return slice(
                self._eval(node.lower) if node.lower else None,
                self._eval(node.upper) if node.upper else None,
                self._eval(node.step) if node.step else None,
            )
        return self._eval(node)

    def _assign(self, target: ast.AST, value: Any) -> None:
        if isinstance(target, ast.Name):
            if target.id.startswith("__") or target.id in self._modules or target.id in self._builtins:
                raise SafePythonValidationError(f"Assignment to reserved name {target.id} is not allowed.")
            self.environment[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            values = list(value)
            if len(values) != len(target.elts):
                raise SafePythonValidationError("Unpack target and value lengths differ.")
            for item_target, item_value in zip(target.elts, values):
                self._assign(item_target, item_value)
            return
        if isinstance(target, ast.Subscript):
            owner = self._eval(target.value)
            if not isinstance(owner, (list, dict)):
                raise SafePythonValidationError("Only list/dict item assignment is allowed.")
            owner[self._eval_slice(target.slice)] = value
            self._ensure_value_shape(owner)
            return
        raise SafePythonValidationError(f"Assignment target {type(target).__name__} is not allowed.")

    def _read_target(self, target: ast.AST) -> Any:
        if isinstance(target, ast.Name):
            return self._eval(target)
        if isinstance(target, ast.Subscript):
            return self._eval(target)
        raise SafePythonValidationError("Augmented assignment target is not allowed.")

    def _eval_comprehension(self, element: ast.AST, generators: List[ast.comprehension]) -> List[Any]:
        output: List[Any] = []

        def visit(index: int) -> None:
            if index == len(generators):
                output.append(self._eval(element))
                self._ensure_collection_limit(output)
                return
            generator = generators[index]
            if generator.is_async:
                raise SafePythonValidationError("Async comprehensions are not allowed.")
            for item in self._materialize_iterable(self._eval(generator.iter)):
                self._tick()
                self._assign(generator.target, item)
                if all(self._eval(condition) for condition in generator.ifs):
                    visit(index + 1)

        visit(0)
        return output

    def _eval_comprehension_pair(self, key: ast.AST, value: ast.AST, generators: List[ast.comprehension]) -> List[Any]:
        marker = ast.Tuple(elts=[key, value], ctx=ast.Load())
        return [tuple(pair) for pair in self._eval_comprehension(marker, generators)]

    def _materialize_iterable(self, value: Any) -> List[Any]:
        if not isinstance(value, (list, tuple, set, dict, range, str, zip, enumerate, reversed)):
            raise SafePythonValidationError(f"Iteration over {type(value).__name__} is not allowed.")
        items = list(value)
        self._ensure_collection_limit(items)
        return items

    def _bounded_collection(self, values: List[Any]) -> List[Any]:
        self._ensure_collection_limit(values)
        return values

    def _ensure_collection_limit(self, value: Any) -> None:
        if len(value) > self.limits.max_collection_items:
            raise SafePythonLimitError(
                f"Collection contains {len(value)} items; limit is {self.limits.max_collection_items}."
            )

    def _ensure_value_shape(self, value: Any) -> None:
        if isinstance(value, (str, bytes)) and len(value) > self.limits.max_value_bytes:
            raise SafePythonLimitError("String/bytes value exceeds the memory limit.")
        if isinstance(value, (list, tuple, set, dict, range)):
            self._ensure_collection_limit(value)


def execute_safe_python(
    code: str,
    inputs: Optional[Mapping[str, Any]] = None,
    result_var: str = "_result",
    limits: Optional[SafeRuntimeLimits] = None,
) -> SafeRuntimeResult:
    return SafePythonInterpreter(limits).execute(code, inputs=inputs, result_var=result_var)

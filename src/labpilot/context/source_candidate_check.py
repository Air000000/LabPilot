"""Trusted, Linux-only bounded AST checker. Never imports candidate modules."""

import ast
import json
import resource
import sys
import warnings


def protected_parts(source: bytes) -> tuple[bytes, bytes]:
    if len(source) > 128 * 1024:
        raise ValueError("source_limit")
    text = source.decode("utf-8", "strict")
    tree = ast.parse(text)
    # Compilation validates scope rules; the resulting code object is never executed.
    compile(tree, "train.py", "exec", dont_inherit=True)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "train_model"
    ]
    if len(functions) != 1 or functions[0] not in tree.body:
        raise ValueError("unsupported_layout")
    function = functions[0]
    if not isinstance(function, ast.FunctionDef) or function.body[0].lineno <= function.lineno:
        raise ValueError("unsupported_layout")
    if function.body[0].col_offset != 4 or function.end_lineno is None:
        raise ValueError("unsupported_layout")
    lines = source.splitlines(keepends=True)
    return b"".join(lines[: function.body[0].lineno - 1]), b"".join(lines[function.end_lineno :])


def main() -> int:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    warnings.simplefilter("ignore", SyntaxWarning)
    try:
        payload = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
        before = bytes.fromhex(payload["before"])
        after = bytes.fromhex(payload["after"])
        if protected_parts(before) != protected_parts(after):
            raise ValueError("protected_source_changed")
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        SyntaxError,
        RecursionError,
        MemoryError,
    ):
        print('{"status":"rejected"}')
        return 1
    print('{"status":"static_scope_passed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

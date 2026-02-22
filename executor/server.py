"""
Executor sidecar — sandboxed Python code execution service.

Runs as a separate Docker container with:
  - No internet access (internal Docker network only)
  - Read-only filesystem (tmpfs for /tmp)
  - 256MB RAM / 0.5 CPU hard limits
  - Non-root user (UID 1001)
  - multiprocessing.Process timeout (signal.alarm is main-thread only)

The main API container calls POST /execute with the code and file_path.
The executor loads the DataFrame from the shared volume, runs the code,
and returns stdout + result dict.
"""
from __future__ import annotations

import io
import json
import math
import multiprocessing
import os
import sys
import traceback
from typing import Any

import flask
import numpy as np
import pandas as pd

app = flask.Flask(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data/uploads")


# ── Whitelisted import gate ───────────────────────────────────────────────────

_ALLOWED_IMPORTS = frozenset({
    "pandas", "numpy", "json", "math", "statistics", "datetime", "collections",
    "itertools", "functools", "re", "string", "decimal", "fractions",
    "random", "calendar", "operator", "io",
})


def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
    top = name.split(".")[0]
    if top not in _ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in the sandbox.")
    import importlib
    return importlib.import_module(name)


# ── Safe builtins — defined AFTER _safe_import so it can be referenced ────────

_SAFE_BUILTINS = {
    "print": print,
    "range": range,
    "len": len,
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "type": type,
    "isinstance": isinstance,
    "hasattr": hasattr,
    "getattr": getattr,
    "vars": vars,
    "repr": repr,
    "format": format,
    "any": any,
    "all": all,
    "next": next,
    "iter": iter,
    "reversed": reversed,
    "True": True,
    "False": False,
    "None": None,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    # Allow imports — gated by _safe_import whitelist
    "__import__": _safe_import,
}


# ── Sanitizer ─────────────────────────────────────────────────────────────────

def _clean_value(v: Any) -> Any:
    """Recursively replace NaN/Inf and non-serializable values with safe equivalents."""
    import math
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, dict):
        return {str(k): _clean_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean_value(i) for i in v]
    # numpy scalar types
    try:
        import numpy as _np
        if isinstance(v, _np.floating):
            return None if (_np.isnan(v) or _np.isinf(v)) else float(v)
        if isinstance(v, _np.integer):
            return int(v)
        if isinstance(v, _np.ndarray):
            return [_clean_value(i) for i in v.tolist()]
    except ImportError:
        pass
    return v


def _sanitize_result(raw: dict) -> dict:
    """Recursively coerce non-JSON-serializable and NaN/Inf values to safe equivalents."""
    cleaned = {}
    for k, v in raw.items():
        safe = _clean_value(v)
        try:
            # allow_nan=False raises ValueError for NaN/Inf — final guard
            json.dumps(safe, allow_nan=False)
            cleaned[k] = safe
        except (TypeError, ValueError):
            cleaned[k] = str(v)
    return cleaned


# ── Worker (child process — true timeout isolation via .terminate()) ──────────

def _exec_worker(
    code: str,
    file_path: str | None,
    data_dir: str,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
) -> None:
    """
    Runs user code inside a child process.
    Communicates result back through a multiprocessing.Queue.
    Using a separate process (not thread) means we can .terminate() it
    on timeout — threads cannot be forcibly killed in Python.
    """
    import collections
    import datetime
    import importlib
    import json as _json
    import math as _math
    import statistics

    import numpy as _np
    import pandas as _pd

    namespace: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        # Pre-inject common data science libraries so Gemini-generated
        # code works even without an explicit import statement.
        "pd": _pd,
        "pandas": _pd,
        "np": _np,
        "numpy": _np,
        "json": _json,
        "math": _math,
        "statistics": statistics,
        "datetime": datetime,
        "collections": collections,
    }

    # Load DataFrame from shared volume
    if file_path:
        abs_path = (
            os.path.join(data_dir, os.path.basename(file_path))
            if not os.path.isabs(file_path)
            else file_path
        )
        if not os.path.exists(abs_path):
            result_queue.put({
                "stdout": "",
                "result": {},
                "error": f"FileNotFoundError: {abs_path} does not exist on executor",
            })
            return
        try:
            namespace["df"] = (
                _pd.read_parquet(abs_path)
                if abs_path.endswith(".parquet")
                else _pd.read_csv(abs_path)
            )
        except Exception as e:
            result_queue.put({
                "stdout": "",
                "result": {},
                "error": f"Failed to load dataset: {e}",
            })
            return

    # Capture stdout
    stdout_buf = io.StringIO()
    sys.stdout = stdout_buf

    result_dict: dict[str, Any] = {}
    error: str | None = None

    try:
        compiled = compile(code, "<user_code>", "exec")
        exec(compiled, namespace)  # noqa: S102

        if "result" in namespace and isinstance(namespace["result"], dict):
            result_dict = _sanitize_result(namespace["result"])

    except SyntaxError as e:
        error = f"SyntaxError: {e}"
    except Exception:
        error = traceback.format_exc()
    finally:
        sys.stdout = sys.__stdout__

    result_queue.put({
        "stdout": stdout_buf.getvalue(),
        "result": result_dict,
        "error": error,
    })


# ── Execute endpoint ──────────────────────────────────────────────────────────

@app.post("/execute")
def execute() -> flask.Response:
    payload: dict[str, Any] = flask.request.get_json(force=True)
    code: str = payload.get("code", "")
    file_path: str | None = payload.get("file_path")
    timeout: int = int(payload.get("timeout", 30))

    if not code.strip():
        return flask.jsonify({"stdout": "", "result": {}, "error": "No code provided"})

    result_queue: multiprocessing.Queue = multiprocessing.Queue()  # type: ignore[type-arg]
    proc = multiprocessing.Process(
        target=_exec_worker,
        args=(code, file_path, DATA_DIR, result_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join()
        return flask.jsonify({
            "stdout": "",
            "result": {},
            "error": (
                f"TimeoutError: Execution exceeded {timeout}s. "
                "Check for infinite loops or heavy computation."
            ),
        })

    try:
        result = result_queue.get_nowait()
    except Exception:
        result = {
            "stdout": "",
            "result": {},
            "error": "Executor worker exited unexpectedly (non-zero exit code).",
        }

    return flask.jsonify(result)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> flask.Response:
    return flask.jsonify({"status": "ok"})


if __name__ == "__main__":
    # "fork" is the default on Linux — fast, no re-import overhead.
    # Must be set before any Process is created.
    multiprocessing.set_start_method("fork")
    app.run(host="0.0.0.0", port=8080, debug=False)

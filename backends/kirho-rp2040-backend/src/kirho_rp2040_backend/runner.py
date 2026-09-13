"""Runner cooperativo para firmware MicroPython sencillo."""
from __future__ import annotations

import ast
import builtins
import sys
import threading

from .micropython_api import ExecutionStopped, MicroPythonApi


_ALLOWED_MODULES = frozenset({"machine", "time", "utime"})
_FORBIDDEN_NAMES = frozenset({
    "__import__", "compile", "eval", "exec", "input", "open",
})
_BUILTIN_NAMES = (
    "__build_class__", "abs", "all", "any", "AssertionError", "bool",
    "bytes", "bytearray", "callable", "chr", "dict", "divmod",
    "enumerate", "Exception", "filter", "float", "format",
    "frozenset", "hasattr", "int", "isinstance", "iter", "len", "list",
    "map", "max", "memoryview", "min", "next", "NotImplementedError",
    "object", "ord", "pow", "print", "property", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "StopIteration",
    "str", "sum", "super", "tuple", "type", "TypeError",
    "ValueError", "RuntimeError", "zip",
)


class _FirmwareValidator(ast.NodeVisitor):
    def visit_Import(self, node):
        for alias in node.names:
            if alias.name not in _ALLOWED_MODULES:
                raise ValueError(f"import not available in Kirho: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level or node.module not in _ALLOWED_MODULES:
            raise ValueError(f"import not available in Kirho: {node.module}")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in _FORBIDDEN_NAMES:
            raise ValueError(f"name not available in Kirho: {node.id}")
        self.generic_visit(node)


class MicroPythonRunner:
    """Ejecuta un subconjunto de MicroPython en un hilo detenible.

    Esto es compatibilidad de API sobre CPython, no un emulador ARM ni un
    sandbox de seguridad. El firmware debe tratarse como código confiable.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.api = None
        self.error = None
        self._stop_event = threading.Event()
        self._done = threading.Event()
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, source: str, filename: str = "<firmware>") -> None:
        if self.running:
            raise RuntimeError("MicroPython runner is already running")
        if not isinstance(source, str):
            raise TypeError("MicroPython source must be text")
        tree = ast.parse(source, filename=filename)
        _FirmwareValidator().visit(tree)
        self.stop()
        self.error = None
        self._stop_event.clear()
        self._done.clear()
        self.api = MicroPythonApi(self.runtime, self._stop_event)
        self._thread = threading.Thread(
            target=self._execute, args=(source, filename),
            name="kirho-rp2040-micropython", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> bool:
        if self._thread is None:
            return True
        self._stop_event.set()
        self._done.wait(timeout)
        return not self.running

    def wait(self, timeout: float | None = None) -> bool:
        """Espera y devuelve si el programa terminó dentro del límite."""
        self._done.wait(timeout)
        return not self.running

    def _execute(self, source: str, filename: str) -> None:
        try:
            sys.settrace(self._trace)
            namespace = self.api.globals()
            namespace["__builtins__"] = self._safe_builtins()
            code = compile(source, filename, "exec")
            exec(code, namespace, namespace)
        except ExecutionStopped:
            pass
        except BaseException as exc:  # firmware errors must reach the UI
            self.error = exc
        finally:
            sys.settrace(None)
            self._done.set()

    def _trace(self, frame, event, arg):
        if self._stop_event.is_set():
            raise ExecutionStopped("MicroPython runner stopped")
        return self._trace

    def _safe_builtins(self) -> dict:
        api = self.api

        def importer(name, globals=None, locals=None, fromlist=(), level=0):
            if level:
                raise ImportError("relative imports are not available in Kirho")
            return api.import_module(name)

        safe = {name: getattr(builtins, name) for name in _BUILTIN_NAMES}
        safe["__import__"] = importer
        return safe

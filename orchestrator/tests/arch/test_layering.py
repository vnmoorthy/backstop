"""Architecture-layering guard tests for the ``backstop`` package.

These tests statically walk every Python module under
``orchestrator/backstop`` with :mod:`ast` (no import side effects) and assert
the hexagonal-architecture invariants that keep the domain pure and PHI
contained:

(a) ``os.environ`` / ``os.getenv`` is referenced **only** from
    ``infra/config.py`` -- configuration is the single environment seam.
(b) **no** module under ``domain/``, ``ports/`` or ``services/`` imports a
    vendor SDK (``httpx``, ``torch``, ``boto3``, ``livekit``) -- the inner
    layers stay free of I/O libraries.

The scan is purely structural: it parses source into an AST and inspects
``Import`` / ``ImportFrom`` / ``Attribute`` nodes. It never executes the code
under test, so it works even before the implementation modules exist (such
directories simply contribute zero files and the asserts hold vacuously).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, NamedTuple, Set

import pytest

# --------------------------------------------------------------------------- #
# Locating the package root.
# --------------------------------------------------------------------------- #
# This test file lives at orchestrator/tests/arch/test_layering.py, so the
# package root ``backstop`` is two parents up, then ``backstop``.
_ORCHESTRATOR_ROOT: Path = Path(__file__).resolve().parents[2]
_BACKSTOP_ROOT: Path = _ORCHESTRATOR_ROOT / "backstop"

# Vendor SDKs / heavyweight runtime deps forbidden in the inner layers.
_FORBIDDEN_VENDOR_ROOTS: Set[str] = {"httpx", "torch", "boto3", "livekit"}

# Layer directories (relative to the package root) that must remain free of
# vendor SDK imports.
_INNER_LAYER_DIRS: Set[str] = {"domain", "ports", "services"}

# The hexagon this guard governs. The legacy v1 prototype modules
# (``server.py``, ``swarm.py``, ``integrations/``, ...) coexist with the new
# layered code during the parallel rebuild and are removed atomically in the
# integration milestone (M17); they are intentionally OUT of scope here so the
# guard polices only the v2 architecture it is meant to protect.
_HEXAGON_DIRS: Set[str] = {
    "domain",
    "ports",
    "adapters",
    "services",
    "controllers",
    "infra",
    "composition",
}
_ROOT_FILES: Set[str] = {"__init__.py", "app.py", "py.typed"}

# The single module allowed to read process environment.
_CONFIG_RELPATH: str = "infra/config.py"


def _in_scope(rel: str) -> bool:
    """Return ``True`` if a package-relative path is part of the v2 hexagon.

    Legacy top-level prototype modules and ``integrations/`` / ``pavo/`` are
    excluded; the guard governs only the layered rebuild.
    """
    return _top_level_layer(rel) in _HEXAGON_DIRS or rel in _ROOT_FILES


# --------------------------------------------------------------------------- #
# AST collection helpers.
# --------------------------------------------------------------------------- #
def _iter_python_files(root: Path) -> List[Path]:
    """Return every ``*.py`` file under *root* (recursively, sorted).

    Returns an empty list when *root* does not yet exist so the guard runs
    cleanly against a partially-scaffolded tree.
    """
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _parse(path: Path) -> ast.Module:
    """Parse *path* into an AST module, attributing the filename for errors."""
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def _relpath(path: Path) -> str:
    """Return *path* relative to the package root using POSIX separators."""
    return path.relative_to(_BACKSTOP_ROOT).as_posix()


def _top_level_layer(rel: str) -> str:
    """Return the first path component of a package-relative POSIX path."""
    return rel.split("/", 1)[0]


def _imported_roots(tree: ast.Module) -> Set[str]:
    """Collect the top-level package name of every import in *tree*.

    ``import torch.nn`` and ``from boto3.session import Session`` both yield
    their root package (``torch`` / ``boto3``). Relative imports
    (``from . import x``) have no root module and are ignored.
    """
    roots: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        # node.level > 0 means a relative import -> no absolute root module.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _references_os_environ(tree: ast.Module) -> bool:
    """Return ``True`` if *tree* reads the process environment.

    Detects both ``os.environ`` (attribute access) and ``os.getenv(...)`` /
    ``os.environ.get(...)`` call patterns, plus ``from os import environ`` /
    ``from os import getenv`` direct-name forms.
    """
    # Direct ``from os import environ|getenv`` brings the name into scope.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in {"environ", "getenv"}:
                    return True

    # ``os.environ`` / ``os.getenv`` attribute access.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
    return False


# --------------------------------------------------------------------------- #
# Module-scoped scan results (computed once, reused by every test).
# --------------------------------------------------------------------------- #
class _ScanResult(NamedTuple):
    """Aggregated structural findings from a single AST walk of the package."""

    files: List[Path]
    environ_offenders: List[str]  # package-relative paths reading os.environ
    vendor_offenders: List[str]  # "<relpath> imports <vendor>" entries


def _scan() -> _ScanResult:
    """Walk the package once and return the aggregated structural findings."""
    files = _iter_python_files(_BACKSTOP_ROOT)
    environ_offenders: List[str] = []
    vendor_offenders: List[str] = []

    for path in files:
        rel = _relpath(path)
        if not _in_scope(rel):
            continue  # legacy v1 modules are governed by their own removal (M17)
        tree = _parse(path)

        if _references_os_environ(tree) and rel != _CONFIG_RELPATH:
            environ_offenders.append(rel)

        if _top_level_layer(rel) in _INNER_LAYER_DIRS:
            for vendor in sorted(_imported_roots(tree) & _FORBIDDEN_VENDOR_ROOTS):
                vendor_offenders.append(f"{rel} imports {vendor}")

    return _ScanResult(
        files=files,
        environ_offenders=environ_offenders,
        vendor_offenders=vendor_offenders,
    )


@pytest.fixture(scope="module")
def scan() -> _ScanResult:
    """Provide the cached single-pass AST scan of the package."""
    return _scan()


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_package_root_exists() -> None:
    """The ``backstop`` package directory must exist to scan."""
    assert (
        _BACKSTOP_ROOT.is_dir()
    ), f"expected package root at {_BACKSTOP_ROOT}; the layering guard has nothing to walk"


def test_only_config_reads_os_environ(scan: _ScanResult) -> None:
    """``os.environ`` / ``os.getenv`` is confined to ``infra/config.py``."""
    offenders = scan.environ_offenders
    assert offenders == [], (
        "only backstop/infra/config.py may read the process environment; "
        f"these modules also do: {sorted(offenders)}"
    )


def test_inner_layers_import_no_vendor_sdk(scan: _ScanResult) -> None:
    """domain/ports/services import no httpx/torch/boto3/livekit."""
    offenders = scan.vendor_offenders
    assert offenders == [], (
        "domain/, ports/ and services/ must not import a vendor SDK "
        f"(httpx/torch/boto3/livekit); found: {sorted(offenders)}"
    )


@pytest.mark.parametrize("layer", sorted(_INNER_LAYER_DIRS))
def test_layer_dir_clean_when_present(scan: _ScanResult, layer: str) -> None:
    """Per-layer view of the vendor-import guard for sharper failures."""
    offenders = [
        entry
        for entry in scan.vendor_offenders
        if _top_level_layer(entry.split(" ", 1)[0]) == layer
    ]
    assert offenders == [], f"layer '{layer}/' imports a forbidden vendor SDK: {offenders}"

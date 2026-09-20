import tempfile
from pathlib import Path

from parity.scan.ast_import import scan_directory_for_imports
from parity.scan.manifest import get_declared_dependencies


def test_manifest_requirements_includes():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        (path / "requirements.txt").write_text("-r base.txt\nrequests==2.31.0\n", encoding="utf-8")
        (path / "base.txt").write_text("urllib3>=1.25\n-c constraints.txt\n", encoding="utf-8")
        (path / "constraints.txt").write_text("flask<3.0\n", encoding="utf-8")
        
        data = get_declared_dependencies(path)
        assert set(data.runtime.keys()) == {"requests", "urllib3", "flask"}
        assert set(data.dev.keys()) == set()

def test_manifest_setup_py():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        (path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        setup_content = """
from setuptools import setup
import sys

_RUNTIME = ["httpx", "rich"]
_DEV = ["pytest"]

setup(
    name="test",
    install_requires=_RUNTIME + ["click"],
    extras_require={
        "dev": _DEV
    }
)
"""
        (path / "setup.py").write_text(setup_content, encoding="utf-8")
        
        data = get_declared_dependencies(path)
        assert "httpx" in data.runtime
        assert "rich" in data.runtime
        assert "click" in data.runtime
        assert "requests" in data.runtime
        assert "pytest" in data.dev

def test_ast_import_scanner():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        
        # main file
        (path / "main.py").write_text("import requests\nfrom flask import Flask\nimport os\n", encoding="utf-8")
        
        # optional imports
        (path / "optional.py").write_text(
"""
import sys
try:
    import pandas
except ImportError:
    pandas = None

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import numpy
""", encoding="utf-8")

        # dev import
        tests = path / "tests"
        tests.mkdir()
        (tests / "test_main.py").write_text("import pytest\nimport httpx\n", encoding="utf-8")
        
        data = scan_directory_for_imports(path)
        
        assert set(data.runtime.keys()) == {"requests", "flask"}
        assert set(data.dev.keys()) == {"pytest", "httpx"}
        assert set(data.optional.keys()) == {"pandas", "numpy"}

def test_ast_syntax_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        (path / "bad.py").write_text("def my_func(:\n    pass", encoding="utf-8")
        
        data = scan_directory_for_imports(path)
        assert len(data.syntax_errors) == 1
        assert "bad.py" in data.syntax_errors[0]
        assert "invalid syntax" in data.syntax_errors[0] or "typo" in data.syntax_errors[0]

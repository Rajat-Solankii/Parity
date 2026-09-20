import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pathspec


@dataclass
class ImportData:
    runtime: set[str] = field(default_factory=set)
    dev: set[str] = field(default_factory=set)
    optional: set[str] = field(default_factory=set)
    syntax_errors: list[str] = field(default_factory=list)

    def merge(self, other: 'ImportData'):
        self.runtime.update(other.runtime)
        self.dev.update(other.dev)
        self.optional.update(other.optional)
        self.syntax_errors.extend(other.syntax_errors)

def scan_directory_for_imports(project_path: Path) -> ImportData:
    """
    Recursively scan all Python files in the directory for imported modules.
    Ignores stdlib, first-party modules, and excluded directories.
    Tags imports as runtime, dev, or optional.
    """
    data = ImportData()
    project_path = project_path.resolve()
    
    ignore_dirs = {
        ".git", ".hg", ".tox", ".nox", "node_modules",
        "__pycache__", "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache"
    }

    # Build pathspec from .gitignore if present
    gitignore_path = project_path / ".gitignore"
    gitignore_spec = None
    if gitignore_path.is_file():
        try:
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()
            gitignore_spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, lines)
        except Exception:
            pass

    # Discover first-party top-level modules
    first_party = _discover_first_party(project_path)
    
    # Python 3.10+
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else _get_fallback_stdlib()

    for py_file in _walk_python_files(project_path, ignore_dirs, gitignore_spec, project_path):
        is_dev = _is_dev_file(py_file, project_path)
        _scan_file(py_file, data, is_dev, first_party, stdlib)
        
    return data

def _discover_first_party(project_path: Path) -> set[str]:
    """Find top-level packages and modules in the project."""
    first_party = set()
    
    dirs_to_check = [project_path]
    src_dir = project_path / "src"
    if src_dir.is_dir():
        dirs_to_check.append(src_dir)
        
    for d in dirs_to_check:
        if not d.is_dir():
            continue
        for item in d.iterdir():
            if item.is_dir():
                if (item / "__init__.py").is_file():
                    first_party.add(item.name)
            elif item.is_file() and item.suffix == ".py":
                first_party.add(item.stem)
                
    return first_party

def _is_dev_file(py_file: Path, project_path: Path) -> bool:
    """Check if the file is a test/docs file."""
    try:
        rel_parts = py_file.relative_to(project_path).parts
        if "tests" in rel_parts or "test" in rel_parts or "docs" in rel_parts:
            return True
        if py_file.name == "conftest.py" or py_file.name.startswith("test_"):
            return True
    except ValueError:
        pass
    return False

def _walk_python_files(directory: Path, ignore_dirs: set[str], spec: pathspec.PathSpec | None, root: Path):
    try:
        for item in directory.iterdir():
            if item.name.endswith(".egg-info"):
                continue
                
            # Check gitignore
            if spec:
                try:
                    rel_path = str(item.relative_to(root).as_posix())
                    if spec.match_file(rel_path):
                        continue
                except ValueError:
                    pass

            if item.is_dir():
                if item.name in ignore_dirs:
                    continue
                # Virtualenv detection
                if (item / "pyvenv.cfg").is_file():
                    continue
                yield from _walk_python_files(item, ignore_dirs, spec, root)
            elif item.is_file() and item.suffix == ".py":
                yield item
    except PermissionError:
        pass

class ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.imports = set()
        self.optional_imports = set()
        self._in_try_except = False
        self._in_type_checking = False
        
    def visit_Try(self, node: ast.Try):
        # We assume if it's in a try, and handles ImportError or Exception, it might be optional
        has_import_error = False
        for handler in node.handlers:
            if handler.type is None or isinstance(handler.type, ast.Name) and handler.type.id in ("ImportError", "ModuleNotFoundError", "Exception"):
                has_import_error = True
            elif isinstance(handler.type, ast.Tuple):
                for elt in handler.type.elts:
                    if isinstance(elt, ast.Name) and elt.id in ("ImportError", "ModuleNotFoundError", "Exception"):
                        has_import_error = True
                        
        prev = self._in_try_except
        if has_import_error:
            self._in_try_except = True
            
        self.generic_visit(node)
        self._in_try_except = prev

    def visit_If(self, node: ast.If):
        # Check if TYPE_CHECKING
        is_tc = False
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING" or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING":
            is_tc = True
            
        prev = self._in_type_checking
        if is_tc:
            self._in_type_checking = True
            
        self.generic_visit(node)
        self._in_type_checking = prev

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            base_module = alias.name.split(".")[0]
            self._add_import(base_module)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module and node.level == 0:
            base_module = node.module.split(".")[0]
            self._add_import(base_module)
        self.generic_visit(node)
        
    def _add_import(self, base_module: str):
        if self._in_try_except or self._in_type_checking:
            self.optional_imports.add(base_module)
        else:
            self.imports.add(base_module)

def _simplify_syntax_error(e: SyntaxError) -> str:
    msg = str(e.msg).lower()
    if "unexpected eof" in msg:
        return "The file ends unexpectedly. You might be missing a closing parenthesis, bracket, or quote."
    if "invalid syntax" in msg:
        return "There is a typo or invalid code structure."
    if "expected ':'" in msg:
        return "You might be missing a colon ':' at the end of an if, for, while, or def statement."
    if "unindent does not match" in msg or "indentation" in msg:
        return "There's a problem with the indentation (mismatched spaces or tabs)."
    if "unterminated string literal" in msg:
        return "You forgot to close a string quote."
    return "There's a syntax error that prevents Python from reading the file."

def _scan_file(py_file: Path, data: ImportData, is_dev: bool, first_party: set[str], stdlib: set[str]):
    try:
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        
        visitor = ImportVisitor()
        visitor.visit(tree)
        
        # Filter and categorize
        for imp in visitor.imports:
            if imp in stdlib or imp in first_party:
                continue
            if is_dev:
                data.dev.add(imp)
            else:
                data.runtime.add(imp)
                
        for imp in visitor.optional_imports:
            if imp in stdlib or imp in first_party:
                continue
            data.optional.add(imp)
            
    except SyntaxError as e:
        simple_msg = _simplify_syntax_error(e)
        line = e.lineno or "unknown"
        data.syntax_errors.append(f"{py_file.name} (line {line}): {simple_msg}")
    except Exception:
        pass

def _get_fallback_stdlib() -> set[str]:
    return {"os", "sys", "re", "math", "json", "ast", "pathlib", "typing", "subprocess", "logging", "argparse", "datetime", "collections"}

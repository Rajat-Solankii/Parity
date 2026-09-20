import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

@dataclass(frozen=True)
class Location:
    file: str
    line: int
    specifier: str = ""
    marker: str = ""

@dataclass
class ManifestData:
    runtime: dict[str, list[Location]] = field(default_factory=dict)
    dev: dict[str, list[Location]] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    requires_python: str | None = None

    def merge(self, other: 'ManifestData') -> None:
        for k, v in other.runtime.items():
            self.runtime.setdefault(k, []).extend(v)
        for k, v in other.dev.items():
            self.dev.setdefault(k, []).extend(v)
        self.unresolved.extend(other.unresolved)

def normalize_dep(name: str) -> str:
    """Normalize a dependency string to just its package name."""
    return re.sub(r'[-_.]+', '-', name).lower()

def _add_dep(target_dict: dict[str, list[Location]], req_str: str, file: str, line: int = 0) -> None:
    try:
        req = Requirement(req_str)
        name = normalize_dep(req.name)
        specifier = str(req.specifier) if req.specifier else ""
        marker = str(req.marker) if req.marker else ""
        target_dict.setdefault(name, []).append(Location(file, line, specifier, marker))
    except InvalidRequirement:
        # Fallback for simple names that might not be valid pep508 fully
        match = re.match(r"^([a-zA-Z0-9_.-]+)", req_str)
        if match:
            name = normalize_dep(match.group(1))
            target_dict.setdefault(name, []).append(Location(file, line, "", ""))

def get_declared_dependencies(project_path: Path, manifest_file: Path | None = None) -> ManifestData:
    data = ManifestData()
    if manifest_file:
        _parse_file(manifest_file, data)
    else:
        for f in _find_manifests(project_path):
            _parse_file(f, data)
    return data

def _find_manifests(project_path: Path) -> list[Path]:
    manifests = []
    for name in ["pyproject.toml", "setup.py", "setup.cfg", "Pipfile"]:
        p = project_path / name
        if p.is_file():
            manifests.append(p)
    reqs = list(project_path.glob("requirements*.txt"))
    return manifests + reqs

def _parse_file(path: Path, data: ManifestData, seen: set[Path] | None = None) -> None:
    if seen is None:
        seen = set()
    if path in seen:
        return
    seen.add(path)
    
    if path.name == "pyproject.toml":
        _parse_pyproject(path, data)
    elif path.name.endswith(".txt"):
        is_dev = "dev" in path.name or "test" in path.name
        _parse_requirements_txt(path, data, is_dev, seen)
    elif path.name == "Pipfile":
        _parse_pipfile(path, data)
    elif path.name == "setup.py":
        _parse_setup_py(path, data)
    # setup.cfg skipped for brevity unless strictly needed

def _parse_pyproject(path: Path, data: ManifestData) -> None:
    try:
        with path.open("rb") as f:
            toml_dict = tomllib.load(f)
            
        # PEP 621
        project = toml_dict.get("project", {})
        data.requires_python = project.get("requires-python")
        for dep in project.get("dependencies", []):
            _add_dep(data.runtime, dep, str(path))
            
        for extra, deps in project.get("optional-dependencies", {}).items():
            target = data.dev if "dev" in extra or "test" in extra else data.runtime
            for dep in deps:
                _add_dep(target, dep, str(path))
                
        # Poetry
        poetry = toml_dict.get("tool", {}).get("poetry", {})
        for dep, version in poetry.get("dependencies", {}).items():
            if dep == "python":
                continue

        if "python" in poetry.get("dependencies", {}):
            val = poetry["dependencies"]["python"]
            if not data.requires_python:
                data.requires_python = val if isinstance(val, str) else None

            req = f"{dep} {version}" if isinstance(version, str) else dep
            _add_dep(data.runtime, req, str(path))
            
        for dep, version in poetry.get("dev-dependencies", {}).items():
            req = f"{dep} {version}" if isinstance(version, str) else dep
            _add_dep(data.dev, req, str(path))
            
        # Poetry group.dev
        dev_group = poetry.get("group", {}).get("dev", {}).get("dependencies", {})
        for dep, version in dev_group.items():
            req = f"{dep} {version}" if isinstance(version, str) else dep
            _add_dep(data.dev, req, str(path))
            
    except Exception as e: # noqa: BLE001
        data.unresolved.append(f"{path.name}: Failed to parse ({e})")

def _parse_requirements_txt(path: Path, data: ManifestData, is_dev: bool, seen: set[Path]) -> None:
    try:
        content = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Follow includes
            if line.startswith(("-r ", "-c ")):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    rel_path = parts[1].strip()
                    # Remove inline comments in the path part if any
                    rel_path = rel_path.split(" #")[0].strip()
                    next_file = path.parent / rel_path
                    _parse_file(next_file, data, seen)
                continue

            if line.startswith("-"):
                continue

            # Just add the whole line
            if is_dev:
                _add_dep(data.dev, line, str(path), lineno)
            else:
                _add_dep(data.runtime, line, str(path), lineno)
                
    except OSError as e:
        data.unresolved.append(f"{path.name}: {e}")

def _parse_pipfile(path: Path, data: ManifestData) -> None:
    try:
        with path.open("rb") as f:
            toml_dict = tomllib.load(f)
            
        for dep in toml_dict.get("packages", {}):
            _add_dep(data.runtime, dep, str(path))
            
        for dep in toml_dict.get("dev-packages", {}):
            _add_dep(data.dev, dep, str(path))
    except Exception as e: # noqa: BLE001
        data.unresolved.append(f"{path.name}: Failed to parse ({e})")

def _parse_setup_py(path: Path, data: ManifestData) -> None:
    try:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(path))
    except (SyntaxError, OSError) as e:
        data.unresolved.append(f"{path.name}: AST parse error ({e})")
        return
        
    class SetupVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.assignments: dict[str, list[str]] = {}

        def visit_Assign(self, node: ast.Assign) -> None:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if isinstance(node.value, ast.List):
                    values = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            values.append(elt.value)
                    self.assignments[name] = values
            self.generic_visit(node)
            
        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "setup":
                for kw in node.keywords:
                    if kw.arg == "install_requires":
                        _extract_deps_from_node(kw.value, data.runtime, data, path, self.assignments)
                    elif kw.arg == "python_requires" and isinstance(kw.value, ast.Constant):
                        if not data.requires_python:
                            data.requires_python = str(kw.value.value)
                    elif kw.arg == "extras_require" and isinstance(kw.value, ast.Dict):
                        for k, v in zip(kw.value.keys, kw.value.values):
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                target = data.dev if "dev" in k.value or "test" in k.value else data.runtime
                                _extract_deps_from_node(v, target, data, path, self.assignments)
            self.generic_visit(node)

    visitor = SetupVisitor()
    visitor.visit(tree)

def _extract_deps_from_node(node: Any, target_set: dict[str, list[Location]], data: ManifestData, path: Path, assignments: dict[str, list[str]]) -> None:
    # Direct list: ['pkg1', 'pkg2']
    if isinstance(node, ast.List):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                _add_dep(target_set, elt.value, str(path), getattr(node, "lineno", 0))
            else:
                data.unresolved.append(f"{path.name}:{node.lineno} dynamic element in list")

    # Variable reference: var_name
    elif isinstance(node, ast.Name):
        if node.id in assignments:
            for val in assignments[node.id]:
                _add_dep(target_set, val, str(path), getattr(node, "lineno", 0))
        else:
            data.unresolved.append(f"{path.name}:{node.lineno} unresolved variable '{node.id}'")
            
    # Call: open('requirements.txt').read().splitlines()
    elif isinstance(node, ast.Call):
        if _is_open_read_splitlines(node):
            file_arg = _get_open_arg(node)
            if file_arg:
                req_path = path.parent / file_arg
                if req_path.is_file():
                    # Parse as runtime or dev based on target_set's current state (heuristic)
                    is_dev = target_set is data.dev
                    _parse_requirements_txt(req_path, data, is_dev, {path})
                    return
        data.unresolved.append(f"{path.name}:{node.lineno} dynamic call in setup")
        
    # BinOp: _RUNTIME + ['other']
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        _extract_deps_from_node(node.left, target_set, data, path, assignments)
        _extract_deps_from_node(node.right, target_set, data, path, assignments)
    else:
        data.unresolved.append(f"{path.name}:{getattr(node, 'lineno', 0)} dynamic expression")

def _is_open_read_splitlines(node: Any) -> bool:
    # Extremely basic AST heuristic for open('...').read().splitlines()
    if isinstance(node.func, ast.Attribute) and node.func.attr == "splitlines":
        if isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Attribute) and node.func.value.func.attr == "read":
            if isinstance(node.func.value.func.value, ast.Call) and isinstance(node.func.value.func.value.func, ast.Name) and node.func.value.func.value.func.id == "open":
                return True
    return False

def _get_open_arg(node: Any) -> str | None:
    try:
        open_call = node.func.value.func.value
        if hasattr(open_call, 'args') and open_call.args and hasattr(open_call.args[0], 'value') and isinstance(open_call.args[0].value, str):
            return open_call.args[0].value
    except AttributeError:
        pass
    return None

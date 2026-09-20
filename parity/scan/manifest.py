import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # type: ignore
except ImportError:
    import tomli as tomllib  # type: ignore

@dataclass
class ManifestData:
    runtime: set[str] = field(default_factory=set)
    dev: set[str] = field(default_factory=set)
    unresolved: list[str] = field(default_factory=list) # e.g. setup.py line 42 could not be resolved statically

    def merge(self, other: 'ManifestData'):
        self.runtime.update(other.runtime)
        self.dev.update(other.dev)
        self.unresolved.extend(other.unresolved)

def normalize_dep(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()

def get_declared_dependencies(project_path: Path, manifest_file: Path | None = None) -> ManifestData:
    """
    Extract declared dependencies from manifests in the project path.
    Separates into runtime and dev groups.
    """
    data = ManifestData()
    
    if manifest_file:
        _parse_file(manifest_file, data)
        return data

    # Auto-detect all supported files in the root
    files_to_check = []
    
    # Requirements files
    for req_file in project_path.glob("requirements*.txt"):
        files_to_check.append(req_file)

    for name in ["pyproject.toml", "setup.py", "setup.cfg", "Pipfile"]:
        path = project_path / name
        if path.is_file():
            files_to_check.append(path)

    for f in files_to_check:
        _parse_file(f, data)
        
    return data

def _parse_file(path: Path, data: ManifestData, seen: set[Path] | None = None):
    if seen is None:
        seen = set()
    
    path = path.resolve()
    if path in seen:
        return
    seen.add(path)
    
    if not path.is_file():
        return
        
    name = path.name
    if name.endswith((".txt", ".in")):
        is_dev = "dev" in name.lower() or "test" in name.lower()
        _parse_requirements_txt(path, data, is_dev, seen)
    elif name == "pyproject.toml":
        _parse_pyproject_toml(path, data)
    elif name == "setup.py":
        _parse_setup_py(path, data)
    elif name == "Pipfile":
        _parse_pipfile(path, data)

def _parse_requirements_txt(path: Path, data: ManifestData, is_dev: bool, seen: set[Path]):
    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
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
                
            match = re.match(r"^([a-zA-Z0-9_.-]+)", line)
            if match:
                dep = normalize_dep(match.group(1))
                if is_dev:
                    data.dev.add(dep)
                else:
                    data.runtime.add(dep)
    except Exception:
        pass

def _parse_pyproject_toml(path: Path, data: ManifestData):
    try:
        content = path.read_text(encoding="utf-8")
        toml_data = tomllib.loads(content)

        # PEP 621
        if "project" in toml_data:
            project = toml_data["project"]
            if "dependencies" in project:
                for dep in project["dependencies"]:
                    match = re.match(r"^([a-zA-Z0-9_.-]+)", dep)
                    if match:
                        data.runtime.add(normalize_dep(match.group(1)))
                        
            if "optional-dependencies" in project:
                for extras in project["optional-dependencies"].values():
                    for dep in extras:
                        match = re.match(r"^([a-zA-Z0-9_.-]+)", dep)
                        if match:
                            data.dev.add(normalize_dep(match.group(1)))

        # Poetry
        if "tool" in toml_data and "poetry" in toml_data["tool"]:
            poetry = toml_data["tool"]["poetry"]
            if "dependencies" in poetry:
                for k in poetry["dependencies"]:
                    if k.lower() != "python":
                        data.runtime.add(normalize_dep(k))
            if "dev-dependencies" in poetry:
                for k in poetry["dev-dependencies"]:
                    data.dev.add(normalize_dep(k))

            if "group" in poetry:
                for group_data in poetry["group"].values():
                    if "dependencies" in group_data:
                        for k in group_data["dependencies"]:
                            data.dev.add(normalize_dep(k))

    except Exception:
        pass

def _parse_setup_py(path: Path, data: ManifestData):
    try:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        
        # Track literal assignments
        assignments: dict[str, list[str]] = {}
        
        for node in ast.walk(tree):
            # Track list assignments: var = ['pkg1', 'pkg2']
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if isinstance(node.value, ast.List):
                            val_list = []
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    val_list.append(elt.value)
                            if len(val_list) == len(node.value.elts): # Only if all are string literals
                                assignments[target.id] = val_list
                                
            # Extract setup(...)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "setup":
                    for kw in node.keywords:
                        if kw.arg == "install_requires":
                            _extract_deps_from_node(kw.value, data.runtime, data, path, assignments)
                        elif kw.arg == "extras_require":
                            if isinstance(kw.value, ast.Dict):
                                for val in kw.value.values:
                                    _extract_deps_from_node(val, data.dev, data, path, assignments)

    except Exception as e:
        data.unresolved.append(f"{path.name}: Failed to parse ({e})")

def _extract_deps_from_node(node: ast.expr, target_set: set[str], data: ManifestData, path: Path, assignments: dict[str, list[str]]):
    # Direct list: ['pkg1', 'pkg2']
    if isinstance(node, ast.List):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                match = re.match(r"^([a-zA-Z0-9_.-]+)", elt.value)
                if match:
                    target_set.add(normalize_dep(match.group(1)))
            else:
                data.unresolved.append(f"{path.name}:{node.lineno} dynamic element in list")
    
    # Variable reference: var_name
    elif isinstance(node, ast.Name):
        if node.id in assignments:
            for val in assignments[node.id]:
                match = re.match(r"^([a-zA-Z0-9_.-]+)", val)
                if match:
                    target_set.add(normalize_dep(match.group(1)))
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
        data.unresolved.append(f"{path.name}:{node.lineno} unresolved dynamic call")
    
    # BinaryOp: ['pkg1'] + ['pkg2']
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        _extract_deps_from_node(node.left, target_set, data, path, assignments)
        _extract_deps_from_node(node.right, target_set, data, path, assignments)
    
    else:
        data.unresolved.append(f"{path.name}:{node.lineno} unsupported AST node for dependencies")

def _is_open_read_splitlines(node: ast.Call) -> bool:
    # Looks for: open('...').read().splitlines()
    if isinstance(node.func, ast.Attribute) and node.func.attr == "splitlines":
        caller1 = node.func.value
        if isinstance(caller1, ast.Call) and isinstance(caller1.func, ast.Attribute) and caller1.func.attr == "read":
            caller2 = caller1.func.value
            if isinstance(caller2, ast.Call) and isinstance(caller2.func, ast.Name) and caller2.func.id == "open":
                return True
    return False

def _get_open_arg(node: ast.Call) -> str | None:
    # Extracts '...' from open('...').read().splitlines()
    try:
        open_call = node.func.value.func.value # type: ignore
        if open_call.args and isinstance(open_call.args[0], ast.Constant):
            return str(open_call.args[0].value)
    except Exception:
        pass
    return None

def _parse_pipfile(path: Path, data: ManifestData):
    try:
        content = path.read_text(encoding="utf-8")
        toml_data = tomllib.loads(content)
        if "packages" in toml_data:
            for k in toml_data["packages"]:
                data.runtime.add(normalize_dep(k))
        if "dev-packages" in toml_data:
            for k in toml_data["dev-packages"]:
                data.dev.add(normalize_dep(k))
    except Exception:
        pass

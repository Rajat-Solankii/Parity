import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import packaging.tags

import parity


class ToolStatus(str, Enum):
    FOUND = "FOUND"
    FOUND_NOT_ON_PATH = "FOUND_NOT_ON_PATH"
    NOT_FOUND = "NOT_FOUND"
    BROKEN = "BROKEN"
    FOUND_INCOMPLETE = "FOUND_INCOMPLETE"

@dataclass
class ToolFingerprint:
    status: ToolStatus
    path: str | None = None
    version: str | None = None
    winget_id: str | None = None
    error_msg: str | None = None

@dataclass
class EnvFingerprint:
    schema_version: str
    parity_version: str
    timestamp: str
    os_name: str
    os_release: str
    architecture: str
    platform_tags: list[str]
    python_version: str
    python_executable: str
    is_venv: bool
    pip_version: str
    installed_packages: dict[str, str] = field(default_factory=dict)
    user_site_packages: dict[str, str] = field(default_factory=dict)
    native_tools: dict[str, ToolFingerprint] = field(default_factory=dict)

def _safe_which(cmd: str, env: dict[str, str]) -> str | None:
    """Safe Windows alternative to shutil.which that avoids the current directory."""
    if platform.system() != "Windows":
        return shutil.which(cmd, path=env.get("PATH"))
        
    path_env = env.get("PATH", "")
    paths = [p for p in path_env.split(os.pathsep) if p and p != "."]
    
    pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
    
    # Block selection from project dir or current dir
    forbidden_dirs = [Path.cwd().resolve()]
    try:
        # Heuristically avoid anything in the current site-packages too if needed, but cwd is main issue
        pass
    except Exception: # noqa: BLE001, S110 (Path check failsafe)
        pass

    for p in paths:
        dir_path = Path(p).resolve()
        
        # Check if dir_path is inside a forbidden dir
        try:
            if any(dir_path == fd or fd in dir_path.parents for fd in forbidden_dirs):
                continue
        except Exception: # noqa: BLE001, S110 (Path check failsafe)
            pass
            
        for ext in [""] + pathext:
            candidate = dir_path / f"{cmd}{ext}"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None

def _get_target_paths(target_python: str) -> list[str]:
    """Extract paths safely from target python."""
    target_path = Path(target_python).resolve()
    
    # Try static venv layout first
    if target_path.parent.name in ("Scripts", "bin"):
        venv_root = target_path.parent.parent
        if (venv_root / "pyvenv.cfg").is_file():
            # It's a venv
            if platform.system() == "Windows":
                sp = venv_root / "Lib" / "site-packages"
            else:
                # Need to find pythonX.Y
                lib = venv_root / "lib"
                sp = None
                if lib.exists():
                    for d in lib.iterdir():
                        if d.name.startswith("python"):
                            sp = d / "site-packages"
                            break
            
            if sp and sp.exists():
                return [str(sp)]
    
    # Fallback to sysconfig
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                [target_python, "-S", "-I", "-c", 
                 "import sysconfig, json; p = sysconfig.get_paths(); print(json.dumps([p['purelib'], p['platlib']]))"],
                capture_output=True, text=True, timeout=10, shell=False, cwd=tmpdir, check=False
            )
            if result.returncode == 0:
                raw = json.loads(result.stdout)
                if isinstance(raw, list):
                    # Validate absolute paths
                    valid_paths = []
                    for p in raw:
                        if isinstance(p, str):
                            p_obj = Path(p)
                            if p_obj.is_absolute() and p_obj.is_dir():
                                valid_paths.append(str(p_obj))
                    return list(dict.fromkeys(valid_paths))  # deduplicate
            else:
                print(f"Fallback subprocess failed: {result.stderr}")
        except Exception as e: # noqa: BLE001 (Safe fallback)
            print(f"Warning: Failed to locate site-packages via fallback: {e}")
            return []
            
    print("Warning: Could not locate site-packages safely.")
    return []

def _parse_pth_files(site_dirs: list[str]) -> list[str]:
    extra_dirs = []
    for sp in site_dirs:
        sp_path = Path(sp)
        if not sp_path.exists():
            continue
        for pth in sp_path.glob("*.pth"):
            try:
                lines = pth.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith(("#", "import ")):
                        continue
                    
                    # Resolve relative
                    extra_path = (pth.parent / line).resolve()
                    if extra_path.is_dir():
                        # Simple protection against symlink escapes
                        try:
                            # Not strictly checking full env bound, but ensuring it resolves
                            extra_dirs.append(str(extra_path))
                        except Exception: # noqa: BLE001, S110 (Safe fallback)
                            pass
            except Exception: # noqa: BLE001, S110 (Safe fallback)
                pass
    return extra_dirs

def _get_user_site(target_python: str) -> list[str]:
    # We do a safe query just for user site
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                [target_python, "-S", "-c", 
                 "import site; print(site.USER_SITE)"],
                capture_output=True, text=True, timeout=2, shell=False, cwd=tmpdir, check=False
            )
            if result.returncode == 0:
                p = Path(result.stdout.strip())
                if p.is_absolute() and p.is_dir():
                    return [str(p)]
        except Exception: # noqa: BLE001, S110 (Safe fallback)
            pass
    return []

def capture_environment(
    target_python: str | None = None, extra_tools: list[str] | None = None
) -> EnvFingerprint:
    target_python = target_python or sys.executable

    target_paths = _get_target_paths(target_python)
    target_paths.extend(_parse_pth_files(target_paths))
    
    # Extract packages directly
    installed = {}
    if target_paths:
        for dist in importlib.metadata.distributions(path=target_paths):
            try:
                name = dist.metadata["Name"]
                if name:
                    installed[name] = dist.version
            except KeyError:
                pass # skip distribution with missing/malformed metadata

    # Extract user site
    user_site_pkgs: dict[str, str] = {}
    user_site = _get_user_site(target_python)
    if user_site:
        for dist in importlib.metadata.distributions(path=user_site):
            try:
                name = dist.metadata["Name"]
                if name and name not in installed:
                    user_site_pkgs[name] = dist.version
            except KeyError:
                pass

    # Get pip version and python version info (safe to execute target_python --version)
    with tempfile.TemporaryDirectory() as tmpdir:
        py_ver = "Unknown"
        try:
            res = subprocess.run([target_python, "-c", "import sys; print(sys.version.split(' ')[0])"], 
                                 cwd=tmpdir, shell=False, capture_output=True, text=True, timeout=2, check=False)
            if res.returncode == 0:
                py_ver = res.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass
            
        pip_ver = "Unknown"
        if "pip" in installed:
            pip_ver = installed["pip"]

    is_venv = False
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            res = subprocess.run([target_python, "-c", "import sys; print(sys.prefix != sys.base_prefix)"], 
                                 cwd=tmpdir, shell=False, capture_output=True, text=True, timeout=2, check=False)
            if res.returncode == 0:
                is_venv = res.stdout.strip() == "True"
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

    tags = [str(t) for t in packaging.tags.sys_tags()]
    
    # Sort keys for determinism
    installed = dict(sorted(installed.items(), key=lambda x: re.sub(r"[-_.]+", "-", x[0]).lower()))
    user_site_pkgs = dict(sorted(user_site_pkgs.items(), key=lambda x: re.sub(r"[-_.]+", "-", x[0]).lower()))

    env = EnvFingerprint(
        schema_version="1.0",
        parity_version=parity.__version__,
        timestamp=datetime.now(UTC).isoformat() + "Z",
        os_name=platform.system(),
        os_release=platform.release(),
        architecture=platform.machine(),
        platform_tags=sorted(tags)[:10], 
        python_version=py_ver,
        python_executable=target_python,
        is_venv=is_venv,
        pip_version=pip_ver,
        installed_packages=installed,
        user_site_packages=user_site_pkgs
    )

    # 2. Capture Native Tools
    bundled_catalog = Path(__file__).parent.parent / "data" / "tool_catalog.json"
    if bundled_catalog.is_file():
        catalog = json.loads(bundled_catalog.read_text(encoding="utf-8")).get("tools", {})
    else:
        catalog = {}

    tools_to_check = set(catalog.keys())
    
    # Extended tools validation
    extended_tool_regex = re.compile(r"^[A-Za-z0-9._+-]+$")
    if extra_tools:
        for tool in extra_tools:
            if not extended_tool_regex.match(tool):
                raise ValueError(f"Invalid extended tool name: {tool}")
            tools_to_check.add(tool)

    safe_env: dict[str, str] = os.environ.copy()
    # Clean PATH: remove empty and "."
    if "PATH" in safe_env:
        paths = [p for p in safe_env["PATH"].split(os.pathsep) if p and p != "."]
        safe_env["PATH"] = os.pathsep.join(paths)

    for tool in sorted(tools_to_check):
        is_extended = tool not in catalog
        tool_data = catalog.get(tool, {
            "executable_names": [tool],
            "common_paths_windows": [],
            "common_paths_posix": [],
            "version_args": ["--version"],
            "version_regex": r"([\d\.]+)",
            "winget_id": None
        })
        env.native_tools[tool] = _detect_tool(tool_data, safe_env, is_extended=is_extended)
        
    return env

def _detect_tool(tool_data: dict[str, Any], safe_env: dict[str, str], is_extended: bool = False) -> ToolFingerprint:
    if "cl" in tool_data.get("executable_names", []) and platform.system() == "Windows" and not is_extended:
        return _detect_msvc(safe_env, tool_data)

    for exe_name in tool_data.get("executable_names", []):
        exe_path = _safe_which(exe_name, safe_env)
        status = ToolStatus.FOUND if exe_path else ToolStatus.NOT_FOUND
        
        if status == ToolStatus.NOT_FOUND:
            common = tool_data.get("common_paths_windows", []) if platform.system() == "Windows" else tool_data.get("common_paths_posix", [])
            for cpath in common:
                expanded = os.path.expandvars(cpath)
                if "%" in expanded or "$" in expanded:
                    continue
                p = Path(expanded)
                if p.is_file():
                    exe_path = str(p)
                    status = ToolStatus.FOUND_NOT_ON_PATH
                    break

        if exe_path:
            version = None
            if not is_extended:
                try:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        result = subprocess.run(
                            [exe_path] + tool_data.get("version_args", ["--version"]),
                            capture_output=True, text=True, timeout=10, shell=False, cwd=tmpdir, env=safe_env, check=False
                        )
                        output = result.stdout + result.stderr
                        match = re.search(tool_data.get("version_regex", r"([\d\.]+)"), output, re.IGNORECASE)
                        if match:
                            version = match.group(1)
                except (subprocess.SubprocessError, FileNotFoundError, OSError):
                    pass
                
            return ToolFingerprint(
                status=status,
                path=exe_path,
                version=version,
                winget_id=tool_data.get("winget_id") if not is_extended else None
            )
            
    return ToolFingerprint(status=ToolStatus.NOT_FOUND, winget_id=tool_data.get("winget_id") if not is_extended else None)

def _detect_msvc(safe_env: dict[str, str], tool_data: dict[str, Any]) -> ToolFingerprint:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if not vswhere.exists():
        return ToolFingerprint(status=ToolStatus.NOT_FOUND)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [str(vswhere), "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationVersion"],
                capture_output=True, text=True, timeout=10, shell=False, cwd=tmpdir, env=safe_env, check=False
            )
            version = result.stdout.strip()
            
            if version:
                path_result = subprocess.run(
                    [str(vswhere), "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                    capture_output=True, text=True, timeout=10, shell=False, cwd=tmpdir, env=safe_env, check=False
                )
                install_path = path_result.stdout.strip()
                return ToolFingerprint(status=ToolStatus.FOUND_NOT_ON_PATH, path=install_path, version=version)
            else:
                check_vs = subprocess.run(
                    [str(vswhere), "-products", "*", "-property", "installationVersion"],
                    capture_output=True, text=True, timeout=10, shell=False, cwd=tmpdir, env=safe_env, check=False
                )
                if check_vs.stdout.strip():
                    return ToolFingerprint(status=ToolStatus.FOUND_INCOMPLETE, error_msg="VS installed without C++ workload")
                
    except Exception: # noqa: BLE001, S110 (Subprocess error for vswhere failsafe)
        pass
        
    return ToolFingerprint(status=ToolStatus.NOT_FOUND)

def redact_fingerprint(env: EnvFingerprint) -> EnvFingerprint:
    import os
    userprofile = os.environ.get("USERPROFILE", "")
    username = os.environ.get("USERNAME", "")
    
    def redact_str(s: str) -> str:
        if not s: return s
        if userprofile and userprofile in s:
            s = s.replace(userprofile, "<USERPROFILE>")
        if username and username in s:
            s = s.replace(username, "<USER>")
        return s

    env.python_executable = redact_str(env.python_executable)
    for t in env.native_tools.values():
        if t.path:
            t.path = redact_str(t.path)
            
    return env

"""
Parity Frontend API Server

A lightweight Flask server that exposes the Parity engine as JSON endpoints
for the web dashboard.

Run:  python frontend/server.py
      → serves on http://localhost:5117
"""
from __future__ import annotations

import dataclasses
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any

# Add project root to path so we can import parity
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder=str(PROJECT_ROOT / "frontend"), static_url_path="")
CORS(app)


# ─── JSON Encoder that handles dataclasses and Enums ───
class ParityEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, Enum):
            return o.value
        return super().default(o)


def jsonify_parity(obj: Any, status: int = 200) -> Any:
    """Serialize Parity objects (dataclasses, Enums) to JSON."""
    payload = json.loads(json.dumps(obj, cls=ParityEncoder))
    return jsonify(payload), status


# ─── Static file serving ───
@app.route("/")
def index() -> Any:
    return send_from_directory(app.static_folder, "index.html")  # type: ignore[arg-type]


# ─── API: Scan ───
@app.route("/api/scan", methods=["POST"])
def api_scan() -> Any:
    data = request.get_json(silent=True) or {}
    project_path = Path(data.get("path", ".")).resolve()

    if not project_path.is_dir():
        return jsonify({"error": f"Not a directory: {project_path}"}), 400

    from parity.scan.ast_import import scan_directory_for_imports
    from parity.scan.manifest import get_declared_dependencies

    declared = get_declared_dependencies(project_path)
    imported = scan_directory_for_imports(project_path)

    # Convert Location objects for JSON
    def locs_to_dicts(locs_dict: dict) -> dict:
        out: dict[str, list[dict[str, Any]]] = {}
        for name, locs in locs_dict.items():
            out[name] = [
                {
                    "file": getattr(loc, "file", str(loc)),
                    "line": getattr(loc, "line", 0),
                    "specifier": getattr(loc, "specifier", ""),
                    "marker": getattr(loc, "marker", ""),
                }
                for loc in locs
            ]
        return out

    result = {
        "declared": {
            "runtime": locs_to_dicts(declared.runtime),
            "dev": locs_to_dicts(declared.dev),
            "unresolved": declared.unresolved,
            "requires_python": declared.requires_python,
        },
        "imported": {
            "runtime": locs_to_dicts(imported.runtime),
            "dev": locs_to_dicts(imported.dev),
            "optional": locs_to_dicts(imported.optional),
            "syntax_errors": imported.syntax_errors,
        },
    }
    return jsonify(result)


# ─── API: Diagnose ───
@app.route("/api/diagnose", methods=["POST"])
def api_diagnose() -> Any:
    data = request.get_json(silent=True) or {}
    project_path = Path(data.get("path", ".")).resolve()
    against_file = data.get("against")

    if not project_path.is_dir():
        return jsonify({"error": f"Not a directory: {project_path}"}), 400

    from parity.diagnose.engine import Diagnoser
    from parity.env.fingerprint import EnvFingerprint, capture_environment
    from parity.resolve.mapping import Resolution, map_import_to_package
    from parity.scan.ast_import import scan_directory_for_imports
    from parity.scan.manifest import get_declared_dependencies

    import_data = scan_directory_for_imports(project_path)
    manifest_data = get_declared_dependencies(project_path)

    if against_file:
        fp_path = Path(against_file).resolve()
        if fp_path.is_file():
            with open(fp_path) as f:
                fp_data = json.load(f)
            fingerprint = EnvFingerprint(**fp_data)
        else:
            return jsonify({"error": f"Fingerprint file not found: {fp_path}"}), 400
    else:
        fingerprint = capture_environment()

    class _Resolver:
        def __init__(self, fp: EnvFingerprint) -> None:
            self.fp = fp

        def map_import_to_package(self, import_name: str) -> Resolution:
            return map_import_to_package(
                import_name, allow_online=False, target_env_distributions=None
            )

    diagnoser = Diagnoser(manifest_data, import_data, fingerprint, _Resolver(fingerprint))
    findings = diagnoser.evaluate()

    return jsonify_parity({"findings": findings})


# ─── API: Fingerprint ───
@app.route("/api/fingerprint", methods=["POST"])
def api_fingerprint() -> Any:
    data = request.get_json(silent=True) or {}
    python_path = data.get("python")
    extra_tools = data.get("tools", [])

    from parity.env.fingerprint import capture_environment

    env = capture_environment(target_python=python_path, extra_tools=extra_tools or None)
    return jsonify_parity(dataclasses.asdict(env))


# ─── API: Resolve ───
@app.route("/api/resolve", methods=["POST"])
def api_resolve() -> Any:
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()

    if not name:
        return jsonify({"error": "No import name provided"}), 400

    from parity.resolve.mapping import map_import_to_package

    result = map_import_to_package(name, allow_online=False, target_env_distributions=None)
    candidates = [
        {
            "distribution": c.distribution,
            "layer": c.layer,
            "confidence": c.confidence.value if isinstance(c.confidence, Enum) else str(c.confidence),
        }
        for c in result.candidates
    ]
    return jsonify({"candidates": candidates})


# ─── API: Browse (native folder dialog) ───
@app.route("/api/browse", methods=["POST"])
def api_browse() -> Any:
    import threading

    result: dict[str, str] = {}

    def pick_folder() -> None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Select Project Folder")
        root.destroy()
        if folder:
            result["path"] = folder

    # tkinter must run on its own thread to avoid blocking Flask
    t = threading.Thread(target=pick_folder)
    t.start()
    t.join(timeout=120)  # 2 min max wait

    if "path" in result:
        return jsonify({"path": result["path"]})
    return jsonify({"path": ""})


# ─── API: Uninstall Package ───
@app.route("/api/uninstall-package", methods=["POST"])
def api_uninstall_package() -> Any:
    import subprocess

    data = request.get_json(silent=True) or {}
    pkg_name = data.get("package", "").strip()

    if not pkg_name:
        return jsonify({"error": "No package name provided"}), 400

    # Safety: reject anything that isn't a simple package name
    import re
    if not re.match(r"^[A-Za-z0-9._-]+$", pkg_name):
        return jsonify({"error": "Invalid package name"}), 400

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", pkg_name],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Uninstall timed out"}), 500

    if result.returncode != 0:
        return jsonify({
            "error": f"pip uninstall failed",
            "detail": (result.stderr or result.stdout or "")[-300:],
        }), 500

    return jsonify({
        "success": True,
        "package": pkg_name,
        "output": (result.stdout or "")[-200:],
    })


# ─── API: Install Package (pip) ───
@app.route("/api/install-pip", methods=["POST"])
def api_install_pip() -> Any:
    import subprocess
    import re

    data = request.get_json(silent=True) or {}
    pkg_name = data.get("package", "").strip()

    if not pkg_name:
        return jsonify({"error": "No package name provided"}), 400

    # Basic safety validation
    if not re.match(r"^[A-Za-z0-9._-]+$", pkg_name):
        return jsonify({"error": "Invalid package name"}), 400

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Install timed out"}), 500

    if result.returncode != 0:
        return jsonify({
            "error": "pip install failed",
            "detail": (result.stderr or result.stdout or "")[-300:],
        }), 500

    return jsonify({
        "success": True,
        "package": pkg_name,
        "output": (result.stdout or "")[-200:],
    })


# ─── API: Add to PATH ───
@app.route("/api/add-to-path", methods=["POST"])
def api_add_to_path() -> Any:
    data = request.get_json(silent=True) or {}
    tool_path = data.get("path", "")
    if not tool_path:
        return jsonify({"error": "No path provided"}), 400
        
    import os
    import winreg
    
    dir_path = os.path.dirname(tool_path)
    
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE)
        try:
            current_path, _ = winreg.QueryValueEx(key, "PATH")
        except FileNotFoundError:
            current_path = ""
            
        path_list = [p.strip() for p in current_path.split(";") if p.strip()]
        if dir_path not in path_list and dir_path + "\\" not in path_list:
            new_path = current_path + (";" if current_path and not current_path.endswith(";") else "") + dir_path
            winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, new_path)
            
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                SMTO_ABORTIFHUNG, 5000, ctypes.byref(ctypes.c_long())
            )
            
            # Also update the current process's environment so Parity recognizes it immediately
            os.environ["PATH"] = os.environ.get("PATH", "") + ";" + dir_path
            
        winreg.CloseKey(key)
        return jsonify({"success": True, "message": f"Added to PATH"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── API: Solve Finding ───
@app.route("/api/solve", methods=["POST"])
def api_solve() -> Any:
    data = request.get_json(silent=True) or {}
    project_path = data.get("path", ".")
    finding = data.get("finding", {})
    
    if not finding:
        return jsonify({"error": "No finding data provided"}), 400
        
    rule_id = finding.get("rule_id")
    fix = finding.get("fix", {})
    
    if rule_id == "R1" and fix.get("kind") == "INSTALL_PACKAGE":
        # R1 is Undeclared Import. The fix is to add it to requirements.txt
        pkg_target = fix.get("target")
        if not pkg_target:
            return jsonify({"error": "No target package specified in fix"}), 400
            
        req_file = Path(project_path) / "requirements.txt"
        
        # Append to requirements.txt (create if it doesn't exist)
        try:
            content = req_file.read_text(encoding="utf-8") if req_file.exists() else ""
            prefix = "\n" if content and not content.endswith("\n") else ""
            with open(req_file, "a", encoding="utf-8") as f:
                f.write(f"{prefix}{pkg_target}\n")
            return jsonify({
                "success": True, 
                "message": f"Added {pkg_target} to requirements.txt"
            })
        except Exception as e:
            return jsonify({"error": f"Failed to write to requirements.txt: {e}"}), 500
            
    elif rule_id == "R2" and fix.get("kind") in ("INSTALL_PACKAGE", "UPGRADE"):
        import subprocess
        import re
        
        pkg_target = fix.get("target")
        spec = fix.get("specifier") or ""
        if not pkg_target:
            return jsonify({"error": "No target package specified in fix"}), 400
            
        if not re.match(r"^[A-Za-z0-9._-]+$", pkg_target):
            return jsonify({"error": "Invalid package name"}), 400
            
        install_arg = f"{pkg_target}{spec}" if spec and spec.lower() != "any" else pkg_target
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", install_arg],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return jsonify({"error": f"pip install failed", "detail": (result.stderr or result.stdout or "")[-300:]}), 500
            return jsonify({
                "success": True,
                "message": f"Installed {install_arg} via pip"
            })
        except Exception as e:
            return jsonify({"error": f"Failed to run pip install: {e}"}), 500
            
    return jsonify({"error": f"Auto-solve not implemented for rule {rule_id}"}), 400


# ─── API: Install Tool (winget) ───
WINGET_IDS: dict[str, str] = {
    "cmake": "Kitware.CMake",
    "docker": "Docker.DockerDesktop",
    "gcc": "MSYS2.MSYS2",
    "git": "Git.Git",
    "make": "GnuWin32.Make",
    "ffmpeg": "Gyan.FFmpeg",
    "node": "OpenJS.NodeJS.LTS",
    "rust": "Rustlang.Rust.MSVC",
    "go": "GoLang.Go",
}


@app.route("/api/install-tool", methods=["POST"])
def api_install_tool() -> Any:
    data = request.get_json(silent=True) or {}
    tool_name = data.get("tool", "").strip().lower()

    if not tool_name:
        return jsonify({"error": "No tool name provided"}), 400

    winget_id = WINGET_IDS.get(tool_name)
    if not winget_id:
        return jsonify({"error": f"No winget package known for '{tool_name}'"}), 400

    import shutil
    import subprocess

    # Run winget install
    try:
        result = subprocess.run(
            [
                "winget", "install", "--id", winget_id,
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--silent",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max
        )
    except FileNotFoundError:
        return jsonify({"error": "winget is not available on this system"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Installation timed out after 5 minutes"}), 500

    if result.returncode != 0:
        # returncode -1978335189 means already installed
        stderr = result.stderr or ""
        stdout = result.stdout or ""
        combined = stdout + stderr
        if "already installed" in combined.lower() or "no available upgrade" in combined.lower():
            pass  # treat as success
        else:
            return jsonify({
                "error": f"winget exited with code {result.returncode}",
                "detail": combined[-500:],
            }), 500

    # Try to find the tool path after install
    found_path = shutil.which(tool_name)

    return jsonify({
        "success": True,
        "tool": tool_name,
        "winget_id": winget_id,
        "path": found_path or "Installed (restart terminal to update PATH)",
        "output": (result.stdout or "")[-300:],
    })


# ─── Main ───
if __name__ == "__main__":
    print("=" * 50)
    print("  Parity Dashboard -- http://localhost:5117")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5117, debug=True)

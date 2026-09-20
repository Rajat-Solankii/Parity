import json
import os
import platform
import venv
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from parity.env.fingerprint import (
    ToolStatus,
    _detect_msvc,
    _get_target_paths,
    _parse_pth_files,
    _safe_which,
    capture_environment,
)


@pytest.fixture
def real_venv(tmp_path):
    venv_dir = tmp_path / "my_venv"
    venv.create(venv_dir, with_pip=False)
    
    if platform.system() == "Windows":
        python_exe = venv_dir / "Scripts" / "python.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        
    return venv_dir, python_exe

def test_target_env_paths_match(real_venv):
    _venv_dir, python_exe = real_venv
    
    # 1. Path from static calculation
    static_paths = _get_target_paths(str(python_exe))
    assert len(static_paths) == 1
    
    # 2. Path from fallback sysconfig
    # Force fallback by mocking is_file
    original_is_file = Path.is_file
    def mock_is_file(self):
        if self.name == "pyvenv.cfg":
            return False
        return original_is_file(self)
        
    with patch("pathlib.Path.is_file", mock_is_file):
        fallback_paths = _get_target_paths(str(python_exe))
        
    # The purelib path from fallback should match the static path
    assert static_paths[0] in fallback_paths

def test_venv_pth_execution_blocked(real_venv, tmp_path):
    venv_dir, python_exe = real_venv
    
    if platform.system() == "Windows":
        sp = venv_dir / "Lib" / "site-packages"
    else:
        lib = venv_dir / "lib"
        sp = next(iter(lib.glob("python*"))) / "site-packages"
        
    sp.mkdir(parents=True, exist_ok=True)
    
    marker = tmp_path / "pwned.txt"
    
    # Create malicious .pth
    pth = sp / "evil.pth"
    pth.write_text(f"import pathlib; pathlib.Path(r'{marker}').write_text('x')")
    
    # Static extraction
    _get_target_paths(str(python_exe))
    assert not marker.exists()
    
    # Force fallback
    original_is_file = Path.is_file
    def mock_is_file(self):
        if self.name == "pyvenv.cfg":
            return False
        return original_is_file(self)
        
    with patch("pathlib.Path.is_file", mock_is_file):
        _get_target_paths(str(python_exe))
        
    assert not marker.exists()

def test_editable_install_pth_discovered_as_data(tmp_path):
    sp = tmp_path / "site-packages"
    sp.mkdir()
    
    (sp / "test.pth").write_text("import os\n../my_editable\n# comment\n")
    
    # Create the target dir so it resolves
    editable = tmp_path / "my_editable"
    editable.mkdir()
    
    paths = _parse_pth_files([str(sp)])
    assert len(paths) == 1
    assert str(editable.resolve()) in paths

def test_tool_status_enum():
    
    # 1. FOUND
    with patch("parity.env.fingerprint._safe_which", return_value="/bin/dummy"):
        res = capture_environment(extra_tools=["dummy"])
        assert res.native_tools["dummy"].status == ToolStatus.FOUND
        
    # 2. NOT FOUND
    with patch("parity.env.fingerprint._safe_which", return_value=None):
        res = capture_environment(extra_tools=["dummy"])
        assert res.native_tools["dummy"].status == ToolStatus.NOT_FOUND

@patch("subprocess.run")
def test_vswhere_parsing(mock_run):
    safe_env = os.environ.copy()
    
    # 1. Full install
    mock_run.side_effect = [
        MagicMock(stdout="16.0", returncode=0), # version
        MagicMock(stdout="C:\\VS", returncode=0)  # path
    ]
    res = _detect_msvc(safe_env, {"command": "vswhere", "args": ["-latest", "-property", "installationVersion"]})
    assert res.status == ToolStatus.FOUND_NOT_ON_PATH
    assert res.version == "16.0"
    
    # 2. Installed without C++ workload
    mock_run.reset_mock()
    mock_run.side_effect = [
        MagicMock(stdout="", returncode=0), # Requires fails
        MagicMock(stdout="16.0", returncode=0) # But VS exists
    ]
    res = _detect_msvc(safe_env, {"command": "vswhere", "args": ["-latest", "-property", "installationVersion"]})
    assert res.status == ToolStatus.FOUND_INCOMPLETE

def test_planted_executable_ignored(tmp_path):
    # Fake a malicious tool in the current directory
    fake_tool = tmp_path / "git.exe"
    fake_tool.write_text("evil")
    fake_tool.chmod(0o777)
    
    os.chdir(tmp_path)
    # The tool should NOT be found because _safe_which ignores cwd
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) # Force it to look here
    assert _safe_which("git", env) is None

def test_two_venvs_differ(tmp_path):
    import venv
    venv1 = tmp_path / "venv1"
    venv2 = tmp_path / "venv2"
    venv.create(venv1, with_pip=False)
    venv.create(venv2, with_pip=False)
    
    py1 = str(venv1 / "Scripts" / "python.exe" if platform.system() == "Windows" else venv1 / "bin" / "python")
    py2 = str(venv2 / "Scripts" / "python.exe" if platform.system() == "Windows" else venv2 / "bin" / "python")
    
    env1 = capture_environment(target_python=py1)
    env2 = capture_environment(target_python=py2)
    
    assert env1.python_executable == py1
    assert env2.python_executable == py2

def test_extended_tools_not_in_tier2_allowlist():
    env = capture_environment(extra_tools=["python"])
    assert env.native_tools["python"].winget_id is None

def test_user_site_reported_separately():
    env = capture_environment()
    assert hasattr(env, "user_site_packages")

def test_tempdirs_cleaned_up():
    # Check that we aren't using bare mkdtemp
    fingerprint_py = Path(__file__).parent.parent / "parity" / "env" / "fingerprint.py"
    content = fingerprint_py.read_text()
    import ast
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "mkdtemp":
                pytest.fail("mkdtemp found in fingerprint.py, use TemporaryDirectory instead")

def test_fingerprint_determinism():
    from dataclasses import asdict
    env1 = capture_environment()
    env2 = capture_environment()
    
    d1 = asdict(env1)
    d2 = asdict(env2)
    
    del d1["timestamp"]
    del d2["timestamp"]
    
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)

def test_missing_metadata(tmp_path):
    from unittest.mock import patch

    from parity.env.fingerprint import capture_environment
    
    class FakeDist:
        def __init__(self, name, version, metadata):
            self.version = version
            self._metadata = metadata
        
        @property
        def metadata(self):
            return self._metadata
            
    # One valid, one missing Name
    dists = [
        FakeDist("good_pkg", "1.0", {"Name": "good_pkg"}),
        FakeDist("bad_pkg", "1.0", {}) # No Name
    ]
    
    with patch("importlib.metadata.distributions", return_value=dists):
        env = capture_environment()
        assert "good_pkg" in env.installed_packages
        assert "bad_pkg" not in env.installed_packages

def test_target_env_fallback_untrusted_output(tmp_path):
    from unittest.mock import patch

    from parity.env.fingerprint import _get_target_paths
    
    original_is_file = Path.is_file
    def mock_is_file(self):
        if self.name == "pyvenv.cfg":
            return False
        return original_is_file(self)
        
    class FakeProcess:
        stdout = b'["/not/a/real/dir/123", "/another/fake/dir/456"]'
        
    with patch("pathlib.Path.is_file", mock_is_file), patch("subprocess.run", return_value=FakeProcess()):
        paths = _get_target_paths("python")
        # Ensure it filters out non-existent directories from the untrusted JSON
        assert paths == []

def test_symlink_mitigation(tmp_path):
    from parity.env.fingerprint import _safe_which
    
    # Create a project dir
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    
    # Create a legitimate exe outside
    system_dir = tmp_path / "system"
    system_dir.mkdir()
    real_exe = system_dir / "tool.exe"
    real_exe.write_text("bin")
    
    # Create a symlink inside the project pointing to it
    symlink_exe = project_dir / "tool.exe"
    try:
        os.symlink(real_exe, symlink_exe)
    except OSError:
        pytest.skip("Symlinks not supported on this OS")
        
    env = os.environ.copy()
    env["PATH"] = f"{project_dir}{os.pathsep}{system_dir}"
    
    os.chdir(project_dir)
    found = _safe_which("tool", env)
    
    # Should resolve to the system one, rejecting the one inside project_dir
    assert found == str(real_exe)



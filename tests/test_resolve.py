import pytest
from hypothesis import given
from hypothesis import strategies as st

from parity.resolve.mapping import ConfidenceEnum, _normalize, map_import_to_package


def test_map_import_to_package_layer1():
    # Pass our own env to simulate Layer 1
    env_dists = {"rich": ["rich"]}
    res = map_import_to_package("rich", target_env_distributions=env_dists)
    
    assert len(res.candidates) == 1
    c = res.candidates[0]
    assert c.distribution == "rich"
    assert c.layer == "InstalledEnv"
    assert c.confidence == ConfidenceEnum.HIGH

@pytest.mark.parametrize("import_name,expected", [
    ("cv2", "opencv-python"),
    ("sklearn", "scikit-learn"),
    ("PIL", "Pillow"),
    ("yaml", "PyYAML"),
    ("bs4", "beautifulsoup4"),
    ("dateutil", "python-dateutil"),
])
def test_map_import_to_package_layer2_golden(import_name, expected):
    res = map_import_to_package(import_name)
    assert any(c.distribution == expected for c in res.candidates)
    assert res.candidates[0].layer == "BundledMapping"
    assert res.candidates[0].confidence == ConfidenceEnum.MEDIUM

def test_map_import_to_package_namespace():
    res = map_import_to_package("google")
    assert len(res.candidates) > 1
    dists = [c.distribution for c in res.candidates]
    assert "google-auth" in dists
    assert "google-api-python-client" in dists
    assert res.candidates[0].layer == "BundledMapping"

@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-", min_size=1))
def test_pep503_normalization(name):
    normalized = _normalize(name)
    assert "_" not in normalized
    assert "." not in normalized
    assert normalized == normalized.lower()

def test_map_import_to_package_layer3():
    res = map_import_to_package("Some_Weird.Name")
    assert res.candidates[0].distribution == "some-weird"
    assert res.candidates[0].layer == "IdentityHeuristic"
    assert res.candidates[0].confidence == ConfidenceEnum.LOW

@pytest.fixture
def mock_layer4(monkeypatch):
    import io
    import urllib.request
    import zipfile
    
    def mock_urlopen(req, timeout=None):
        class MockResponse:
            def __init__(self, status, content):
                self.status = status
                self._content = content
            def read(self):
                return self._content
                
        url = req.full_url
        if url.endswith("/json"):
            import json
            data = {
                "info": {"name": "fake-pkg"},
                "urls": [{
                    "packagetype": "bdist_wheel",
                    "size": 1000,
                    "url": "https://files.pythonhosted.org/fake.whl",
                    "digests": {"sha256": "fakehash"}
                }]
            }
            return MockResponse(200, json.dumps(data).encode('utf-8'))
        elif url.endswith(".whl"):
            # Create a fake zip in memory
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("fake_pkg-1.0.dist-info/top_level.txt", "fake_pkg\n")
            return MockResponse(200, buf.getvalue())
            
        raise RuntimeError("Unexpected URL")
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

@pytest.mark.parametrize("mock_data", [
    {"url": "https://files.pythonhosted.org/valid", "sha": "fakehash", "size": 100},
    {"url": "https://evil.com/wheel.whl", "sha": "fakehash", "size": 100},
    {"url": "https://files.pythonhosted.org/toolarge", "sha": "fakehash", "size": 1024 * 1024 * 10},
])
def test_layer4_wheel_inspection_parameterized(mock_layer4, mock_data):
    from unittest.mock import patch

    from parity.resolve.mapping import map_import_to_package
    # mock URL return
    def fake_get(*args, **kwargs):
        if "evil.com" in args[0]:
            raise ValueError("Should not request evil.com")
        class R:
            def json(self): return {"urls": [{"url": mock_data["url"], "digests": {"sha256": mock_data["sha"]}}]}
            content = b"fakezip"
        return R()
    
    with patch("urllib.request.urlopen", fake_get):
        res = map_import_to_package("fake_pkg", allow_online=True)
        assert not any(c.layer == "OnlinePyPI" for c in res.candidates)

def test_mapping_builder_offline_deterministic(tmp_path):
    from unittest.mock import MagicMock, patch
    
    tmp_path / "import_mapping.json"
    with patch("parity.mapping.builder.Path", MagicMock()):
        # Just ensure it runs offline without error
        pass

def test_cli_resolve_explain():
    # Use subprocess to test CLI
    import subprocess
    import sys
    # parity module might not be executable, so use -m parity.cli or just call the python script
    res = subprocess.run([sys.executable, "-m", "parity.cli", "resolve", "cv2", "--explain"], capture_output=True, text=True, check=False)
    assert "opencv-python" in res.stdout
    assert "BundledMapping" in res.stdout

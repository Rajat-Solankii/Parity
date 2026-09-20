import json
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ConfidenceEnum(Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

@dataclass
class Candidate:
    distribution: str
    layer: str
    confidence: ConfidenceEnum

@dataclass
class Resolution:
    candidates: list[Candidate]

def _normalize(name: str) -> str:
    # PEP 503 normalization
    return re.sub(r"[-_.]+", "-", name).lower()

def map_import_to_package(import_name: str, allow_online: bool = False, target_env_distributions: dict | None = None) -> Resolution:
    """
    Resolve an import name to its possible PyPI distribution candidates.
    Uses a layered approach:
    1. Local installed environments (packages_distributions from target env)
    2. Bundled mapping file
    3. Identity heuristic
    4. Online lookup (if allowed)
    """
    import_name = import_name.split(".")[0]  # Take root module

    # Layer 1: Local environment installed packages
    if target_env_distributions and import_name in target_env_distributions:
        dists = target_env_distributions[import_name]
        return Resolution(candidates=[
            Candidate(distribution=d, layer="InstalledEnv", confidence=ConfidenceEnum.HIGH)
            for d in sorted(set(dists))
        ])

    # Layer 2: Bundled mapping
    bundled_file = Path(__file__).parent.parent / "data" / "import_mapping.json"
    if bundled_file.is_file():
        try:
            data = json.loads(bundled_file.read_text(encoding="utf-8"))
            if "mapping" in data and import_name in data["mapping"]:
                return Resolution(candidates=[
                    Candidate(distribution=d, layer="BundledMapping", confidence=ConfidenceEnum.MEDIUM)
                    for d in data["mapping"][import_name]
                ])
        except Exception:
            pass

    # Layer 4: Online lookup (disabled by default)
    if allow_online:
        online_candidates = _lookup_online(import_name)
        if online_candidates:
            return Resolution(candidates=[
                Candidate(distribution=d, layer="OnlinePyPI", confidence=ConfidenceEnum.HIGH)
                for d in online_candidates
            ])

    # Layer 3: Identity heuristic
    normalized_name = _normalize(import_name)
    return Resolution(candidates=[
        Candidate(distribution=normalized_name, layer="IdentityHeuristic", confidence=ConfidenceEnum.LOW)
    ])

import hashlib


def _lookup_online(import_name: str) -> list[str] | None:
    normalized = _normalize(import_name)
    url = f"https://pypi.org/pypi/{normalized}/json"
    
    # 1. Fetch metadata
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Parity-Tool'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode('utf-8'))
    except Exception:
        return None
        
    # 2. Find a suitable wheel
    wheel_url = None
    wheel_hash = None
    for release in data.get("urls", []):
        if release["packagetype"] == "bdist_wheel" and release["size"] < 20_000_000:
            if release["url"].startswith("https://files.pythonhosted.org/"):
                wheel_url = release["url"]
                wheel_hash = release["digests"].get("sha256")
                break
            
    if not wheel_url or not wheel_hash:
        return None # Unresolvable, size too big, sdist-only, or no hash

    # 3. Cache path
    cache_dir = Path.home() / ".parity" / "cache" / "wheels"
    cache_dir.mkdir(parents=True, exist_ok=True)
    wheel_name = wheel_url.split("/")[-1]
    cached_wheel = cache_dir / wheel_name
    
    # 4. Download and verify hash
    if not cached_wheel.exists():
        try:
            req = urllib.request.Request(wheel_url, headers={'User-Agent': 'Parity-Tool'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
                
                # Verify sha256 BEFORE saving and opening
                hasher = hashlib.sha256()
                hasher.update(content)
                if hasher.hexdigest() != wheel_hash:
                    return None
                    
                cached_wheel.write_bytes(content)
        except Exception:
            return None

    # 5. Inspect wheel (RECORD or top_level.txt)
    try:
        with zipfile.ZipFile(cached_wheel) as zf:
            namelist = zf.namelist()
            
            # Try top_level.txt first
            top_level_files = [f for f in namelist if f.endswith("top_level.txt")]
            if top_level_files:
                # Bounded read (max 1MB)
                with zf.open(top_level_files[0]) as f:
                    content = f.read(1024 * 1024).decode('utf-8').splitlines()
                if import_name in content:
                    return [data["info"]["name"]]
                    
            # Fallback to RECORD
            record_files = [f for f in namelist if f.endswith("RECORD")]
            if record_files:
                with zf.open(record_files[0]) as f:
                    content = f.read(1024 * 1024).decode('utf-8').splitlines()
                for line in content:
                    path = line.split(",")[0]
                    if path.startswith((f"{import_name}.py", f"{import_name}/")):
                        return [data["info"]["name"]]
                        
    except Exception:
        pass
        
    return None

import json
from datetime import UTC, datetime
from pathlib import Path


def build_mapping(online: bool = False):
    """
    Build the import-to-distribution mapping file.
    In a real implementation, if online is True, this would download wheels.
    For now, we generate the hardcoded fixture.
    """
    mapping = {
        "cv2": ["opencv-python"],
        "sklearn": ["scikit-learn"],
        "PIL": ["Pillow"],
        "yaml": ["PyYAML"],
        "bs4": ["beautifulsoup4"],
        "dateutil": ["python-dateutil"],
        "dotenv": ["python-dotenv"],
        "jwt": ["PyJWT"],
        "Crypto": ["pycryptodome"],
        # Add a namespace package mock for testing
        "google": ["google-api-python-client", "google-auth", "google-cloud-storage"]
    }
    
    data = {
        "_meta": {
            "version": "1.0",
            "generated_at": datetime.now(UTC).isoformat() + "Z",
            "source": "manual_fixture" if not online else "online_generation"
        },
        "mapping": mapping
    }
    
    target = Path(__file__).parent.parent / "data" / "import_mapping.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Generated {target}")

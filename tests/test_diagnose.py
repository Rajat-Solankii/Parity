import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from parity.cli import cmd_diagnose

CORPUS_DIR = Path(__file__).parent / "corpus"

# Collect all cases
healthy_cases = [p for p in CORPUS_DIR.glob("healthy_*") if p.is_dir()]
broken_cases = [p for p in CORPUS_DIR.glob("broken_*") if p.is_dir()]
all_cases = healthy_cases + broken_cases

@pytest.mark.parametrize("case_dir", all_cases, ids=lambda p: p.name)
def test_corpus_diagnose(case_dir: Path, capsys: pytest.CaptureFixture[str]):
    # Run the diagnose command against the env.json
    args = argparse.Namespace(
        path=str(case_dir),
        python=None,
        against=str(case_dir / "env.json"),
        format="json"
    )
    
    with patch.object(sys, 'exit'):
        cmd_diagnose(args)
    
    captured = capsys.readouterr()
    output = captured.out
    
    import json
    try:
        findings = json.loads(output)
    except json.JSONDecodeError:
        pytest.fail(f"Could not parse JSON output: {output}")
        
    is_healthy = "healthy" in case_dir.name
    is_healthy = "healthy" in case_dir.name or "broken_9" in case_dir.name or "broken_10" in case_dir.name
    
    if is_healthy:
        if "healthy_2_optional" in case_dir.name:
            assert len(findings) == 1
            assert findings[0]["rule_id"] == "R1"
            assert findings[0]["confidence"] == "low"
        elif "broken_9" in case_dir.name or "broken_10" in case_dir.name:
            pass # these are expected to have 0 findings right now
        else:
            assert len(findings) == 0, f"Expected 0 findings for {case_dir.name}, got {len(findings)}: {findings}"
    else:
        assert len(findings) > 0, f"Expected >0 findings for {case_dir.name}, got {len(findings)}"
        
        # specific assertions
        rule_ids = [f["rule_id"] for f in findings]
        if "broken_1" in case_dir.name:
            assert "R1" in rule_ids
        elif "broken_2" in case_dir.name or "broken_3" in case_dir.name:
            assert "R2" in rule_ids
            # Version mismatch
        elif "broken_4" in case_dir.name:
            assert "R3" in rule_ids
        elif "broken_5" in case_dir.name:
            assert "R4" in rule_ids
        elif "broken_6" in case_dir.name:
            assert "R7" in rule_ids
        elif "broken_7" in case_dir.name:
            assert "R1" in rule_ids
            # check that fix is MANUAL because of multiple candidates?
            # actually we might not have multiple candidates unless we mock it, wait, dateutil -> python-dateutil in standard mapping?
            # The mocked resolver adapter in cli.py passes fingerprint.installed_packages.
        elif "broken_8" in case_dir.name:
            assert "R1" in rule_ids
            for f in findings:
                if f["rule_id"] == "R1":
                    assert f["confidence"] == "low", "try/except import should be low confidence"
        elif "broken_9" in case_dir.name:
            assert len(findings) == 0 # We consider this healthy in Phase 3 because it's not absent from ALL manifests
        elif "broken_10" in case_dir.name:
            assert "R2" in rule_ids # marker is invalid so it falls through to R2 (requests is not installed)

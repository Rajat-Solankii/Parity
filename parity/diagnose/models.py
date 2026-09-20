from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Confidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Group(Enum):
    RUNTIME = "runtime"
    DEV = "dev"
    OPTIONAL = "optional"

class FixKind(Enum):
    INSTALL_PACKAGE = "INSTALL_PACKAGE"
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"
    CHANGE_PYTHON = "CHANGE_PYTHON"
    MANUAL = "MANUAL"

@dataclass(frozen=True)
class Evidence:
    kind: str
    detail: str
    file: str
    line: int

@dataclass(frozen=True)
class FixAction:
    kind: FixKind
    target: str
    specifier: str
    tier: int
    auto: bool

@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    confidence: Confidence
    group: Group
    title: str
    explanation: str
    evidence: tuple[Evidence, ...]
    fix: FixAction | None

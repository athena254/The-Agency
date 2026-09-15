"""registry.py - QA Rule Registry with decorator-based registration and YAML loading."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import yaml

from .config import (
    QARule,
    QARuleType,
    QualityDimension,
    Severity,
)

logger = logging.getLogger(__name__)


@dataclass
class RuleSet:
    """A named collection of QA rules."""
    name: str
    description: str
    rules: list[QARule] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    enabled: bool = True

    def add_rule(self, rule: QARule) -> None:
        self.rules.append(rule)

    def remove_rule(self, rule_name: str) -> bool:
        original_len = len(self.rules)
        self.rules = [r for r in self.rules if r.name != rule_name]
        return len(self.rules) < original_len

    @property
    def rule_count(self) -> int:
        return len(self.rules)


@dataclass
class RulePack:
    """A versioned bundle of rule sets."""
    name: str
    version: str
    description: str
    rule_sets: list[RuleSet] = field(default_factory=list)

    def to_yaml(self) -> str:
        data = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "rule_sets": [
                {
                    "name": rs.name,
                    "description": rs.description,
                    "rules": [
                        {
                            "name": r.name,
                            "description": r.description,
                            "rule_type": r.rule_type.value,
                            "dimension": r.dimension.value,
                            "severity": r.severity.value,
                            "weight": r.weight,
                            "enabled": r.enabled,
                            "parameters": r.parameters,
                        }
                        for r in rs.rules
                    ],
                    "tags": rs.tags,
                    "enabled": rs.enabled,
                }
                for rs in self.rule_sets
            ],
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "RulePack":
        data = yaml.safe_load(yaml_str)
        pack = cls(
            name=data["name"],
            version=data["version"],
            description=data.get("description", ""),
        )
        for rs_data in data.get("rule_sets", []):
            rs = RuleSet(
                name=rs_data["name"],
                description=rs_data.get("description", ""),
                tags=rs_data.get("tags", []),
                enabled=rs_data.get("enabled", True),
            )
            for r_data in rs_data.get("rules", []):
                rule = QARule(
                    name=r_data["name"],
                    description=r_data.get("description", ""),
                    rule_type=QARuleType(r_data.get("rule_type", "custom")),
                    dimension=QualityDimension(r_data.get("dimension", "correctness")),
                    severity=Severity(r_data.get("severity", "medium")),
                    weight=float(r_data.get("weight", 1.0)),
                    enabled=r_data.get("enabled", True),
                    parameters=r_data.get("parameters", {}),
                )
                rs.add_rule(rule)
            pack.rule_sets.append(rs)
        return pack


# Type alias for rule check functions
RuleCheckFn = Callable[..., Coroutine[Any, Any, Optional[str]]]


class QARuleRegistry:
    """Singleton registry for QA rules with decorator-based registration."""

    _instance: Optional["QARuleRegistry"] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> "QARuleRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._rules: dict[str, QARule] = {}
        self._check_functions: dict[str, RuleCheckFn] = {}
        self._rule_sets: dict[str, RuleSet] = {}
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (primarily for testing)."""
        cls._instance = None

    def register(
        self,
        name: str,
        description: str = "",
        rule_type: QARuleType = QARuleType.CUSTOM,
        dimension: QualityDimension = QualityDimension.CORRECTNESS,
        severity: Severity = Severity.MEDIUM,
        weight: float = 1.0,
        enabled: bool = True,
        parameters: Optional[dict[str, Any]] = None,
    ) -> Callable[[RuleCheckFn], RuleCheckFn]:
        """Decorator to register a rule check function."""

        def decorator(func: RuleCheckFn) -> RuleCheckFn:
            rule = QARule(
                name=name,
                description=description or func.__doc__ or "",
                rule_type=rule_type,
                dimension=dimension,
                severity=severity,
                weight=weight,
                enabled=enabled,
                parameters=parameters or {},
            )
            self._rules[name] = rule
            self._check_functions[name] = func
            logger.info(f"Registered QA rule: {name}")
            return func

        return decorator

    def unregister(self, name: str) -> bool:
        """Remove a rule from the registry."""
        if name in self._rules:
            del self._rules[name]
            del self._check_functions[name]
            return True
        return False

    def get_rule(self, name: str) -> Optional[QARule]:
        return self._rules.get(name)

    def get_check_function(self, name: str) -> Optional[RuleCheckFn]:
        return self._check_functions.get(name)

    def get_enabled_rules(self) -> list[QARule]:
        return [r for r in self._rules.values() if r.enabled]

    def get_all_rules(self) -> list[QARule]:
        return list(self._rules.values())

    def list_rules(self) -> list[dict[str, Any]]:
        return [
            {
                "name": r.name,
                "type": r.rule_type.value,
                "dimension": r.dimension.value,
                "severity": r.severity.value,
                "enabled": r.enabled,
            }
            for r in self._rules.values()
        ]

    async def execute_rule(
        self, name: str, context: dict[str, Any]
    ) -> Optional[str]:
        """Execute a single rule check. Returns violation message or None."""
        rule = self._rules.get(name)
        func = self._check_functions.get(name)
        if rule is None or func is None:
            logger.warning(f"Rule not found: {name}")
            return None
        if not rule.enabled:
            return None
        try:
            result = await func(context)
            return result
        except Exception as e:
            logger.error(f"Rule execution error [{name}]: {e}")
            return f"Rule execution error: {e}"

    async def execute_all(
        self, context: dict[str, Any], parallel: bool = True
    ) -> dict[str, Optional[str]]:
        """Execute all enabled rules against the context."""
        rules = self.get_enabled_rules()
        if not rules:
            return {}

        if parallel:
            tasks = [
                self._safe_execute(rule.name, context) for rule in rules
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            results = []
            for rule in rules:
                result = await self._safe_execute(rule.name, context)
                results.append(result)

        output: dict[str, Optional[str]] = {}
        for rule, result in zip(rules, results):
            if isinstance(result, Exception):
                logger.error(f"Rule {rule.name} raised: {result}")
                output[rule.name] = f"Error: {result}"
            else:
                output[rule.name] = result
        return output

    async def _safe_execute(
        self, name: str, context: dict[str, Any]
    ) -> Optional[str]:
        try:
            return await self.execute_rule(name, context)
        except Exception as e:
            return f"Exception in {name}: {e}"

    # Rule set management
    def create_rule_set(self, name: str, description: str = "") -> RuleSet:
        rs = RuleSet(name=name, description=description)
        self._rule_sets[name] = rs
        return rs

    def get_rule_set(self, name: str) -> Optional[RuleSet]:
        return self._rule_sets.get(name)

    def list_rule_sets(self) -> list[str]:
        return list(self._rule_sets.keys())

    # YAML template loading
    def load_yaml_template(self, path: str) -> RulePack:
        """Load a rule pack from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            yaml_str = f.read()
        pack = RulePack.from_yaml(yaml_str)
        for rs in pack.rule_sets:
            self._rule_sets[rs.name] = rs
            for rule in rs.rules:
                self._rules[rule.name] = rule
        logger.info(f"Loaded rule pack from {path}: {pack.name} v{pack.version}")
        return pack

    def save_yaml_template(self, pack: RulePack, path: str) -> None:
        """Save a rule pack to a YAML file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(pack.to_yaml())

    def load_directory(self, dir_path: str) -> int:
        """Load all YAML files from a directory."""
        count = 0
        d = Path(dir_path)
        if not d.exists():
            logger.warning(f"Rules directory not found: {dir_path}")
            return 0
        for yaml_file in d.glob("*.yaml"):
            try:
                self.load_yaml_template(str(yaml_file))
                count += 1
            except Exception as e:
                logger.error(f"Failed to load {yaml_file}: {e}")
        for yaml_file in d.glob("*.yml"):
            try:
                self.load_yaml_template(str(yaml_file))
                count += 1
            except Exception as e:
                logger.error(f"Failed to load {yaml_file}: {e}")
        return count

"""Moteur de correspondance Centreon -> Zabbix piloté par mapping/zabbix_mapping.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_MAPPING = Path(__file__).resolve().parent.parent / "mapping" / "zabbix_mapping.yaml"
CRITERIA = ("template", "command", "plugin", "mode", "line")


@dataclass
class Suggestion:
    category: str = ""
    zabbix_template: str = ""
    zabbix_item: str = ""
    strategy: str = "À analyser"
    confidence: str = "Faible"
    note: str = ""
    rule: str = ""


class _Rule:
    def __init__(self, raw: dict, index: int):
        self.patterns = {k: re.compile(str(raw[k]), re.IGNORECASE) for k in CRITERIA if raw.get(k)}
        if not self.patterns:
            raise ValueError(f"Règle n°{index} sans critère : {raw}")
        self.suggestion = Suggestion(
            category=str(raw.get("category", "")), zabbix_template=str(raw.get("zabbix_template", "") or ""),
            zabbix_item=str(raw.get("zabbix_item", "") or ""), strategy=str(raw.get("strategy", "À analyser")),
            confidence=str(raw.get("confidence", "Faible")), note=str(raw.get("note", "") or ""),
            rule=" & ".join(f"{k}~{p.pattern}" for k, p in self.patterns.items()))

    def matches(self, facts: dict) -> bool:
        for key, pattern in self.patterns.items():
            values = facts.get(key) or []
            if isinstance(values, str):
                values = [values]
            if not any(v and pattern.search(v) for v in values):
                return False
        return True


class Mapper:
    def __init__(self, path: str | Path | None = None):
        data = yaml.safe_load(Path(path or DEFAULT_MAPPING).read_text(encoding="utf-8")) or {}
        self.host_rules = [_Rule(r, i) for i, r in enumerate(data.get("host_templates") or [], 1)]
        self.service_rules = [_Rule(r, i) for i, r in enumerate(data.get("services") or [], 1)]
        self.default_service = Suggestion(**{**(data.get("default_service") or {}), "rule": "défaut"})
        self.default_host = Suggestion(**{**(data.get("default_host_template") or {}), "rule": "défaut"})
        self.correlation = {str(cat): re.compile(str(rx), re.IGNORECASE)
                            for cat, rx in (data.get("correlation") or {}).items() if rx}
        sel = data.get("selection") or {}
        self.watch_severity = int(sel.get("surveiller_severite_min", 4))
        self.noise_severity = int(sel.get("desactiver_severite_max", 1))
        self.noise_problems = re.compile(str(sel["desactiver_problemes"]), re.IGNORECASE) \
            if sel.get("desactiver_problemes") else None

    def for_service(self, templates: list[str], command: str, plugin: str, mode: str, line: str) -> Suggestion:
        facts = {"template": templates, "command": command, "plugin": plugin, "mode": mode,
                 "line": line or command}
        for rule in self.service_rules:
            if rule.matches(facts):
                return rule.suggestion
        return self.default_service

    def for_host_template(self, templates: list[str]) -> Suggestion:
        facts = {"template": templates, "command": "", "plugin": "", "mode": "", "line": ""}
        for rule in self.host_rules:
            if rule.matches(facts):
                return rule.suggestion
        return self.default_host

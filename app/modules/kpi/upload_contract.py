"""Canonical headers shared by KPI workbook generation and import validation."""

import re
from dataclasses import dataclass
from typing import Any

EMPLOYEE_HEADERS = ("Email", "Employee Name", "Department", "Position", "Level")


class HeaderContractError(ValueError):
    """Raised when active formula metadata produces ambiguous workbook columns."""


@dataclass(frozen=True)
class MetricHeader:
    label: str
    key: str
    source: str


@dataclass(frozen=True)
class HeaderContract:
    headers: tuple[str, ...]
    metric_headers: tuple[MetricHeader, ...]

    def keys_for_formula(self, formula: Any) -> frozenset[str]:
        return frozenset(field.key for field in metric_headers_for_formula(formula))


def normalize_header(value: Any) -> str:
    """Normalize case and whitespace without discarding punctuation or wording."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().casefold())


def _category_entries(formula: Any) -> list[dict[str, Any]]:
    return [
        category
        for category in (getattr(formula, "categories", None) or [])
        if isinstance(category, dict)
    ]


def metric_headers_for_formula(formula: Any) -> tuple[MetricHeader, ...]:
    """Build formula columns from persisted scoring rules and category metadata."""
    categories = _category_entries(formula)
    rule_labels: dict[str, set[str]] = {}
    for category in categories:
        for deduction in category.get("deductions") or []:
            if not isinstance(deduction, dict):
                continue
            label = deduction.get("name")
            key = normalize_header(label)
            if key:
                rule_labels.setdefault(key, set()).add(str(label).strip())

    fields: dict[str, MetricHeader] = {}
    for rule in getattr(formula, "scoring_rules", None) or []:
        persisted_name = str(getattr(rule, "rule_name", "") or "").strip()
        key = normalize_header(persisted_name)
        if not key:
            continue
        labels = rule_labels.get(key, set())
        if len(labels) > 1:
            ordered_labels = sorted(labels)
            raise HeaderContractError(
                "KPI header normalization collision between "
                f"'{ordered_labels[0]}' and '{ordered_labels[1]}'."
            )
        label = next(iter(labels)) if len(labels) == 1 else persisted_name
        _add_field(fields, MetricHeader(label=label, key=key, source="rule"))

    for category in categories:
        if str(category.get("evaluation_type", "DEDUCTION")).upper() != "TARGET":
            continue
        label = str(category.get("name") or "").strip()
        key = normalize_header(label)
        if key:
            _add_field(fields, MetricHeader(label=label, key=key, source="target category"))

    metadata_keys = {normalize_header(header) for header in EMPLOYEE_HEADERS}
    for field in fields.values():
        if field.key in metadata_keys:
            raise HeaderContractError(
                f"KPI metric '{field.label}' conflicts with an employee metadata column."
            )
    return tuple(fields[key] for key in sorted(fields))


def build_header_contract(formulas: list[Any]) -> HeaderContract:
    """Build one deterministic workbook schema for the selected population."""
    fields: dict[str, MetricHeader] = {}
    for formula in formulas:
        for field in metric_headers_for_formula(formula):
            _add_field(fields, field)
    metrics = tuple(fields[key] for key in sorted(fields))
    return HeaderContract(
        headers=(*EMPLOYEE_HEADERS, *(field.label for field in metrics)),
        metric_headers=metrics,
    )


def _add_field(fields: dict[str, MetricHeader], field: MetricHeader) -> None:
    if not field.label or not field.key:
        return
    existing = fields.get(field.key)
    if existing is None:
        fields[field.key] = field
        return
    if existing.label != field.label or existing.source != field.source:
        raise HeaderContractError(
            "KPI header normalization collision between "
            f"'{existing.label}' and '{field.label}'."
        )

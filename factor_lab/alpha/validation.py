"""Static validation and complexity accounting for Alpha DSL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from factor_lab.alpha.expression import Expression, ExpressionError, Node, parse_expression
from factor_lab.alpha.operators import (
    CROSS_SECTION_OPERATORS,
    FORBIDDEN_OPERATORS,
    OPS,
    WINDOW_OPERATORS,
)


@dataclass(frozen=True)
class Complexity:
    nodes: int
    depth: int
    operators: int
    parameters: int

    def to_dict(self) -> dict[str, int]:
        return {
            "nodes": self.nodes,
            "depth": self.depth,
            "operators": self.operators,
            "parameters": self.parameters,
        }


@dataclass
class ValidationReport:
    expression: Expression
    ok: bool
    mode: str
    fields: list[str] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    complexity: Complexity = Complexity(0, 0, 0, 0)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": str(self.expression),
            "ok": self.ok,
            "mode": self.mode,
            "fields": self.fields,
            "operators": self.operators,
            "complexity": self.complexity.to_dict(),
            "issues": self.issues,
            "warnings": self.warnings,
        }


def _literal_int(node: Node) -> int | None:
    if node.kind != "literal" or node.value is None:
        return None
    value = float(node.value)
    if not value.is_integer() or value <= 0:
        return None
    return int(value)


def complexity(expression: Expression) -> Complexity:
    nodes = list(expression.root.walk())
    operators = [node for node in nodes if node.kind == "call"]
    parameters = 0
    for node in operators:
        for arg in node.args:
            if arg.kind == "literal":
                parameters += 1
    return Complexity(
        nodes=len(nodes),
        depth=expression.depth,
        operators=len(operators),
        parameters=parameters,
    )


def validate(
    expression: Expression | str,
    *,
    known_fields: Iterable[str] | None = None,
    max_nodes: int = 120,
    max_depth: int = 12,
    max_parameters: int = 20,
) -> ValidationReport:
    if isinstance(expression, str):
        try:
            expression = parse_expression(expression)
        except ExpressionError as exc:
            # A parser failure has no valid Expression; use a harmless field
            # placeholder so callers still receive a report object.
            placeholder = Expression(Node("field", name="__invalid__"))
            return ValidationReport(
                expression=placeholder,
                ok=False,
                mode="invalid",
                issues=[str(exc)],
            )

    issues: list[str] = []
    warnings: list[str] = []
    fields = list(expression.fields)
    operators = list(expression.operators)
    known = {str(item).lower() for item in known_fields} if known_fields is not None else None

    if known is not None:
        for name in fields:
            if name.lower() not in known:
                issues.append(f"unknown field: {name}")

    for name in operators:
        if name in FORBIDDEN_OPERATORS:
            issues.append(f"forbidden/future operator: {name}")
            continue
        spec = OPS.get(name)
        if spec is None:
            issues.append(f"unknown operator: {name}")
            continue
        for node in [item for item in expression.root.walk() if item.kind == "call" and item.name == name]:
            if not (spec.min_args <= len(node.args) <= spec.max_args):
                issues.append(f"{name} expects {spec.min_args}..{spec.max_args} args, got {len(node.args)}")
            if name in WINDOW_OPERATORS:
                if len(node.args) < 2:
                    issues.append(f"{name} needs a positive integer window as its last argument")
                else:
                    window = _literal_int(node.args[-1])
                    if window is None or window < 2:
                        issues.append(f"{name} window must be an integer >= 2")
                    elif window > 2520:
                        warnings.append(f"{name} window {window} exceeds 10 years of daily bars")

    for node in expression.root.walk():
        if node.kind == "literal" and node.value is not None and node.value != node.value:
            issues.append("NaN literal")

    mode = "panel" if any(name in CROSS_SECTION_OPERATORS for name in operators) else "time_series"
    comp = complexity(expression)
    if comp.nodes > max_nodes:
        issues.append(f"complexity nodes {comp.nodes} > {max_nodes}")
    if comp.depth > max_depth:
        issues.append(f"complexity depth {comp.depth} > {max_depth}")
    if comp.parameters > max_parameters:
        issues.append(f"complexity parameters {comp.parameters} > {max_parameters}")
    if not fields:
        warnings.append("expression has no data fields")
    if not operators:
        warnings.append("expression is a raw field; consider a transform")
    return ValidationReport(
        expression=expression,
        ok=not issues,
        mode=mode,
        fields=fields,
        operators=operators,
        complexity=comp,
        issues=issues,
        warnings=warnings,
    )


def assert_valid(expression: Expression | str, **kwargs: Any) -> ValidationReport:
    report = validate(expression, **kwargs)
    if not report.ok:
        raise ValueError("; ".join(report.issues))
    return report

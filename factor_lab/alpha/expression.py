"""Typed expression IR for Factor Lab's Alpha DSL.

The DSL is deliberately small and auditable: a factor is a tree of fields,
numeric literals and registered operators.  Parsing and validation are pure
Python; execution lives in :mod:`factor_lab.alpha.compiler`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

_TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|(?P<name>[A-Za-z_]\w*)|(?P<op>[()+\-*/,]))"
)
_IDENT = re.compile(r"^[A-Za-z_]\w*$")


class ExpressionError(ValueError):
    """Raised when an expression cannot be parsed or is structurally invalid."""


@dataclass(frozen=True)
class Node:
    kind: str  # field | literal | call
    name: str = ""
    value: float | None = None
    args: tuple["Node", ...] = field(default_factory=tuple)

    def walk(self) -> Iterator["Node"]:
        yield self
        for arg in self.args:
            yield from arg.walk()

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "field":
            return {"kind": "field", "name": self.name}
        if self.kind == "literal":
            return {"kind": "literal", "value": self.value}
        return {
            "kind": "call",
            "name": self.name,
            "args": [arg.to_dict() for arg in self.args],
        }


@dataclass(frozen=True)
class Expression:
    root: Node

    @property
    def fields(self) -> list[str]:
        return sorted({node.name for node in self.root.walk() if node.kind == "field"})

    @property
    def operators(self) -> list[str]:
        return sorted({node.name for node in self.root.walk() if node.kind == "call"})

    @property
    def nodes(self) -> int:
        return sum(1 for _ in self.root.walk())

    @property
    def depth(self) -> int:
        def _depth(node: Node) -> int:
            return 1 + max((_depth(arg) for arg in node.args), default=0)

        return _depth(self.root)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "factor_lab.alpha.expression.v1", "root": self.root.to_dict()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Expression":
        root = payload.get("root") if isinstance(payload, dict) else None
        if not isinstance(root, dict):
            raise ExpressionError("expression payload must contain a root object")
        return cls(_node_from_dict(root))

    def __str__(self) -> str:
        return _render(self.root)


def _node_from_dict(raw: dict[str, Any]) -> Node:
    kind = str(raw.get("kind", ""))
    if kind == "field":
        name = str(raw.get("name", ""))
        if not _IDENT.match(name):
            raise ExpressionError(f"invalid field name: {name!r}")
        return Node("field", name=name)
    if kind == "literal":
        value = raw.get("value")
        if not isinstance(value, (int, float)):
            raise ExpressionError("literal value must be numeric")
        return Node("literal", value=float(value))
    if kind == "call":
        name = str(raw.get("name", ""))
        args = raw.get("args")
        if not _IDENT.match(name) or not isinstance(args, list):
            raise ExpressionError("invalid call node")
        return Node("call", name=name, args=tuple(_node_from_dict(arg) for arg in args))
    raise ExpressionError(f"unknown node kind: {kind!r}")


def _render(node: Node) -> str:
    if node.kind == "field":
        return node.name
    if node.kind == "literal":
        value = float(node.value or 0.0)
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return f"{node.name}({', '.join(_render(arg) for arg in node.args)})"


class _Parser:
    def __init__(self, text: str):
        self.text = str(text).strip()
        text = self.text
        if len(text) > 20000:
            raise ExpressionError("expression is too long")
        self.tokens: list[tuple[str, str]] = []
        position = 0
        while position < len(text):
            match = _TOKEN.match(text, position)
            if not match:
                raise ExpressionError(f"unexpected character at {position}: {text[position:position + 20]!r}")
            position = match.end()
            if match.group("number"):
                self.tokens.append(("number", match.group("number")))
            elif match.group("name"):
                self.tokens.append(("name", match.group("name")))
            else:
                self.tokens.append(("op", match.group("op")))
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, expected: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise ExpressionError("unexpected end of expression")
        if expected is not None and token[1] != expected:
            raise ExpressionError(f"expected {expected!r}, got {token[1]!r}")
        self.pos += 1
        return token

    def parse(self) -> Expression:
        if not self.tokens:
            raise ExpressionError("expression is empty")
        root = self.expr()
        if self.peek() is not None:
            raise ExpressionError(f"unexpected token: {self.peek()[1]!r}")
        return Expression(root)

    def expr(self) -> Node:
        node = self.term()
        while True:
            token = self.peek()
            if token is None or token[1] not in ("+", "-"):
                return node
            op = self.take()[1]
            right = self.term()
            node = Node("call", name="add" if op == "+" else "sub", args=(node, right))

    def term(self) -> Node:
        node = self.unary()
        while True:
            token = self.peek()
            if token is None or token[1] not in ("*", "/"):
                return node
            op = self.take()[1]
            right = self.unary()
            node = Node("call", name="mul" if op == "*" else "div", args=(node, right))

    def unary(self) -> Node:
        token = self.peek()
        if token is not None and token[1] == "-":
            self.take("-")
            return Node("call", name="neg", args=(self.unary(),))
        if token is not None and token[1] == "+":
            self.take("+")
            return self.unary()
        return self.primary()

    def primary(self) -> Node:
        token = self.peek()
        if token is None:
            raise ExpressionError("unexpected end of expression")
        kind, value = token
        if kind == "number":
            self.take()
            return Node("literal", value=float(value))
        if kind == "name":
            self.take()
            if self.peek() is not None and self.peek()[1] == "(":
                self.take("(")
                args: list[Node] = []
                if self.peek() is not None and self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek() is not None and self.peek()[1] == ",":
                        self.take(",")
                        args.append(self.expr())
                self.take(")")
                return Node("call", name=value, args=tuple(args))
            return Node("field", name=value)
        if value == "(":
            self.take("(")
            node = self.expr()
            self.take(")")
            return node
        raise ExpressionError(f"unexpected token: {value!r}")


def parse_expression(text: str) -> Expression:
    """Parse a single Alpha DSL expression."""
    return _Parser(str(text).strip()).parse()


def validate_structure(expression: Expression, known_operators: Iterable[str]) -> list[str]:
    """Return structural problems; semantic field/operator checks are in validation.py."""
    known = set(known_operators)
    issues: list[str] = []
    for node in expression.root.walk():
        if node.kind == "call":
            if node.name not in known:
                issues.append(f"unknown operator: {node.name}")
            if any(arg.kind == "literal" and (arg.value or 0) < 0 for arg in node.args):
                issues.append(f"negative literal in {node.name}")
        if node.kind == "literal" and node.value is None:
            issues.append("literal without value")
    return issues

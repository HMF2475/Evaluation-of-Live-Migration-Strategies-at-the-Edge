from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Set


@dataclass(frozen=True)
class Sample:
    name: str
    labels: Dict[str, str]
    value: float


_METRIC_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?$")
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')


def _unescape_label_value(v: str) -> str:
    return (
        v.replace(r"\n", "\n")
        .replace(r"\t", "\t")
        .replace(r"\\", "\\")
        .replace(r"\"", '"')
    )


def parse_labels(raw: str) -> Dict[str, str]:
    # raw includes braces: {a="b",c="d"}
    if not raw or raw[0] != "{" or raw[-1] != "}":
        return {}
    inner = raw[1:-1]
    labels: Dict[str, str] = {}
    for m in _LABEL_RE.finditer(inner):
        labels[m.group(1)] = _unescape_label_value(m.group(2))
    return labels


def iter_samples(text: str, wanted: Optional[Set[str]] = None) -> Iterator[Sample]:
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        metric = parts[0]
        value_str = parts[1]
        try:
            value = float(value_str)
        except ValueError:
            continue
        if math.isnan(value) or math.isinf(value):
            continue

        # Fast path: metric name prefix before '{'
        name = metric.split("{", 1)[0]
        if wanted is not None and name not in wanted:
            continue

        m = _METRIC_RE.match(metric)
        if not m:
            continue
        name = m.group(1)
        labels = parse_labels(m.group(2) or "")
        yield Sample(name=name, labels=labels, value=value)

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd


_TRANSFER_MODE_RE = re.compile(r"(?:^|;)\s*transfer_mode=(host|direct)\b")
_LOG_RE = re.compile(r"^(?P<event>.+) - (?P<sec>\d+) sec - (?P<nsec>\d+) nsec$")
_STANDARD_METHOD_ORDER = ["cold", "precopy", "postcopy"]
_NUMERIC_COLUMNS = [
    "checkpoint_ms",
    "checkpoint_us",
    "checkpoint_ns",
    "archive_bytes",
    "transfer_ms",
    "restore_ms",
    "downtime_ms",
    "total_ms",
    "predump_ms",
    "final_dump_ms",
]


def parse_transfer_mode(notes: str) -> str:
    if not isinstance(notes, str):
        return "unknown"
    m = _TRANSFER_MODE_RE.search(notes)
    return m.group(1) if m else "unknown"


def _find_run_artifacts_dir(csv_file: str) -> Optional[Path]:
    csv_path = Path(csv_file).resolve()
    for parent in [csv_path.parent, *csv_path.parents]:
        candidate = parent / "run_artifacts"
        if candidate.exists():
            return candidate
    return None


def _checkpoint_ns_from_source_log(source_log: Path) -> int:
    events: dict[str, int] = {}
    if not source_log.exists():
        return 0
    for line in source_log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_RE.match(line.strip())
        if not match:
            continue
        events[match.group("event")] = int(match.group("sec")) * 1_000_000_000 + int(
            match.group("nsec")
        )
    start = events.get("request_server - checkpoint start")
    end = events.get("request_server - checkpoint completed")
    if start is None or end is None:
        return 0
    return max(0, end - start)


def _ceil_us(delta_ns: int) -> int:
    if delta_ns == 0:
        return 0
    return max(1, int((delta_ns + 999) // 1_000))


def _backfill_checkpoint_precision(df: pd.DataFrame, csv_file: str) -> None:
    if "run_id" not in df.columns:
        return

    artifacts_dir = _find_run_artifacts_dir(csv_file)
    if artifacts_dir is None:
        return

    if "checkpoint_ns" not in df.columns:
        df["checkpoint_ns"] = 0
    if "checkpoint_us" not in df.columns:
        df["checkpoint_us"] = 0

    cache: dict[str, int] = {}
    for idx, row in df.iterrows():
        if float(row.get("checkpoint_ns", 0) or 0) > 0:
            continue
        run_id = str(row["run_id"])
        if run_id not in cache:
            cache[run_id] = _checkpoint_ns_from_source_log(
                artifacts_dir / run_id / "source.log"
            )
        checkpoint_ns = cache[run_id]
        if checkpoint_ns > 0:
            df.at[idx, "checkpoint_ns"] = checkpoint_ns
            df.at[idx, "checkpoint_us"] = _ceil_us(checkpoint_ns)


def load_migration_csv(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file)
    if df.empty:
        return df

    for column in _NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    if "notes" in df.columns:
        df["transfer_mode"] = df["notes"].apply(parse_transfer_mode)
    else:
        df["transfer_mode"] = "unknown"

    _backfill_checkpoint_precision(df, csv_file)

    if "checkpoint_us" in df.columns:
        precise_ms = df["checkpoint_us"] / 1000.0
        fallback_ms = df.get("checkpoint_ms", 0)
        df["checkpoint_plot_ms"] = precise_ms.where(precise_ms > 0, fallback_ms)
        df["checkpoint_plot_us"] = df["checkpoint_us"].where(
            df["checkpoint_us"] > 0, fallback_ms * 1000.0
        )
    elif "checkpoint_ms" in df.columns:
        df["checkpoint_plot_ms"] = df["checkpoint_ms"]
        df["checkpoint_plot_us"] = df["checkpoint_ms"] * 1000.0
    else:
        df["checkpoint_plot_ms"] = 0.0
        df["checkpoint_plot_us"] = 0.0

    if "archive_bytes" in df.columns:
        df["archive_kib"] = df["archive_bytes"] / 1024.0

    return df


def ordered_methods(values) -> list[str]:
    seen = {str(v) for v in values if str(v)}
    ordered = [m for m in _STANDARD_METHOD_ORDER if m in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def default_plots_dir() -> Path:
    script_dir = Path(__file__).resolve().parent  # WASM-migration/scripts/visualization
    return script_dir.parent.parent / "metrics" / "plots"


def resolve_output_file(output_file: Optional[str], default_name: str) -> Path:
    if output_file:
        return Path(output_file)
    return default_plots_dir() / default_name

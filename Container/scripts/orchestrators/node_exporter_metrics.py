from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


_METRIC_LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')
_SKIP_DISK_DEV_RE = re.compile(r"^(loop|ram|fd|sr)\d+$")


@dataclass(frozen=True)
class Snapshot:
    cpu: Dict[Tuple[str, str], float]  # (cpu, mode) -> seconds
    mem_total: Optional[float]
    mem_avail: Optional[float]
    disk_read: Dict[str, float]  # device -> bytes
    disk_write: Dict[str, float]  # device -> bytes


def _unescape_label_value(v: str) -> str:
    return v.replace(r"\n", "\n").replace(r"\t", "\t").replace(r"\\", "\\").replace(r"\"", '"')


def _parse_labels(raw: str) -> Dict[str, str]:
    if not raw or raw[0] != "{" or raw[-1] != "}":
        return {}
    inner = raw[1:-1]
    labels: Dict[str, str] = {}
    for m in _LABEL_RE.finditer(inner):
        labels[m.group(1)] = _unescape_label_value(m.group(2))
    return labels


def load_snapshot(path: Path) -> Snapshot:
    cpu: Dict[Tuple[str, str], float] = {}
    mem_total = None
    mem_avail = None
    disk_read: Dict[str, float] = {}
    disk_write: Dict[str, float] = {}

    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        m = _METRIC_LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        labels = _parse_labels(m.group(2) or "")
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        if math.isnan(value) or math.isinf(value):
            continue

        if name == "node_cpu_seconds_total":
            cpu_label = labels.get("cpu")
            mode = labels.get("mode")
            if cpu_label is None or mode is None:
                continue
            cpu[(cpu_label, mode)] = value
        elif name == "node_memory_MemTotal_bytes":
            mem_total = value
        elif name == "node_memory_MemAvailable_bytes":
            mem_avail = value
        elif name == "node_disk_read_bytes_total":
            dev = labels.get("device")
            if not dev or _SKIP_DISK_DEV_RE.match(dev):
                continue
            disk_read[dev] = value
        elif name == "node_disk_written_bytes_total":
            dev = labels.get("device")
            if not dev or _SKIP_DISK_DEV_RE.match(dev):
                continue
            disk_write[dev] = value

    return Snapshot(cpu=cpu, mem_total=mem_total, mem_avail=mem_avail, disk_read=disk_read, disk_write=disk_write)


def _load_host_epoch(meta_path: Path) -> Optional[float]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return float(meta.get("captured_at_host_epoch"))
    except Exception:
        return None


def _delta_sum(before: Dict, after: Dict) -> float:
    total = 0.0
    for k, v_after in after.items():
        v_before = before.get(k)
        if v_before is None:
            continue
        total += (v_after - v_before)
    return total


def cpu_util_pct(before: Snapshot, after: Snapshot) -> Optional[float]:
    if not before.cpu or not after.cpu:
        return None
    total = _delta_sum(before.cpu, after.cpu)
    if total <= 0:
        return None
    idle = 0.0
    iowait = 0.0
    for (cpu_label, mode), v_after in after.cpu.items():
        v_before = before.cpu.get((cpu_label, mode))
        if v_before is None:
            continue
        d = v_after - v_before
        if mode == "idle":
            idle += d
        elif mode == "iowait":
            iowait += d
    busy = total - idle - iowait
    return max(0.0, min(100.0, (busy / total) * 100.0))


def mem_used_pct(snapshot: Snapshot) -> Optional[float]:
    if snapshot.mem_total is None or snapshot.mem_avail is None or snapshot.mem_total <= 0:
        return None
    return (1.0 - (snapshot.mem_avail / snapshot.mem_total)) * 100.0


def disk_mb_s(before: Snapshot, after: Snapshot, dt_s: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if dt_s is None or dt_s <= 0:
        return None, None, None
    read_b = _delta_sum(before.disk_read, after.disk_read)
    write_b = _delta_sum(before.disk_write, after.disk_write)
    read_mb_s = read_b / dt_s / (1024 * 1024)
    write_mb_s = write_b / dt_s / (1024 * 1024)
    return read_mb_s, write_mb_s, read_mb_s + write_mb_s


NODE_EXPORTER_CSV_HEADER = [
    "run_id",
    "migration_method",
    "transfer_mode",
    "source_node",
    "dest_node",
    "src_dt_s",
    "dst_dt_s",
    "src_cpu_util_pct",
    "dst_cpu_util_pct",
    "cpu_util_pct_avg",
    "src_mem_used_pct_before",
    "src_mem_used_pct_after",
    "src_mem_used_pct_delta",
    "dst_mem_used_pct_before",
    "dst_mem_used_pct_after",
    "dst_mem_used_pct_delta",
    "mem_used_pct_after_avg",
    "mem_used_pct_delta_avg",
    "src_disk_read_mb_s",
    "src_disk_write_mb_s",
    "src_disk_total_mb_s",
    "dst_disk_read_mb_s",
    "dst_disk_write_mb_s",
    "dst_disk_total_mb_s",
    "disk_total_mb_s_avg",
    "timestamp",
]


def _ensure_csv_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(",".join(NODE_EXPORTER_CSV_HEADER) + "\n", encoding="utf-8")
        return
    first = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
    if not first:
        path.write_text(",".join(NODE_EXPORTER_CSV_HEADER) + "\n", encoding="utf-8")
        return
    if first[0].strip() != ",".join(NODE_EXPORTER_CSV_HEADER):
        raise RuntimeError(
            f"node_exporter CSV schema mismatch in {path}. "
            f"Expected header: {','.join(NODE_EXPORTER_CSV_HEADER)}"
        )


def append_node_exporter_row(
    csv_path: Path,
    *,
    run_id: str,
    migration_method: str,
    transfer_mode: str,
    source_node: str,
    dest_node: str,
    src_before_prom: Path,
    src_after_prom: Path,
    src_before_meta: Path,
    src_after_meta: Path,
    dst_before_prom: Path,
    dst_after_prom: Path,
    dst_before_meta: Path,
    dst_after_meta: Path,
) -> bool:
    if not (src_before_prom.exists() and src_after_prom.exists() and dst_before_prom.exists() and dst_after_prom.exists()):
        return False

    src_before = load_snapshot(src_before_prom)
    src_after = load_snapshot(src_after_prom)
    dst_before = load_snapshot(dst_before_prom)
    dst_after = load_snapshot(dst_after_prom)

    src_t0 = _load_host_epoch(src_before_meta) if src_before_meta.exists() else src_before_prom.stat().st_mtime
    src_t1 = _load_host_epoch(src_after_meta) if src_after_meta.exists() else src_after_prom.stat().st_mtime
    dst_t0 = _load_host_epoch(dst_before_meta) if dst_before_meta.exists() else dst_before_prom.stat().st_mtime
    dst_t1 = _load_host_epoch(dst_after_meta) if dst_after_meta.exists() else dst_after_prom.stat().st_mtime

    src_dt = float(src_t1 - src_t0) if (src_t0 is not None and src_t1 is not None) else None
    dst_dt = float(dst_t1 - dst_t0) if (dst_t0 is not None and dst_t1 is not None) else None

    src_cpu = cpu_util_pct(src_before, src_after)
    dst_cpu = cpu_util_pct(dst_before, dst_after)
    cpu_avg = (src_cpu + dst_cpu) / 2.0 if (src_cpu is not None and dst_cpu is not None) else None

    src_mem_b = mem_used_pct(src_before)
    src_mem_a = mem_used_pct(src_after)
    dst_mem_b = mem_used_pct(dst_before)
    dst_mem_a = mem_used_pct(dst_after)
    src_mem_d = (src_mem_a - src_mem_b) if (src_mem_a is not None and src_mem_b is not None) else None
    dst_mem_d = (dst_mem_a - dst_mem_b) if (dst_mem_a is not None and dst_mem_b is not None) else None

    mem_after_avg = (src_mem_a + dst_mem_a) / 2.0 if (src_mem_a is not None and dst_mem_a is not None) else None
    mem_delta_avg = (src_mem_d + dst_mem_d) / 2.0 if (src_mem_d is not None and dst_mem_d is not None) else None

    src_r, src_w, src_t = disk_mb_s(src_before, src_after, src_dt)
    dst_r, dst_w, dst_t = disk_mb_s(dst_before, dst_after, dst_dt)
    disk_avg = (src_t + dst_t) / 2.0 if (src_t is not None and dst_t is not None) else None

    def f(v: Optional[float]) -> str:
        if v is None:
            return ""
        return f"{v:.6f}"

    _ensure_csv_schema(csv_path)
    with csv_path.open("a", newline="", encoding="utf-8") as fcsv:
        w = csv.DictWriter(fcsv, fieldnames=NODE_EXPORTER_CSV_HEADER)
        w.writerow(
            {
                "run_id": run_id,
                "migration_method": migration_method,
                "transfer_mode": transfer_mode,
                "source_node": source_node,
                "dest_node": dest_node,
                "src_dt_s": f(src_dt),
                "dst_dt_s": f(dst_dt),
                "src_cpu_util_pct": f(src_cpu),
                "dst_cpu_util_pct": f(dst_cpu),
                "cpu_util_pct_avg": f(cpu_avg),
                "src_mem_used_pct_before": f(src_mem_b),
                "src_mem_used_pct_after": f(src_mem_a),
                "src_mem_used_pct_delta": f(src_mem_d),
                "dst_mem_used_pct_before": f(dst_mem_b),
                "dst_mem_used_pct_after": f(dst_mem_a),
                "dst_mem_used_pct_delta": f(dst_mem_d),
                "mem_used_pct_after_avg": f(mem_after_avg),
                "mem_used_pct_delta_avg": f(mem_delta_avg),
                "src_disk_read_mb_s": f(src_r),
                "src_disk_write_mb_s": f(src_w),
                "src_disk_total_mb_s": f(src_t),
                "dst_disk_read_mb_s": f(dst_r),
                "dst_disk_write_mb_s": f(dst_w),
                "dst_disk_total_mb_s": f(dst_t),
                "disk_total_mb_s_avg": f(disk_avg),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )

    return True


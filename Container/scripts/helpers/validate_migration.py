#!/usr/bin/env python3
"""
Checkpoint Validation Tool

Validates CRIU checkpoint archives by examining image files and contents.
Works with both checkpointctl (if available) and direct file inspection.

This tool:
1. Analyzes CRIU checkpoint directories to verify all required image files
2. Checks for critical components (core dumps, memory pages, file descriptors)
3. Compares source and target checkpoints to verify migration completeness
4. Works with any CRIU checkpoint, regardless of checkpointctl version
5. Provides detailed JSON report of validation results

Usage:
  python3 validate_migration.py <source_checkpoint>
  python3 validate_migration.py <source_checkpoint> <target_checkpoint>

Example:
  # Validate a single checkpoint
  python3 validate_migration.py /tmp/CRIU-counter

  # Compare source and restored checkpoint
  python3 validate_migration.py /tmp/CRIU-counter-src /tmp/CRIU-counter-dst
"""

import subprocess
import json
import sys
from pathlib import Path
from datetime import datetime


def resolve_checkpointctl_path() -> str:
    """Find checkpointctl binary in common locations."""
    candidates = [
        "checkpointctl",
        "./tools/checkpointctl/checkpointctl",
        "../tools/checkpointctl/checkpointctl",
    ]
    for candidate in candidates:
        result = subprocess.run(
            ["bash", "-lc", f"command -v {candidate}"], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return ""


def analyze_checkpoint_files(checkpoint_path: str) -> dict:
    """
    Analyze checkpoint directory structure and files.

    This is a fallback method that doesn't require checkpointctl's --json flag.
    It inspects the CRIU image files directly.

    Args:
        checkpoint_path: Path to CRIU checkpoint directory

    Returns:
        Dictionary with checkpoint metadata
    """
    path = Path(checkpoint_path)

    if not path.exists():
        return None

    if not path.is_dir():
        return None

    metadata = {
        "path": str(checkpoint_path),
        "images": [],
        "image_count": 0,
        "total_size": 0,
        "has_core_dump": False,
        "has_memory": False,
        "has_mm": False,
        "has_files": False,
    }

    # Scan for CRIU image files
    for img_file in path.glob("*.img"):
        size = img_file.stat().st_size
        metadata["images"].append({"name": img_file.name, "size": size})
        metadata["total_size"] += size

        # Check for key components
        if "core" in img_file.name:
            metadata["has_core_dump"] = True
        if "pages" in img_file.name:
            metadata["has_memory"] = True
        if "mm" in img_file.name:
            metadata["has_mm"] = True
        if "files" in img_file.name:
            metadata["has_files"] = True

    metadata["image_count"] = len(metadata["images"])

    # Check for pstree (process tree)
    if path.joinpath("pstree.img").exists():
        metadata["has_pstree"] = True

    return metadata


def run_checkpointctl_show(checkpoint_path: str) -> dict:
    """
    Try to run checkpointctl show (text output version).

    This version doesn't use --json flag, works with older checkpointctl versions.

    Args:
        checkpoint_path: Path to CRIU checkpoint directory

    Returns:
        Dictionary with checkpoint metadata or None
    """
    try:
        checkpointctl = resolve_checkpointctl_path()
        if not checkpointctl:
            return None

        result = subprocess.run(
            [checkpointctl, "show", checkpoint_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            # Parse text output
            metadata = {"path": checkpoint_path, "raw_output": result.stdout}
            return metadata
        else:
            return None
    except Exception:
        return None


def validate_migration(source_checkpoint: str, target_checkpoint: str = None) -> dict:
    """
    Validate a CRIU migration by analyzing checkpoints.

    Uses direct file inspection as primary method, falls back to checkpointctl if available.

    Args:
        source_checkpoint: Path to dump on source node
        target_checkpoint: Path to dump on target node (optional)

    Returns:
        Validation report
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "source_checkpoint": source_checkpoint,
        "target_checkpoint": target_checkpoint,
        "validation_results": {},
    }

    # Analyze source checkpoint
    print(f"Analyzing source checkpoint: {source_checkpoint}")
    source_meta = analyze_checkpoint_files(source_checkpoint)

    if not source_meta:
        # Try checkpointctl as fallback
        source_meta = run_checkpointctl_show(source_checkpoint)

    if source_meta:
        report["validation_results"]["source"] = {
            "accessible": True,
            "metadata": source_meta,
        }

        # Check for critical components
        components = source_meta.get("images", [])
        core_dump = source_meta.get("has_core_dump", False)
        memory = source_meta.get("has_memory", False)
        files = source_meta.get("has_files", False)

        print("  ✓ Source checkpoint accessible")
        print(f"  - Image files: {source_meta.get('image_count', 0)}")
        print(
            f"  - Total size: {source_meta.get('total_size', 0)} bytes ({source_meta.get('total_size', 0) / 1024:.1f} KB)"
        )
        print(f"  - Has core dump: {core_dump}")
        print(f"  - Has memory: {memory}")
        print(f"  - Has files info: {files}")

        # Overall assessment
        has_required = core_dump and memory
        if has_required:
            print("  ✓ Has required components (core + memory)")
        else:
            print("  ⚠ Missing required components")
    else:
        report["validation_results"]["source"] = {
            "accessible": False,
            "error": "Failed to read checkpoint",
        }
        print("  ✗ Source checkpoint not accessible")

    # Analyze target checkpoint if provided
    if target_checkpoint and Path(target_checkpoint).exists():
        print(f"\nAnalyzing target checkpoint: {target_checkpoint}")
        target_meta = analyze_checkpoint_files(target_checkpoint)

        if not target_meta:
            # Try checkpointctl as fallback
            target_meta = run_checkpointctl_show(target_checkpoint)

        if target_meta:
            report["validation_results"]["target"] = {
                "accessible": True,
                "metadata": target_meta,
            }

            target_components = target_meta.get("images", [])
            print("  ✓ Target checkpoint accessible")
            print(f"  - Image files: {target_meta.get('image_count', 0)}")
            print(
                f"  - Total size: {target_meta.get('total_size', 0)} bytes ({target_meta.get('total_size', 0) / 1024:.1f} KB)"
            )

            # Compare
            if source_meta and target_meta:
                source_count = source_meta.get("image_count", 0)
                target_count = target_meta.get("image_count", 0)

                if source_count == target_count:
                    report["validation_results"]["comparison"] = {
                        "image_count_match": True,
                        "status": "PASS",
                    }
                    print(f"  ✓ Image count matches ({source_count})")
                else:
                    report["validation_results"]["comparison"] = {
                        "image_count_match": False,
                        "source_count": source_count,
                        "target_count": target_count,
                        "status": "FAIL",
                    }
                    print(
                        f"  ✗ Image count differs: source={source_count}, target={target_count}"
                    )
        else:
            report["validation_results"]["target"] = {
                "accessible": False,
                "error": "Failed to read checkpoint",
            }
            print("  ✗ Target checkpoint not accessible")

    return report


def main():
    """
    Example usage:
    python3 validate_migration.py /path/to/dump /path/to/dump.target
    """

    if len(sys.argv) < 2:
        print(
            "Usage: python3 validate_migration.py <source_checkpoint> [target_checkpoint]"
        )
        print("")
        print("Example:")
        print("  python3 validate_migration.py /tmp/criu-dump-src /tmp/criu-dump-dst")
        sys.exit(1)

    source = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None

    report = validate_migration(source, target)

    # Print report
    print("\n" + "=" * 50)
    print("VALIDATION REPORT")
    print("=" * 50)
    print(json.dumps(report, indent=2))

    return (
        0
        if report["validation_results"].get("comparison", {}).get("status") == "PASS"
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())

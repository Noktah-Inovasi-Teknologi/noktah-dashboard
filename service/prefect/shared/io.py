"""
Filesystem / JSON output helpers shared across workflows.
"""
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def run_output_dir(base_dir: str, timestamp: str) -> str:
    """
    Build (and create) a timestamped run output directory under ``base_dir``.

    Args:
        base_dir: Base data directory (e.g. ``.../data``).
        timestamp: Run timestamp, typically ``%Y%m%d_%H%M%S``.

    Returns:
        Absolute path to the created ``base_dir/timestamp`` directory.
    """
    path = os.path.join(base_dir, timestamp)
    os.makedirs(path, exist_ok=True)
    return path


def save_json(data: Dict[str, Any], output_path: str) -> str:
    """
    Write ``data`` to ``output_path`` as UTF-8 JSON (2-space indent, non-ASCII kept).

    Ensures the parent directory exists first.

    Args:
        data: Serializable data to write.
        output_path: Destination file path.

    Returns:
        The path written to.
    """
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(f"Data saved to JSON file: {output_path}")
    return output_path


def load_json(input_path: str) -> Optional[Dict[str, Any]]:
    """
    Load JSON from ``input_path``, or return None if the file does not exist.

    Args:
        input_path: Path to a JSON file.

    Returns:
        Parsed data, or None if the file is missing.
    """
    if not os.path.exists(input_path):
        return None
    with open(input_path, "r", encoding="utf-8") as f:
        return json.load(f)

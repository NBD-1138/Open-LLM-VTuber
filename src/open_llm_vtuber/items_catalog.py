"""
Items catalog builder for Live2D items.

Scans the live2d items directory and builds a catalog of:
- Live2D models (directories containing *.model3.json)
- Standalone image items (PNG/JPG/etc.) that are NOT under a model directory

Rules:
  - Walk base_dir recursively.
  - If a directory contains a Live2D model file (e.g. *.model3.json),
    create a 'live2d' item for that model and DO NOT include any PNGs
    from that directory or its subdirectories (to avoid including
    texture/skin PNGs).
  - Directories that do NOT contain a model file:
    include standalone PNG (or other image) files as 'image' items.

The result is written to `catalog.json` in base_dir
and returned as a Python list.
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any

from loguru import logger

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Treat any file that ends with model.json or model3.json as a model definition.
def _is_model_file(name: str) -> bool:
    lower = name.lower()
    return lower.endswith("model.json") or lower.endswith("model3.json")


def build_items_catalog(base_dir: str, url_prefix: str = "/live2d-models/items") -> List[Dict[str, Any]]:
  """
  Build a catalog of Live2D items and write it to catalog.json.

  Args:
      base_dir: Directory containing Live2D items (usually live2d-models/items).
      url_prefix: URL prefix under which these files are served
                  (e.g. /live2d-models/items).

  Returns:
      A list of item dictionaries, each describing a model or image.
  """
  base_path = Path(base_dir).expanduser()
  # Allow callers to pass either the items directory or an explicit catalog.json path.
  if base_path.suffix.lower() == ".json":
      out_path = base_path.resolve()
      base_path = out_path.parent
  else:
      base_path = base_path.resolve()
      out_path = base_path / "catalog.json"

  if not base_path.exists():
      logger.warning(f"[ItemsCatalog] Base directory does not exist: {base_path}")
      return []

  if not base_path.is_dir():
      logger.warning(f"[ItemsCatalog] Base path is not a directory: {base_path}")
      return []

  items: List[Dict[str, Any]] = []

  # Walk top-down so we can stop descending into model folders
  for root, dirs, files in os.walk(base_path):
      root_path = Path(root)

      # Detect model files in this directory
      model_files = [
          f for f in files
          if _is_model_file(f)
      ]

      # If this directory contains a model, register models and do not
      # descend further (skip textures / skins under this folder).
      if model_files:
          for filename in model_files:
              model_path = root_path / filename
              rel = model_path.relative_to(base_path)
              url = f"{url_prefix}/{rel.as_posix()}"

              item_id = rel.stem  # filename without extension
              items.append(
                  {
                      "id": f"model:{item_id}",
                      "type": "live2d",
                      "name": item_id,
                      "model_path": url,
                      "relative_path": rel.as_posix(),
                  }
              )

          # Do not traverse subdirectories of a model root, so we don't
          # accidentally include texture PNGs.
          dirs[:] = []
          continue

      # No model in this directory: include image files as standalone items
      for filename in files:
          ext = Path(filename).suffix.lower()
          if ext not in IMAGE_EXTS:
              continue

          file_path = root_path / filename
          rel = file_path.relative_to(base_path)
          url = f"{url_prefix}/{rel.as_posix()}"

          item_id = rel.stem
          items.append(
              {
                  "id": f"image:{item_id}",
                  "type": "image",
                  "name": item_id,
                  "image_path": url,
                  "relative_path": rel.as_posix(),
              }
          )

  # Build catalog structure
  catalog_data = {
      "base_url": url_prefix,
      "count": len(items),
      "items": items,
  }

  # Write catalog file into base_dir
  try:
      out_path.write_text(
          json.dumps(catalog_data, indent=2, ensure_ascii=False),
          encoding="utf-8",
      )
      logger.info(
          f"[ItemsCatalog] Wrote {len(items)} items to {out_path}"
      )
  except OSError as exc:
      logger.warning(
          f"[ItemsCatalog] Failed to write {out_path}: {exc}"
      )

  logger.info(f"[ItemsCatalog] Final item count: {len(items)}")
  return items

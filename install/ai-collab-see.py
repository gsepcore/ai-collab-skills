#!/usr/bin/env python3
"""Direct, deterministic computer vision for agents without native image input."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path


def load_observer():
    path = Path(__file__).resolve().with_name("ai-collab-observer.py")
    spec = importlib.util.spec_from_file_location("ai_collab_observer_for_vision", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load observer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("visual evidence is not a valid PNG")
    return struct.unpack(">II", header[16:24])


def parse_agents(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def inspect(image: Path, root: Path, required: list[str]) -> dict:
    observer = load_observer()
    image = image.expanduser().resolve()
    root = root.expanduser().resolve()
    if not image.is_file():
        return {"status": "failed", "reason": f"image not found: {image}"}
    try:
        width, height = png_size(image)
    except (OSError, ValueError) as exc:
        return {"status": "failed", "reason": str(exc)}
    binary = observer.tesseract_bin()
    if not binary:
        return {"status": "failed", "reason": "tesseract is required for direct pixel inspection"}
    prepared, temporary = observer.prepare_ocr_image(image, root, subprocess.run)
    try:
        try:
            completed = observer.run_command(
                [binary, str(prepared), "stdout", "--psm", "6", "tsv"],
                root,
                runner=subprocess.run,
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "reason": f"direct pixel OCR failed: {exc}"}
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    if completed.returncode != 0:
        return {
            "status": "failed",
            "reason": observer.truncate(completed.stderr or completed.stdout or "direct pixel OCR failed", 500),
        }
    text, words, ocr_width, ocr_height = observer.parse_tesseract_tsv(completed.stdout)
    hits = observer.visual_agent_hits(words, text, ocr_width, ocr_height)
    missing = [agent for agent in required if agent not in hits]
    normalized_text = "".join(character for character in text.lower() if character.isalnum())
    normalized_project = "".join(character for character in root.name.lower() if character.isalnum())
    project_match = bool(normalized_project and normalized_project in normalized_text)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    return {
        "schema": "ai-collab.direct-vision.v1",
        "status": "verified" if project_match and not missing else "failed",
        "method": "direct-pixel-ocr",
        "image": str(image),
        "sha256": digest,
        "image_size": {"width": width, "height": height},
        "project": root.name,
        "project_path": str(root),
        "project_match": project_match,
        "required_agents": required,
        "visible_agents": sorted(hits),
        "missing_agents": missing,
        "visual_hits": hits,
        "text_excerpt": observer.truncate(text, 1200),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the actual screenshot pixels for AI Collab visual evidence.")
    parser.add_argument("--image", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--agents", required=True, help="Comma-separated required agent slugs")
    args = parser.parse_args(argv)
    result = inspect(Path(args.image), Path(args.root), parse_agents(args.agents))
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "verified" else 4


if __name__ == "__main__":
    raise SystemExit(main())

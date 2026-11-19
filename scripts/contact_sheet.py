"""Build numbered contact sheets so many images can be reviewed in one view.

Each cell keeps the source item's ``idx`` as an ASCII label (the default
Pillow font cannot render Chinese), which is the key used to map a visual
observation back to a record.

The manifest's ``idx`` is the authoritative identifier. It may contain gaps
when a download failed, so selection is by ``idx`` value by default. The old
``--mode position`` behaviour is kept only to reproduce historical sheets.

Usage::

    python scripts/contact_sheet.py --manifest covers/manifest.json \
        --outdir .scratch/sheets --start 0 --count 25
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def select_items(items: list[dict], start: int, count: int,
                 mode: str = "idx") -> list[dict]:
    """Pick up to ``count`` items starting at ``start``.

    ``idx`` mode selects by the item's label (with gaps allowed); ``position``
    mode selects by array offset. Results are always sorted by ``idx``.
    """
    if count < 1:
        raise ValueError("count must be positive")
    if mode not in ("idx", "position"):
        raise ValueError("mode must be 'idx' or 'position'")
    if mode == "position":
        subset = items[start:start + count]
    else:
        stop = start + count
        subset = [
            item for item in items
            if start <= int(item["idx"]) < stop
        ]
    return sorted(subset, key=lambda item: int(item["idx"]))


def build_contact_sheet(items: list[dict], outdir: Path, *,
                        start: int = 0, count: int = 25, cols: int = 5,
                        cell: int = 360, label_height: int = 34,
                        pad: int = 6, tag: str = "",
                        mode: str = "idx") -> tuple[Path, int]:
    """Write one contact sheet and return ``(path, number_of_cells)``."""
    from PIL import Image, ImageDraw  # imported lazily: pure logic stays testable

    subset = select_items(items, start, count, mode)
    if not subset:
        raise ValueError("no items in the requested range")
    if cols < 1 or cell < 1:
        raise ValueError("cols and cell must be positive")

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = (len(subset) + cols - 1) // cols
    cell_height = cell + label_height
    sheet = Image.new(
        "RGB",
        (cols * (cell + pad) + pad, rows * (cell_height + pad) + pad),
        (24, 24, 28),
    )
    draw = ImageDraw.Draw(sheet)
    placed = 0

    for index, item in enumerate(subset):
        image_path = Path(item["raw"])
        if not image_path.exists():
            continue
        try:
            image = Image.open(image_path).convert("RGB")
        except OSError:
            continue
        width, height = image.size
        scale = cell / max(width, height)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )
        x0 = pad + (index % cols) * (cell + pad)
        y0 = pad + (index // cols) * (cell_height + pad)
        offset_x = x0 + (cell - image.width) // 2
        offset_y = y0 + label_height + (cell - image.height) // 2
        sheet.paste(image, (offset_x, offset_y))
        draw.rectangle(
            [x0, y0, x0 + cell, y0 + label_height - 2],
            fill=(200, 40, 90),
        )
        draw.text((x0 + 6, y0 + 9), f"#{item['idx']}", fill=(255, 255, 255))
        placed += 1

    first_idx = int(subset[0]["idx"])
    last_idx = int(subset[-1]["idx"])
    output = outdir / f"sheet_{tag}{first_idx:04d}_{last_idx:04d}.png"
    sheet.save(output)
    return output, placed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--cols", type=int, default=5)
    parser.add_argument("--cell", type=int, default=360)
    parser.add_argument("--tag", default="")
    parser.add_argument("--mode", choices=("idx", "position"), default="idx")
    args = parser.parse_args(argv)

    items = json.loads(args.manifest.read_text(encoding="utf-8"))
    output, placed = build_contact_sheet(
        items, args.outdir, start=args.start, count=args.count,
        cols=args.cols, cell=args.cell, tag=args.tag, mode=args.mode)
    print(json.dumps({"output": str(output), "cells": placed},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

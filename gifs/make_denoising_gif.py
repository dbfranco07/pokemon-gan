#!/usr/bin/env python3
"""Generate a denoising progression GIF from the latest diffusion checkpoint.

The script:
  - loads the newest ckpt_*.pth from checkpoints/diffusion
  - picks four reproducible random seeds (p1..p4) unless they are provided
  - renders all four sprites at each DDIM step count
  - each GIF frame is a 2x2 grid of the four sprites at the same step count,
    so the animation shows every sprite sharpen together as steps increase
  - keeps the raw model output on a flat black background (same look as
    samples/diffusion.gif); the alpha key used by the web app is NOT applied
  - prints the wall-clock time spent at each step count (more steps -> slower)
  - saves the individual PNGs, a 2x2 preview grid, and an animated GIF

Output is written under gifs/<timestamp>_<seeds>/.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import inference


DEFAULT_STEPS = list(range(50, 1001, 50))
NUM_SPRITES = 25

# The animation is drawn on black to match samples/diffusion.gif.
BG_COLOR = (0, 0, 0, 255)
TEXT_COLOR = (235, 235, 235, 255)
BORDER_COLOR = (70, 70, 70, 255)


def flatten_on_black(img: Image.Image) -> Image.Image:
    """Composite the keyed RGBA sprite back onto opaque black.

    `inference.generate` keys the model's flat-black background to alpha. Pasting
    it on black recovers the original sprite-on-black with nothing removed.
    """
    bg = Image.new("RGBA", img.size, BG_COLOR)
    return Image.alpha_composite(bg, img.convert("RGBA"))


def parse_steps(raw_steps: str | None) -> list[int]:
    if not raw_steps:
        return DEFAULT_STEPS
    steps = []
    for item in raw_steps.split(","):
        value = int(item.strip())
        if value < 1 or value > 1000:
            raise ValueError("steps must be between 1 and 1000")
        steps.append(value)
    if not steps:
        raise ValueError("at least one step value is required")
    return steps


def parse_seeds(raw_seeds: str | None) -> list[int]:
    """Return exactly NUM_SPRITES seeds; random ones fill any that are missing."""
    if not raw_seeds:
        return [random.randint(0, 2**10 - 1) for _ in range(NUM_SPRITES)]
    seeds = [int(item.strip()) for item in raw_seeds.split(",") if item.strip()]
    if len(seeds) != NUM_SPRITES:
        raise ValueError(f"--seeds needs exactly {NUM_SPRITES} comma-separated values")
    return seeds


def build_grid(images: list[Image.Image], step: int, scale: int = 3, cols: int = 5) -> Image.Image:
    """Compose the four sprites into a 2x2 grid on black, titled with the step count.

    Sprites are upscaled by an integer factor with NEAREST resampling so the
    pixels stay crisp/blocky, matching the web app's `image-rendering: pixelated`.
    """
    if not images:
        raise ValueError("no images to compose")

    native = max(max(img.width, img.height) for img in images)
    thumb_size = native * scale
    title_height = 30
    label_height = 24
    pad = 16
    rows = (len(images) + cols - 1) // cols
    sheet_w = cols * thumb_size + (cols + 1) * pad
    sheet_h = title_height + rows * (thumb_size + label_height) + (rows + 1) * pad
    sheet = Image.new("RGBA", (sheet_w, sheet_h), BG_COLOR)
    draw = ImageDraw.Draw(sheet)

    draw.text((pad, 8), f"DDIM steps = {step}", fill=TEXT_COLOR)

    for index, img in enumerate(images):
        row = index // cols
        col = index % cols
        x = pad + col * (thumb_size + pad)
        y = title_height + pad + row * (thumb_size + label_height + pad)

        rgba = img.convert("RGBA")
        # Integer nearest-neighbor upscale -> crisp pixels (no LANCZOS blur).
        fitted = rgba.resize((rgba.width * scale, rgba.height * scale), Image.NEAREST)
        paste_x = x + (thumb_size - fitted.width) // 2
        paste_y = y + (thumb_size - fitted.height) // 2
        sheet.alpha_composite(fitted, (paste_x, paste_y))
        draw.rectangle([x, y, x + thumb_size - 1, y + thumb_size - 1], outline=BORDER_COLOR, width=2)
        draw.text((x, y + thumb_size + 5), f"p{index + 1}", fill=TEXT_COLOR)

    return sheet


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a denoising GIF from the latest checkpoint")
    parser.add_argument(
        "--seeds",
        help=f"comma-separated seeds for the {NUM_SPRITES} sprites (default: {NUM_SPRITES} random seeds)",
    )
    parser.add_argument(
        "--steps",
        help="comma-separated DDIM step counts (default: 50,100,...,1000)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "gifs",
        help="root directory for generated GIF assets",
    )
    parser.add_argument(
        "--gif-duration",
        type=int,
        default=250,
        help="milliseconds per frame in the animated GIF",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=3,
        help="integer nearest-neighbor upscale for crisp pixels in the grid",
    )
    args = parser.parse_args()

    seeds = parse_seeds(args.seeds)
    steps = parse_steps(args.steps)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    seed_tag = "_".join(str(s) for s in seeds)
    out_dir = args.output_root / f"{stamp}_seeds{seed_tag}"
    out_dir.mkdir(parents=True, exist_ok=False)

    model_state = inference.load_model()
    print(f"checkpoint={Path(model_state.checkpoint).name} device={model_state.device}")
    print(f"seeds={seeds}")

    grids: list[tuple[int, Image.Image]] = []
    total_start = time.perf_counter()

    for step in steps:
        step_start = time.perf_counter()
        sprites = [flatten_on_black(inference.generate(seed, step)) for seed in seeds]
        elapsed = time.perf_counter() - step_start

        for sprite_idx, img in enumerate(sprites):
            frame_path = out_dir / f"p{sprite_idx + 1}_step{step:04d}.png"
            img.save(frame_path)

        grid = build_grid(sprites, step, scale=args.scale)
        grids.append((step, grid))
        print(
            f"steps={step:4d}  time={elapsed:7.2f}s  "
            f"({elapsed / NUM_SPRITES:.2f}s/sprite)"
        )

    total_elapsed = time.perf_counter() - total_start
    print(f"total generation time={total_elapsed:.2f}s over {len(steps)} step counts")

    # Preview = the final (highest-step) grid, i.e. the sharpest 2x2.
    preview = grids[-1][1]
    preview_path = out_dir / "preview_2x2.png"
    preview.save(preview_path)

    gif_frames = [grid.convert("P", palette=Image.ADAPTIVE) for _step, grid in grids]
    gif_path = out_dir / "denoising.gif"
    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=args.gif_duration,
        loop=0,
        optimize=True,
        disposal=2,
    )

    meta_path = out_dir / "run.txt"
    meta_path.write_text(
        "\n".join(
            [
                f"seeds={','.join(str(s) for s in seeds)}",
                f"checkpoint={Path(model_state.checkpoint).name}",
                f"device={model_state.device}",
                f"steps={','.join(str(step) for step in steps)}",
                f"total_seconds={total_elapsed:.2f}",
                f"gif={gif_path.name}",
                f"preview={preview_path.name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"wrote {gif_path}")
    print(f"wrote {preview_path}")
    print(f"seeds={','.join(str(s) for s in seeds)}")
    print(f"checkpoint={Path(model_state.checkpoint).name}")


if __name__ == "__main__":
    main()

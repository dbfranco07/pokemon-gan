#!/usr/bin/env python3
"""Turn a folder of images into an animated GIF.

Examples:
    python make_gif.py samples/diffusion
    python make_gif.py samples/diffusion -o progress.gif --fps 5
    python make_gif.py samples/diffusion --pattern "sample_epoch*.png" --width 256 --loop 0
"""
import argparse
import re
import sys
from pathlib import Path

from PIL import Image

IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff"}


def natural_key(path: Path):
    """Sort so epoch10 < epoch100, not lexicographically."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", path.name)]


def main():
    ap = argparse.ArgumentParser(description="Make a GIF from a folder of images.")
    ap.add_argument("folder", type=Path, help="folder containing the images")
    ap.add_argument("-o", "--output", type=Path, help="output .gif (default: <folder>.gif)")
    ap.add_argument("--pattern", default="*", help="glob to select files (default: all images)")
    ap.add_argument("--fps", type=float, help="frames per second (overrides --duration)")
    ap.add_argument("--duration", type=float, default=200,
                    help="ms per frame (default: 200)")
    ap.add_argument("--width", type=int, help="resize frames to this width (keeps aspect)")
    ap.add_argument("--loop", type=int, default=0, help="loop count, 0 = forever (default: 0)")
    ap.add_argument("--reverse", action="store_true", help="reverse frame order")
    ap.add_argument("--bounce", action="store_true", help="play forward then backward")
    args = ap.parse_args()

    if not args.folder.is_dir():
        sys.exit(f"error: {args.folder} is not a directory")

    files = sorted(
        (p for p in args.folder.glob(args.pattern)
         if p.is_file() and p.suffix.lower() in IMG_EXTS),
        key=natural_key,
    )
    if not files:
        sys.exit(f"error: no images matching '{args.pattern}' in {args.folder}")

    if args.reverse:
        files.reverse()

    frames = []
    size = None
    for p in files:
        im = Image.open(p).convert("RGBA")
        if args.width:
            h = round(im.height * args.width / im.width)
            im = im.resize((args.width, h), Image.LANCZOS)
        if size is None:
            size = im.size
        elif im.size != size:               # GIF needs uniform frame size
            im = im.resize(size, Image.LANCZOS)
        # flatten onto white so transparency doesn't go black
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        frames.append(Image.alpha_composite(bg, im).convert("P", palette=Image.ADAPTIVE))

    if args.bounce and len(frames) > 2:
        frames += frames[-2:0:-1]

    duration = 1000 / args.fps if args.fps else args.duration
    out = args.output or args.folder.with_suffix(".gif")
    frames[0].save(
        out, save_all=True, append_images=frames[1:],
        duration=duration, loop=args.loop, optimize=True, disposal=2,
    )
    print(f"wrote {out}  ({len(frames)} frames, {duration:.0f} ms/frame, {size[0]}x{size[1]})")


if __name__ == "__main__":
    main()

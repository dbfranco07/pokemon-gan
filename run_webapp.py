#!/usr/bin/env python3
"""Launch the sprite generator web app.

    python run_webapp.py                 # http://127.0.0.1:8000
    python run_webapp.py --host 0.0.0.0 --port 9000
    SPRITE_CKPT_DIR=archive/checkpoints/diffusion python run_webapp.py

Uses the GPU when available (CUDA -> MPS -> CPU), portable across Win/Mac/Linux.
"""
import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the sprite generator web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="auto-reload (dev)")
    args = ap.parse_args()

    uvicorn.run("webapp.app:app", host=args.host, port=args.port,
                reload=args.reload)


if __name__ == "__main__":
    main()

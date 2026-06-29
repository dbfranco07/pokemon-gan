"""FastAPI backend for the sprite generator web app.

Endpoints:
  GET  /              -> the single-page viewer (static/index.html)
  GET  /api/info      -> {device, checkpoint, epoch, image_size}
  POST /api/generate  -> {seed, steps, image: dataURL}
  POST /api/save      -> {filename, url}
  GET  /api/saved     -> [{filename, url, seed, steps}, ...] (newest first)
  /static/*, /saved/* -> static assets / persisted sprites
"""
from __future__ import annotations

import base64
import io
import os
import random
import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import inference

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
SAVED_DIR = os.path.join(HERE, "saved")
os.makedirs(SAVED_DIR, exist_ok=True)

MAX_SEED = 2 ** 31 - 1
SAVED_RE = re.compile(r"sprite_seed(\d+)_steps(\d+)_")

app = FastAPI(title="Sprite Generator")


@app.on_event("startup")
def _startup() -> None:
    # Load the model once so the first request isn't slow / racy.
    inference.load_model()


class GenerateRequest(BaseModel):
    seed: Optional[int] = None
    steps: int = 50


class SaveRequest(BaseModel):
    image: str  # data:image/png;base64,....
    seed: int
    steps: int


@app.get("/api/info")
def info() -> dict:
    st = inference.load_model()
    return {
        "device": str(st.device),
        "checkpoint": os.path.basename(st.checkpoint),
        "epoch": st.epoch,
        "image_size": st.image_size,
    }


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict:
    seed = req.seed if req.seed is not None else random.randint(0, MAX_SEED)
    steps = max(1, min(int(req.steps), 1000))
    img = inference.generate(seed, steps)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"seed": seed, "steps": steps,
            "image": f"data:image/png;base64,{b64}"}


@app.post("/api/save")
def save(req: SaveRequest) -> dict:
    header, _, payload = req.image.partition(",")
    if "base64" not in header or not payload:
        raise HTTPException(400, "image must be a base64 PNG data URL")
    try:
        raw = base64.b64decode(payload)
    except Exception:
        raise HTTPException(400, "could not decode base64 image")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"sprite_seed{int(req.seed)}_steps{int(req.steps)}_{ts}.png"
    with open(os.path.join(SAVED_DIR, filename), "wb") as f:
        f.write(raw)
    return {"filename": filename, "url": f"/saved/{filename}"}


@app.get("/api/saved")
def saved() -> list:
    files = [f for f in os.listdir(SAVED_DIR) if f.lower().endswith(".png")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(SAVED_DIR, f)),
               reverse=True)
    out = []
    for f in files:
        m = SAVED_RE.search(f)
        out.append({
            "filename": f,
            "url": f"/saved/{f}",
            "seed": int(m.group(1)) if m else None,
            "steps": int(m.group(2)) if m else None,
        })
    return out


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/saved", StaticFiles(directory=SAVED_DIR), name="saved")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

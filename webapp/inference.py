"""Model loading + DDIM sampling + RGBA postprocess for the sprite web app.

This is a thin, headless port of the inference path in `train_diffusion.ipynb`:
the same EMA weights, the same device-portable seeded sampler, and the
"Notes & next steps" postprocess (crisp pixels + black->alpha threshold).

Reproducibility: noise is drawn from a CPU `torch.Generator` seeded with `seed`
and *then* moved to the device, so a given seed yields the identical sprite on
CUDA, MPS, or CPU.
"""
from __future__ import annotations

import contextlib
import glob
import os
import re
from dataclasses import dataclass
from typing import Optional

# Reduce CUDA fragmentation OOMs on shared GPUs (must be set before torch import).
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import numpy as np
import torch
from PIL import Image

from diffusers import UNet2DModel, DDIMScheduler
from diffusers.training_utils import EMAModel

# Default checkpoint directory (relative to the project root). Override with the
# SPRITE_CKPT_DIR env var or by passing ckpt_dir into load_model().
DEFAULT_CKPT_DIR = os.environ.get("SPRITE_CKPT_DIR", "checkpoints/diffusion")

# A pixel is "foreground" if any RGB channel exceeds this (on the 0..1 scale).
# The model paints on a flat black background, so this re-keys black -> alpha.
ALPHA_THRESHOLD = 0.10

# Number of train timesteps the scheduler was trained with (see notebook cfg).
NUM_TRAIN_TIMESTEPS = 1000


def pick_device() -> torch.device:
    """CUDA (most-free GPU) -> MPS -> CPU. Portable across Windows/Mac/Linux."""
    if torch.cuda.is_available():
        best, best_free = 0, -1
        for i in range(torch.cuda.device_count()):
            free, _total = torch.cuda.mem_get_info(i)
            if free > best_free:
                best, best_free = i, free
        return torch.device(f"cuda:{best}")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _epoch_of(path: str) -> int:
    m = re.search(r"ckpt_(\d+)\.pth$", os.path.basename(path))
    return int(m.group(1)) if m else -1


def find_latest_checkpoint(ckpt_dir: str = DEFAULT_CKPT_DIR) -> str:
    """Return the highest-epoch ckpt_*.pth in `ckpt_dir`."""
    ckpts = glob.glob(os.path.join(ckpt_dir, "ckpt_*.pth"))
    if not ckpts:
        raise FileNotFoundError(
            f"No ckpt_*.pth found in '{ckpt_dir}'. Train the model or copy a "
            f"checkpoint there (or set SPRITE_CKPT_DIR)."
        )
    return max(ckpts, key=_epoch_of)


def build_unet(config: dict) -> UNet2DModel:
    """Rebuild the exact UNet from the `config` dict saved in the checkpoint."""
    return UNet2DModel(
        sample_size=config["image_size"],
        in_channels=config["channels"],
        out_channels=config["channels"],
        layers_per_block=2,
        block_out_channels=(128, 256, 256, 512),
        down_block_types=("DownBlock2D", "DownBlock2D",
                          "AttnDownBlock2D", "DownBlock2D"),
        up_block_types=("UpBlock2D", "AttnUpBlock2D",
                        "UpBlock2D", "UpBlock2D"),
    )


@dataclass
class LoadedModel:
    model: UNet2DModel
    ddim: DDIMScheduler
    device: torch.device
    image_size: int
    channels: int
    epoch: int
    checkpoint: str


_state: Optional[LoadedModel] = None


def load_model(ckpt_dir: str = DEFAULT_CKPT_DIR) -> LoadedModel:
    """Load the latest checkpoint's EMA weights once; cache module-globally."""
    global _state
    if _state is not None:
        return _state

    device = pick_device()
    ckpt_path = find_latest_checkpoint(ckpt_dir)
    ck = torch.load(ckpt_path, map_location=device, weights_only=False)
    config = ck["config"]

    model = build_unet(config).to(device)
    model.load_state_dict(ck["model"])

    # Swap in the EMA weights used for sampling in the notebook.
    ema = EMAModel(model.parameters(), model_cls=UNet2DModel,
                   model_config=model.config)
    ema.load_state_dict(ck["ema"])
    ema.copy_to(model.parameters())
    model.eval()

    ddim = DDIMScheduler(
        num_train_timesteps=config.get("num_train_timesteps", NUM_TRAIN_TIMESTEPS),
        beta_schedule="squaredcos_cap_v2",
    )

    _state = LoadedModel(
        model=model, ddim=ddim, device=device,
        image_size=config["image_size"], channels=config["channels"],
        epoch=int(ck.get("epoch", -1)) + 1, checkpoint=ckpt_path,
    )
    return _state


def _amp_ctx(device: torch.device):
    if device.type == "cuda":
        return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
    return contextlib.nullcontext()


def _to_rgba(img01: torch.Tensor) -> Image.Image:
    """(3,H,W) in [0,1] -> RGBA PIL with flat-black background keyed to alpha."""
    rgb = (img01.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8)
    # alpha = any channel bright enough to be foreground (re-key black -> transparent)
    alpha = (img01.amax(dim=0) > ALPHA_THRESHOLD).cpu().numpy().astype(np.uint8) * 255
    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, mode="RGBA")


@torch.no_grad()
def generate(seed: int, steps: int = 50) -> Image.Image:
    """Generate one sprite for `seed` using `steps` DDIM steps -> RGBA PIL image.

    Pixels stay crisp (native resolution, nearest-neighbor display upscaling is
    left to the viewer). The same seed reproduces the same sprite on any device.
    """
    st = load_model()
    st.ddim.set_timesteps(steps)

    g = torch.Generator().manual_seed(int(seed))  # CPU generator -> device-portable
    noise = torch.randn(1, st.channels, st.image_size, st.image_size, generator=g)
    x = noise.to(st.device)

    for t in st.ddim.timesteps:
        with _amp_ctx(st.device):
            pred = st.model(x, t).sample
        x = st.ddim.step(pred.float(), t, x).prev_sample

    img01 = (x[0].clamp(-1, 1) + 1) / 2
    return _to_rgba(img01)

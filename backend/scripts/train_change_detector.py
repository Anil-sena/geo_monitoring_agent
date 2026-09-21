"""
Fine-tune a REAL change-detection model on the LEVIR-CD benchmark using TorchGeo,
and save weights that src/models/change_detection.py can load at inference time.

This is the missing piece that turns the capstone from "diff-based demo" into an
actual trained deep-learning model, matching the JD's "PyTorch/TensorFlow +
change/anomaly detection" requirement.

Usage:
    pip install torchgeo lightning
    python scripts/train_change_detector.py --epochs 10 --batch-size 8 \
        --data-dir ./data/levircd --out checkpoints/siamese_levircd.pt

TorchGeo will auto-download LEVIR-CD (~2GB) into --data-dir on first run.
Docs: https://torchgeo.readthedocs.io/en/stable/api/datasets.html#levircd
"""
from __future__ import annotations
import argparse
from pathlib import Path

import torch
from torch.utils.data import DataLoader

try:
    from torchgeo.datasets import LEVIRCDPlus
    from torchgeo.transforms import AugmentationSequential
except ImportError as e:
    raise SystemExit(
        "torchgeo is required for training: pip install torchgeo\n"
        f"Original error: {e}"
    )

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from geoagent.pipeline.change_detection import build_siamese_lite

try:
    from torchgeo.models import FCSiamDiff
    HAS_TORCHGEO_MODEL = True
except ImportError:
    HAS_TORCHGEO_MODEL = False


def build_model(name: str, in_channels: int):
    if name == "fcsiam_diff":
        if not HAS_TORCHGEO_MODEL:
            raise SystemExit("torchgeo.models.FCSiamDiff unavailable — check your torchgeo install.")
        return FCSiamDiff(in_channels=in_channels, classes=1)
    return build_siamese_lite(in_channels=in_channels)


def forward_pass(model, name: str, img1: torch.Tensor, img2: torch.Tensor) -> torch.Tensor:
    if name == "fcsiam_diff":
        stacked = torch.stack([img1, img2], dim=1)  # (B, T=2, C, H, W)
        return torch.sigmoid(model(stacked))
    return model(img1, img2)


def get_dataloaders(data_dir: str, batch_size: int):
    train_ds = LEVIRCDPlus(root=data_dir, split="train", download=True)
    val_ds = LEVIRCDPlus(root=data_dir, split="test", download=True)

    def collate(batch):
        # TorchGeo LEVIRCDPlus sample keys: "image1", "image2", "mask"
        img1 = torch.stack([b["image1"].float() / 255.0 for b in batch])
        img2 = torch.stack([b["image2"].float() / 255.0 for b in batch])
        mask = torch.stack([b["mask"].float() for b in batch])
        return img1, img2, mask

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                               collate_fn=collate, num_workers=2)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False,
                             collate_fn=collate, num_workers=2)
    return train_loader, val_loader


def dice_loss(pred: torch.Tensor, target: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    pred = pred.flatten()
    target = target.flatten()
    inter = (pred * target).sum()
    return 1 - (2 * inter + eps) / (pred.sum() + target.sum() + eps)


def train(args):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Training on {device}")

    train_loader, val_loader = get_dataloaders(args.data_dir, args.batch_size)

    # LEVIR-CD is RGB (3 channels); adjust to match your Sentinel-2 band count
    # at inference time if you fine-tune further on your own AOIs.
    model = build_model(args.model, in_channels=3).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    bce = torch.nn.BCELoss()

    best_val = float("inf")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(args.epochs):
        model.train()
        running = 0.0
        for img1, img2, mask in train_loader:
            img1, img2, mask = img1.to(device), img2.to(device), mask.to(device)
            if mask.ndim == 3:
                mask = mask.unsqueeze(1)

            opt.zero_grad()
            pred = forward_pass(model, args.model, img1, img2)
            if pred.shape[-2:] != mask.shape[-2:]:
                pred = torch.nn.functional.interpolate(pred, size=mask.shape[-2:], mode="bilinear")
            loss = bce(pred, mask) + dice_loss(pred, mask)
            loss.backward()
            opt.step()
            running += loss.item()

        train_loss = running / max(len(train_loader), 1)

        # Validation
        model.eval()
        val_running = 0.0
        with torch.no_grad():
            for img1, img2, mask in val_loader:
                img1, img2, mask = img1.to(device), img2.to(device), mask.to(device)
                if mask.ndim == 3:
                    mask = mask.unsqueeze(1)
                pred = forward_pass(model, args.model, img1, img2)
                if pred.shape[-2:] != mask.shape[-2:]:
                    pred = torch.nn.functional.interpolate(pred, size=mask.shape[-2:], mode="bilinear")
                val_running += (bce(pred, mask) + dice_loss(pred, mask)).item()
        val_loss = val_running / max(len(val_loader), 1)

        print(f"Epoch {epoch+1}/{args.epochs} | train_loss={train_loss:.4f} | val_loss={val_loss:.4f}")

        if val_loss < best_val:
            best_val = val_loss
            torch.save(model.state_dict(), out_path)
            print(f"  ↳ saved best checkpoint to {out_path}")

    print(f"Done. Best val loss: {best_val:.4f}. Load with:\n"
          f"  load_or_create_model('{args.model}', checkpoint='{out_path}')")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=["siamese_lite", "fcsiam_diff"], default="siamese_lite",
                   help="siamese_lite = lightweight custom net (fast, CPU-friendly). "
                        "fcsiam_diff = TorchGeo's benchmark architecture (stronger, needs torchgeo).")
    p.add_argument("--data-dir", default="./data/levircd")
    p.add_argument("--out", default="./checkpoints/siamese_levircd.pt")
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--lr", type=float, default=1e-3)
    train(p.parse_args())

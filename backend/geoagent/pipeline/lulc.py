"""Optional land-use / land-cover classification with the checkpoints in models_store/.

Notes on the bundled weights:
  * keras/cnn_lulc_model_final_9.keras expects an 11-feature vector (its scaler
    has n_features_in_ == 11), not an image tile. extract_lulc9_features() is a
    best-guess feature set and validates the count against the scaler so a
    mismatch fails loudly instead of returning nonsense.
  * resnet/ is a TF-Hub module (embedding only, no LULC head).
  * eurosat.pt is a pickled PyTorch model; format unverified.
TensorFlow / torch are imported lazily so the main pipeline never pays for them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np

from geoagent.config import settings

EUROSAT_CLASSES = [
    "AnnualCrop", "Forest", "HerbaceousVegetation", "Highway", "Industrial",
    "Pasture", "PermanentCrop", "Residential", "River", "SeaLake",
]

_CACHE: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# LULC-9 (Keras + StandardScaler, 11 engineered features)
# ---------------------------------------------------------------------------
def extract_lulc9_features(tile: np.ndarray) -> np.ndarray:
    """
    tile: (C, H, W) array, Sentinel-2-like bands (at minimum Blue,Green,Red,NIR;
    ideally also SWIR1/SWIR2 to compute NDBI as get_imagery already fetches).

    Produces 11 features: mean of up to 6 bands + NDVI mean + NDBI mean + overall
    brightness + std + a texture proxy (std of NDVI). THIS IS A BEST-GUESS ORDERING —
    verify against the real training notebook before trusting predictions.
    """
    c = tile.shape[0]
    band_means = [float(np.nanmean(tile[i])) for i in range(min(c, 6))]
    while len(band_means) < 6:
        band_means.append(0.0)

    def band(i):
        return tile[i].astype(np.float32) if i < c else np.zeros(tile.shape[1:], dtype=np.float32)

    red, nir = band(2), band(3)
    swir1 = band(4)
    eps = 1e-6
    ndvi = (nir - red) / (nir + red + eps)
    ndbi = (swir1 - nir) / (swir1 + nir + eps)

    features = band_means + [
        float(np.nanmean(ndvi)),
        float(np.nanmean(ndbi)),
        float(np.nanmean(tile)),      # overall brightness
        float(np.nanstd(tile)),       # overall texture/contrast
        float(np.nanstd(ndvi)),       # vegetation heterogeneity
    ]
    return np.array(features, dtype=np.float32).reshape(1, -1)


def load_lulc9():
    if "lulc9" in _CACHE:
        return _CACHE["lulc9"]
    import tensorflow as tf
    import joblib

    model_path = Path(settings.lulc_keras_model)
    scaler_path = Path(settings.lulc_scaler)
    if not model_path.exists() or not scaler_path.exists():
        raise FileNotFoundError(f"Missing lulc9 files at {model_path} / {scaler_path}")

    model = tf.keras.models.load_model(str(model_path))
    scaler = joblib.load(str(scaler_path))
    _CACHE["lulc9"] = (model, scaler)
    return model, scaler


def predict_lulc9(tile: np.ndarray) -> Dict[str, Any]:
    model, scaler = load_lulc9()
    feats = extract_lulc9_features(tile)

    expected = getattr(scaler, "n_features_in_", feats.shape[1])
    if feats.shape[1] != expected:
        raise ValueError(
            f"extract_lulc9_features() produced {feats.shape[1]} features but the fitted "
            f"scaler expects {expected}. The feature engineering here is a placeholder — "
            f"update extract_lulc9_features() to match the original training pipeline."
        )

    x = scaler.transform(feats)
    probs = model.predict(x, verbose=0)[0]
    top = int(np.argmax(probs))
    return {
        "model": "lulc9",
        "predicted_class": f"class_{top}",  # unknown taxonomy — see README note below
        "confidence": float(np.max(probs)),
        "note": "lulc9's 9-class label names are unknown; check the Kaggle model card "
                "(daneshjangra/lulc-9) and fill them in here once confirmed.",
    }


# ---------------------------------------------------------------------------
# ResNet50 (TF-Hub module — resnet/tfhub_module.pb, NOT a plain Keras model)
# ---------------------------------------------------------------------------
def load_resnet_hub():
    if "resnet" in _CACHE:
        return _CACHE["resnet"]
    try:
        import tensorflow_hub as hub
    except ImportError:
        raise ImportError(
            "resnet/ is a TF-Hub module (has tfhub_module.pb) — loading it needs "
            "`pip install tensorflow-hub`, not just tensorflow."
        )
    path = str(settings.resnet_dir)
    layer = hub.KerasLayer(path, trainable=False)
    _CACHE["resnet"] = layer
    return layer


def embed_resnet(tile_rgb_224: np.ndarray) -> np.ndarray:
    """tile_rgb_224: (224, 224, 3) float in [0,1]. Returns the embedding vector.
    This is a generic ImageNet backbone — it gives you a feature embedding, not
    LULC class names. Pair it with a small classifier head trained on your own
    labeled tiles if you want it to output LULC labels directly."""
    import tensorflow as tf
    layer = load_resnet_hub()
    x = tf.expand_dims(tf.convert_to_tensor(tile_rgb_224, dtype=tf.float32), axis=0)
    return layer(x).numpy()[0]


# ---------------------------------------------------------------------------
# eurosat.pt (generic PyTorch checkpoint — identity/format unverified)
# ---------------------------------------------------------------------------
def load_eurosat_torch():
    if "eurosat_pt" in _CACHE:
        return _CACHE["eurosat_pt"]
    import torch
    path = Path(settings.eurosat_checkpoint)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found.")
    try:
        obj = torch.load(str(path), map_location="cpu", weights_only=False)
    except Exception as e:
        raise RuntimeError(
            f"Could not load {path}. If this is a raw state_dict rather than a full "
            f"pickled model, you need the original model class (VGG16 or I-JEPA "
            f"backbone) to reconstruct it, e.g.:\n"
            f"  model = VGG16Classifier(num_classes=10)\n"
            f"  model.load_state_dict(torch.load('{path}'))\n"
            f"Original error: {e}"
        )
    if hasattr(obj, "eval"):
        obj.eval()
    _CACHE["eurosat_pt"] = obj
    return obj


def predict_eurosat_torch(tile_224: np.ndarray) -> Dict[str, Any]:
    """tile_224: (3, 224, 224) float in [0,1]."""
    import torch
    model = load_eurosat_torch()
    x = torch.from_numpy(tile_224).float().unsqueeze(0)
    with torch.no_grad():
        out = model(x)
        probs = torch.softmax(out, dim=-1).numpy()[0]
    top = int(np.argmax(probs))
    label = EUROSAT_CLASSES[top] if top < len(EUROSAT_CLASSES) else f"class_{top}"
    return {"model": "eurosat_pt", "predicted_class": label, "confidence": float(np.max(probs))}


# ---------------------------------------------------------------------------
# Dispatcher used by the agent tool
# ---------------------------------------------------------------------------
AVAILABLE_MODELS = ["lulc9", "resnet_embedding", "eurosat_pt"]  # vgg16 excluded: not downloaded


def _resize_tile(tile: np.ndarray, size=(224, 224)) -> np.ndarray:
    from skimage.transform import resize
    chw = tile
    if chw.shape[0] > 3:
        chw = chw[:3]
    hwc = np.transpose(chw, (1, 2, 0))
    if hwc.max() > 1.5:
        hwc = hwc / 255.0
    resized = resize(hwc, size, preserve_range=True, anti_aliasing=True).astype(np.float32)
    return np.transpose(resized, (2, 0, 1))  # back to (C, H, W)


def classify_tile(tile: np.ndarray, model_name: str) -> Dict[str, Any]:
    if model_name == "lulc9":
        return predict_lulc9(tile)
    if model_name == "resnet_embedding":
        rgb224 = np.transpose(_resize_tile(tile), (1, 2, 0))
        emb = embed_resnet(rgb224)
        return {"model": "resnet_embedding", "embedding_dim": len(emb),
                "note": "Feature embedding only — no LULC label without a trained classifier head."}
    if model_name == "eurosat_pt":
        return predict_eurosat_torch(_resize_tile(tile))
    raise ValueError(f"Unknown/unavailable model '{model_name}'. Options: {AVAILABLE_MODELS}")


def classify_geotiff(path: str, model_name: str = "lulc9", tile_size: int = 64) -> Dict[str, Any]:
    import rasterio
    with rasterio.open(path) as src:
        data = src.read()

    c, h, w = data.shape
    results = []
    for y in range(0, h - tile_size + 1, tile_size):
        for x in range(0, w - tile_size + 1, tile_size):
            tile = data[:, y:y + tile_size, x:x + tile_size]
            try:
                res = classify_tile(tile, model_name)
            except Exception as e:
                res = {"model": model_name, "error": str(e)}
            res.update({"row": y // tile_size, "col": x // tile_size})
            results.append(res)
    return {"model": model_name, "n_tiles": len(results), "tiles": results}

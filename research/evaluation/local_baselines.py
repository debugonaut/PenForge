"""Small, CPU-only word recognizers. No product inference or glyph generation.

Nearest references need no fitted weights. Ridge learns a linear multiclass
least-squares classifier on HOG. All vocabulary/centering comes from train only.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import cv2
import numpy as np

from research.evaluation.harness import edit_distance

FEATURE_VERSION = "otsu-bbox-hog512-v1"


def features(data: bytes):
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size > 50_000_000:
        raise ValueError("invalid_verified_image")
    _, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    yy, xx = np.where(mask > 0)
    if not len(xx):
        raise ValueError("empty_ink")
    crop = mask[yy.min() : yy.max() + 1, xx.min() : xx.max() + 1]
    ink = cv2.resize(crop, (128, 32), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    pixel = cv2.resize(ink, (32, 16), interpolation=cv2.INTER_AREA).reshape(-1)
    pixel = np.asarray(pixel / max(float(np.linalg.norm(pixel)), 1e-6), dtype=np.float32)
    gx, gy = cv2.Sobel(ink, cv2.CV_32F, 1, 0), cv2.Sobel(ink, cv2.CV_32F, 0, 1)
    mag, angle = cv2.cartToPolar(gx, gy)
    bins = np.floor((angle % np.pi) * (8 / np.pi)).astype(int).clip(0, 7)
    hist = np.zeros((4, 16, 8), np.float32)
    y, x = np.indices(ink.shape)
    np.add.at(hist, (y.reshape(-1) // 8, x.reshape(-1) // 8, bins.reshape(-1)), mag.reshape(-1))
    hog = np.sqrt(hist.reshape(-1) / max(float(hist.sum()), 1e-6))
    # Geometry descriptors are raster proxies. No timing, pressure or direction.
    moments = cv2.moments(crop, binaryImage=True)
    slant = moments["mu11"] / max(moments["mu02"], 1)
    distance = cv2.distanceTransform(crop, cv2.DIST_L2, 3)
    style = np.array(
        [
            np.log(crop.shape[1] / crop.shape[0]),
            float((crop > 0).mean()),
            float(distance[crop > 0].mean()) / crop.shape[0],
            slant,
        ],
        np.float32,
    )
    return pixel, hog, style


def nearest(train, query, batch_size=64, check=lambda: None):
    train = np.ascontiguousarray(train, dtype=np.float32)
    squared = np.sum(train * train, axis=1)
    result, timings = [], []
    for start in range(0, len(query), batch_size):
        check()
        batch = query[start : start + batch_size]
        begin = time.perf_counter()
        # O(batch * training examples) memory; no all-query distance matrix.
        distance = np.sum(batch * batch, axis=1)[:, None] + squared - 2 * batch @ train.T
        result.extend(distance.argmin(axis=1).tolist())
        timings.append((time.perf_counter() - begin) * 1000 / len(batch))
    return np.asarray(result, dtype=np.int32), timings


def fit_ridge(x, y, classes, regularization):
    x = x.astype(np.float64)
    mean = x.mean(axis=0)
    centered = x - mean
    targets = np.zeros((classes, x.shape[1]), np.float64)
    np.add.at(targets, y, centered)
    gram = centered.T @ centered + regularization * np.eye(x.shape[1])
    weights = np.linalg.solve(gram, targets.T).astype(np.float32)
    priors = (np.bincount(y, minlength=classes) / len(y)).astype(np.float32)
    return {"weights": weights, "mean": mean.astype(np.float32), "priors": priors}


def predict_ridge(model, x, batch_size=64, check=lambda: None):
    predictions, timings = [], []
    for start in range(0, len(x), batch_size):
        check()
        batch = x[start : start + batch_size]
        begin = time.perf_counter()
        logits = (batch - model["mean"]) @ model["weights"] + model["priors"]
        predictions.extend(logits.argmax(axis=1).tolist())
        timings.append((time.perf_counter() - begin) * 1000 / len(batch))
    return np.asarray(predictions, dtype=np.int32), timings


def save_tensors(path: Path, tensors):
    """Safetensors F32 numeric container; no object/pickle serialization."""
    header, blocks, offset = {}, [], 0
    for name, array in sorted(tensors.items()):
        a = np.ascontiguousarray(array, dtype="<f4")
        data = a.tobytes()
        header[name] = {
            "dtype": "F32",
            "shape": list(a.shape),
            "data_offsets": [offset, offset + len(data)],
        }
        offset += len(data)
        blocks.append(data)
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 8)
    with path.open("wb") as out:
        out.write(struct.pack("<Q", len(encoded)))
        out.write(encoded)
        for block in blocks:
            out.write(block)


def load_tensors(path: Path):
    if path.stat().st_size > 128_000_000:
        raise ValueError("checkpoint_size_limit")
    data = path.read_bytes()
    if len(data) < 8:
        raise ValueError("invalid_tensor_header")
    length = struct.unpack("<Q", data[:8])[0]
    if length > 4096 or length > len(data) - 8:
        raise ValueError("invalid_tensor_header")
    header, end, result = json.loads(data[8 : 8 + length]), 0, {}
    if set(header) != {"mean", "priors", "weights"}:
        raise ValueError("unexpected_checkpoint_tensors")
    for name, item in header.items():
        shape, offsets = item["shape"], item["data_offsets"]
        if (
            item["dtype"] != "F32"
            or not 1 <= len(shape) <= 2
            or any(type(n) is not int or n <= 0 or n > 20000 for n in shape)
            or offsets[0] != end
            or offsets[1] - offsets[0] != int(np.prod(shape)) * 4
            or 8 + length + offsets[1] > len(data)
        ):
            raise ValueError("invalid_tensor_layout")
        result[name] = (
            np.frombuffer(
                data, dtype="<f4", count=int(np.prod(shape)), offset=8 + length + offsets[0]
            )
            .reshape(shape)
            .copy()
        )
        if not np.isfinite(result[name]).all():
            raise ValueError("nonfinite_checkpoint")
        end = offsets[1]
    if 8 + length + end != len(data):
        raise ValueError("tensor_trailing_data")
    if result["weights"].shape != (len(result["mean"]), len(result["priors"])):
        raise ValueError("incompatible_tensor_shapes")
    return result


def recognition_metrics(truth, prediction, writers, vocabulary):
    def report(indices):
        pairs = [(truth[i], prediction[i]) for i in indices]
        total = sum(len(a) for a, _ in pairs)
        return {
            "n": len(pairs),
            "wer": sum(a != b for a, b in pairs) / len(pairs) if pairs else None,
            "scalar_cer": sum(edit_distance(a, b) for a, b in pairs) / total if total else None,
        }

    result = report(list(range(len(truth))))
    result["by_writer"] = {
        w: report([i for i, v in enumerate(writers) if v == w]) for w in sorted(set(writers))
    }
    result["in_vocabulary"] = report([i for i, value in enumerate(truth) if value in vocabulary])
    result["out_of_vocabulary"] = report(
        [i for i, value in enumerate(truth) if value not in vocabulary]
    )
    return result

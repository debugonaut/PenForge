"""Separately invoked evaluation primitive. Import is inert; runtime is injected."""

from __future__ import annotations

import json
import time

from PIL import Image

from research.english.r5_data.common import canonical_hash, checked, digest_file


def _percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (
        position - lower
    )


def predict_rows(
    torch, model, processor, tokenizer, rows, staging, *, split: str, max_tokens=8, seed=20261023
):
    """Preserve final rows as final rows; never disguise them as validation inputs."""
    if split not in {"validation", "test"} or not rows or any(r["split"] != split for r in rows):
        raise ValueError("r5_evaluation_requires_one_declared_validation_or_final_split")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("r5_evaluation_inventory_must_be_unique")
    ordered = sorted(rows, key=lambda r: canonical_hash([seed, split, r["id"]]))
    records: list[dict] = []
    latency: list[float] = []
    limit_hits = 0
    model.eval()
    start = time.monotonic()

    def progress():
        print(
            json.dumps(
                {
                    "stage": "r5-evaluation",
                    "split": split,
                    "completed_samples": len(records),
                    "total_samples": len(ordered),
                    "elapsed_seconds": round(time.monotonic() - start, 1),
                }
            ),
            flush=True,
        )

    progress()
    for index in range(0, len(ordered), 8):
        group = ordered[index : index + 8]
        tick = time.monotonic()
        inputs = []
        for row in group:
            if row["path"] != f"images/{row['hash']}.png":
                raise ValueError("r5_eval_image_not_content_addressed")
            path = checked(staging, row["path"])
            if digest_file(path) != row["hash"]:
                raise ValueError("r5_eval_image_bytes_changed")
            with Image.open(path) as image:
                if (
                    image.format != "PNG"
                    or image.size != (128, 128)
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("r5_eval_requires_unchanged_publisher_png")
                inputs.append(
                    processor(images=image.convert("RGB"), return_tensors="pt").pixel_values
                )
        values = torch.cat(inputs, dim=0)
        with torch.inference_mode():
            generated = model.generate(
                pixel_values=values,
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_tokens,
                use_cache=True,
            )
        latency.extend([(time.monotonic() - tick) * 1000 / len(group)] * len(group))
        token_rows = generated.tolist()
        if len(token_rows) != len(group):
            raise ValueError("r5_generation_dropped_or_added_rows")
        for row, ids in zip(group, token_rows, strict=True):
            text = tokenizer.decode(
                ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            records.append({"sample_id": row["id"], "truth": row["label"], "prediction": text})
            limit_hits += int(tokenizer.eos_token_id not in ids[1:])
        if len(records) % 64 == 0 or len(records) == len(ordered):
            progress()
    return records, {
        "samples": len(records),
        "wall_seconds": time.monotonic() - start,
        "per_crop_ms_p50": _percentile(latency, 0.5),
        "per_crop_ms_p95": _percentile(latency, 0.95),
        "generation_limit_hits": limit_hits,
        "split": split,
        "batch_size": 8,
        "preprocessing": "pinned_upstream_full_128_square_rgb_resize_384_v1",
    }

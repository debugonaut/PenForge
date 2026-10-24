"""Neural operations for the owner's English runner; no execution on import."""

from __future__ import annotations

import json
import math
import os
import random
import resource
import threading
import time
from pathlib import Path

from PIL import Image

from research.english.metrics import letter_metrics, word_metrics
from research.english.study import Recipe, source_path
from research.pretrained.artifacts import digest_file
from research.pretrained.runtime import (
    image_processor,
    load_model,
    peak_rss_mb,
    teacher_forced_loss,
    write_json,
)
from research.pretrained.study import encode_label


class Budget:
    def __init__(self, recipe: Recipe, output: Path):
        self.recipe, self.output = recipe, output
        self.start = time.monotonic()
        self.stop = threading.Event()

    def __enter__(self):
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = self.recipe.max_cpu_seconds
        if hard != resource.RLIM_INFINITY:
            ceiling = min(ceiling, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (ceiling, ceiling))

        def watch():
            while not self.stop.wait(0.25):
                reason = None
                if time.monotonic() - self.start > self.recipe.max_wall_seconds:
                    reason = "wall_ceiling_exceeded"
                if peak_rss_mb() > self.recipe.max_rss_mb:
                    reason = "rss_ceiling_exceeded"
                size = 0
                for path in self.output.rglob("*"):
                    try:
                        if path.is_file():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        continue
                if size > self.recipe.max_output_bytes:
                    reason = "output_ceiling_exceeded"
                if reason:
                    try:
                        write_json(
                            self.output / "budget-failure.json",
                            {"status": "failed", "code": reason},
                        )
                    finally:
                        os._exit(124)

        threading.Thread(target=watch, daemon=True).start()
        return self

    def __exit__(self, *_):
        self.stop.set()


def setup(recipe: Recipe):
    import torch

    random.seed(recipe.seed)
    torch.manual_seed(recipe.seed)
    torch.set_num_threads(recipe.threads)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch


def pixels(processor, staging: Path, row: dict):
    path = source_path(staging, row)
    if digest_file(path) != row["hash"]:
        raise ValueError("english_source_changed_during_execution")
    with Image.open(path) as image:
        if (
            image.format != "PNG"
            or getattr(image, "n_frames", 1) != 1
            or image.width * image.height > 4_000_000
        ):
            raise ValueError("english_crop_format_or_pixel_ceiling")
        image.load()
        return processor(images=image.convert("RGB"), return_tensors="pt").pixel_values


def score(task, rows, predictions, vocabulary):
    args = ([r["label"] for r in rows], predictions, [r["writer"] for r in rows])
    return letter_metrics(*args) if task == "letters" else word_metrics(*args, set(vocabulary))


def evaluate(torch, model, processor, tokenizer, rows, vocabulary, staging, recipe, output, split):
    model.eval()
    predictions, times = [], []
    truncated = replacement = 0
    with (output / f"{split}-predictions-private.jsonl").open("x") as evidence:
        for index, row in enumerate(rows):
            tick = time.monotonic()
            with torch.inference_mode():
                ids = model.generate(
                    pixel_values=pixels(processor, staging, row),
                    do_sample=False,
                    num_beams=1,
                    max_new_tokens=recipe.max_label_tokens,
                    use_cache=True,
                )[0].tolist()
            text = tokenizer.decode(
                ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            predictions.append(text)
            truncated += int(ids[-1] != tokenizer.eos_token_id)
            replacement += int("\ufffd" in text)
            times.append((time.monotonic() - tick) * 1000)
            evidence.write(
                json.dumps({"id": row["id"], "truth": row["label"], "prediction": text}) + "\n"
            )
            if (index + 1) % 8 == 0 or index + 1 == len(rows):
                print(
                    json.dumps(
                        {
                            "stage": split,
                            "task": recipe.task,
                            "completed": index + 1,
                            "total": len(rows),
                        }
                    ),
                    flush=True,
                )
    import numpy as np

    result = score(recipe.task, rows, predictions, vocabulary)
    result.update(
        generation_limit_hits=truncated,
        predictions_with_replacement_character=replacement,
        per_crop_including_preprocessing_p50_ms=float(np.quantile(times, 0.5)),
        per_crop_including_preprocessing_p95_ms=float(np.quantile(times, 0.95)),
    )
    return result


def adapt(torch, model, processor, tokenizer, rows, staging, recipe, output):
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    decoder = model.decoder.model.decoder
    for module in [*decoder.layers[-2:], decoder.embed_tokens, model.decoder.output_projection]:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
    parameters = [p for p in model.parameters() if p.requires_grad]
    if not parameters or any(p.requires_grad for p in model.encoder.parameters()):
        raise ValueError("english_incorrect_trainable_scope")
    optimizer = torch.optim.AdamW(
        parameters, lr=recipe.learning_rate, weight_decay=recipe.weight_decay
    )
    rng = random.Random(recipe.seed)
    order = list(range(len(rows)))
    rng.shuffle(order)
    cursor, seen = 0, set()
    model.train()
    model.encoder.eval()
    started = time.monotonic()
    with (output / "loss-private.jsonl").open("x") as evidence:
        for step in range(recipe.max_steps):
            optimizer.zero_grad(set_to_none=True)
            total = 0.0
            for _ in range(recipe.accumulate_steps):
                if cursor == len(order):
                    rng.shuffle(order)
                    cursor = 0
                row = rows[order[cursor]]
                cursor += 1
                seen.add(row["id"])
                labels = torch.tensor(
                    [encode_label(tokenizer, row["label"], recipe.max_label_tokens)]
                )
                loss = teacher_forced_loss(torch, model, pixels(processor, staging, row), labels)
                value = float(loss.detach())
                if not math.isfinite(value):
                    raise ValueError("english_nonfinite_training_loss")
                total += value / recipe.accumulate_steps
                (loss / recipe.accumulate_steps).backward()
            torch.nn.utils.clip_grad_norm_(
                parameters, recipe.clip_grad_norm, error_if_nonfinite=True
            )
            optimizer.step()
            evidence.write(json.dumps({"step": step + 1, "loss": total}) + "\n")
            print(
                json.dumps(
                    {
                        "stage": "finetune",
                        "task": recipe.task,
                        "completed": step + 1,
                        "total": recipe.max_steps,
                    }
                ),
                flush=True,
            )
    return {
        "optimizer_steps": recipe.max_steps,
        "unique_train_samples_seen": len(seen),
        "sample_presentations": recipe.max_steps * recipe.accumulate_steps,
        "trainable_parameters": sum(p.numel() for p in parameters),
        "encoder_frozen": True,
        "objective": recipe.training_objective,
        "wall_seconds": time.monotonic() - started,
        "selection": "fixed_final_step_no_test_tuning",
    }


__all__ = [
    "Budget",
    "adapt",
    "evaluate",
    "image_processor",
    "load_model",
    "peak_rss_mb",
    "score",
    "setup",
    "write_json",
]

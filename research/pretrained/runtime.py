"""Owner-invoked neural operations. Importing this module does not load weights."""

from __future__ import annotations

import json
import math
import os
import random
import resource
import sys
import threading
import time
from pathlib import Path

from PIL import Image

from research.evaluation.deva_recognition import recognition_metrics as cluster_metrics
from research.evaluation.local_baselines import recognition_metrics
from research.preparation.freeze import writer_id
from research.pretrained.artifacts import digest_file
from research.pretrained.study import Recipe, encode_label, source_path


def write_json(path: Path, value):
    # All writes are inside a newly reserved private run directory.
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def peak_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1_000_000 if sys.platform == "darwin" else 1000)


class Budget:
    def __init__(self, recipe: Recipe, output: Path):
        self.recipe, self.output = recipe, output
        self.start = time.monotonic()
        self.stop = threading.Event()

    def __enter__(self):
        # Include imports, checksums, preprocessing, generation and optimizer work.
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = self.recipe.max_cpu_seconds
        if hard != resource.RLIM_INFINITY:
            ceiling = min(ceiling, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (ceiling, ceiling))

        def watch():
            while not self.stop.wait(0.25):
                code = None
                if time.monotonic() - self.start > self.recipe.max_wall_seconds:
                    code = "wall_ceiling_exceeded"
                if peak_rss_mb() > self.recipe.max_rss_mb:
                    code = "rss_ceiling_exceeded"
                size = 0
                for path in self.output.rglob("*"):
                    try:
                        if path.is_file():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        # Atomic report publication may rename a .partial between
                        # discovery and stat; that must not stop the watchdog.
                        continue
                if size > self.recipe.max_output_bytes:
                    code = "output_ceiling_exceeded"
                if code:
                    try:
                        write_json(self.output / "failure.json", {"status": "failed", "code": code})
                    finally:
                        os._exit(124)

        threading.Thread(target=watch, daemon=True).start()
        return self

    def __exit__(self, *_):
        self.stop.set()


def setup(recipe: Recipe):
    # Deliberately lazy: preparation-only checks never import/load a model.
    import torch

    random.seed(recipe.seed)
    torch.manual_seed(recipe.seed)
    torch.set_num_threads(recipe.threads)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch


def load_model(path: Path, tokenizer):
    from transformers import (
        VisionEncoderDecoderConfig,
        VisionEncoderDecoderModel,
        ViTConfig,
        ViTModel,
    )

    config = VisionEncoderDecoderConfig.from_pretrained(
        str(path), local_files_only=True, attn_implementation="eager"
    )
    encoder_config = config.encoder
    if not isinstance(encoder_config, ViTConfig):
        raise ValueError("checkpoint_encoder_must_be_vit")
    # Composite config parsing creates children after the root attention flag;
    # set both children before the explicit encoder constructor can select SDPA.
    encoder_config._attn_implementation = "eager"
    config.decoder._attn_implementation = "eager"
    # The pinned TrOCR checkpoint has no ViT pooler. VisionEncoderDecoderModel's
    # default AutoModel constructor adds one, creating two missing random weights.
    # Supply the matching built-in encoder and retain strict loading for all keys.
    encoder = ViTModel(encoder_config, add_pooling_layer=False)
    model, info = VisionEncoderDecoderModel.from_pretrained(
        str(path),
        config=config,
        encoder=encoder,
        local_files_only=True,
        use_safetensors=True,
        output_loading_info=True,
        attn_implementation="eager",
    )
    if any(
        info.get(key)
        for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")
    ):
        raise ValueError("checkpoint_not_loaded_exactly")
    model.to("cpu")
    model.config.pad_token_id = tokenizer.pad_token_id
    model.config.eos_token_id = tokenizer.eos_token_id
    model.config.decoder_start_token_id = model.config.decoder.decoder_start_token_id
    if model.config.decoder_start_token_id is None:
        raise ValueError("missing_decoder_start_token")
    model.generation_config.pad_token_id = tokenizer.pad_token_id
    model.generation_config.eos_token_id = tokenizer.eos_token_id
    model.generation_config.decoder_start_token_id = model.config.decoder_start_token_id
    return model


def image_processor(bundle: Path):
    from transformers import ViTImageProcessor

    return ViTImageProcessor.from_pretrained(str(bundle), local_files_only=True)


def pixels(processor, staging: Path, row: dict):
    path = source_path(staging, row)
    if digest_file(path) != row["hash"]:
        raise ValueError("source_changed_during_run")
    with Image.open(path) as image:
        return processor(images=image.convert("RGB"), return_tensors="pt").pixel_values


def score(rows, predictions, vocabulary) -> dict:
    truth = [r["label"] for r in rows]
    report = recognition_metrics(
        truth, predictions, [writer_id(r["writer"]) for r in rows], set(vocabulary)
    )
    report["grapheme_cer"] = cluster_metrics(list(zip(truth, predictions)))["cluster_cer"]
    report["wer_definition"] = "whole_word_crop_exact_mismatch_fraction"
    report["normalization"] = "raw_unicode_no_language_correction"
    return report


def evaluate(torch, model, processor, tokenizer, rows, vocabulary, staging, recipe, output, name):
    model.eval()
    predictions, times = [], []
    limit_hits = invalid_utf8 = 0
    started = time.monotonic()
    with (output / f"{name}-predictions-private.jsonl").open("x") as evidence:
        for i, row in enumerate(rows):
            tick = time.monotonic()
            value = pixels(processor, staging, row)
            with torch.inference_mode():
                ids = model.generate(
                    pixel_values=value,
                    do_sample=False,
                    num_beams=1,
                    max_new_tokens=recipe.max_label_tokens,
                    use_cache=True,
                )[0].tolist()
            text = tokenizer.decode(
                ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            predictions.append(text)
            limit_hits += int(ids[-1] != tokenizer.eos_token_id)
            invalid_utf8 += int("\ufffd" in text)
            times.append((time.monotonic() - tick) * 1000)
            evidence.write(
                json.dumps({"sample_id": row["id"], "truth": row["label"], "prediction": text})
                + "\n"
            )
            if (i + 1) % 8 == 0 or i + 1 == len(rows):
                print(
                    json.dumps({"stage": name, "completed": i + 1, "total": len(rows)}), flush=True
                )
    report = score(rows, predictions, vocabulary)
    import numpy as np

    report.update(
        generation_limit_hits=limit_hits,
        predictions_with_replacement_character=invalid_utf8,
        wall_seconds=time.monotonic() - started,
        per_crop_including_preprocessing_p50_ms=float(np.quantile(times, 0.5)),
        per_crop_including_preprocessing_p95_ms=float(np.quantile(times, 0.95)),
    )
    return report


def aligned_token_loss(torch, logits, labels):
    """Cross entropy at the same sequence positions; decoder inputs shift once."""
    if logits.ndim != 3 or labels.ndim != 2 or logits.shape[:-1] != labels.shape:
        raise ValueError("logits_and_targets_must_have_matching_sequence_positions")
    return torch.nn.functional.cross_entropy(
        logits.float().reshape(-1, logits.shape[-1]),
        labels.reshape(-1),
        ignore_index=-100,
    )


def teacher_forced_loss(torch, model, pixel_values, labels):
    # Transformers 4.57.6 falls back to a causal loss for this wrapper, shifting
    # targets again after decoder inputs were already shifted. Avoid that path:
    # construct decoder inputs once and score unchanged targets explicitly.
    decoder_input_ids = model.prepare_decoder_input_ids_from_labels(labels=labels)
    result = model(
        pixel_values=pixel_values,
        decoder_input_ids=decoder_input_ids,
        use_cache=False,
    )
    return aligned_token_loss(torch, result.logits, labels)


def adapt(torch, model, processor, tokenizer, rows, staging, recipe, output):
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    decoder = model.decoder.model.decoder
    # The frozen encoder is always in eval mode; no encoder training/augmentation.
    modules = [*decoder.layers[-2:], decoder.embed_tokens, model.decoder.output_projection]
    for module in modules:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
    parameters = [p for p in model.parameters() if p.requires_grad]
    if not parameters or any(p.requires_grad for p in model.encoder.parameters()):
        raise ValueError("incorrect_trainable_parameter_scope")
    optimizer = torch.optim.AdamW(
        parameters, lr=recipe.learning_rate, weight_decay=recipe.weight_decay
    )
    rng = random.Random(recipe.seed)
    order = list(range(len(rows)))
    rng.shuffle(order)
    cursor = 0
    seen = set()
    model.train()
    model.encoder.eval()
    started = time.monotonic()
    with (output / "loss-private.jsonl").open("x") as loss_log:
        for step in range(recipe.max_steps):
            optimizer.zero_grad(set_to_none=True)
            total_loss = 0.0
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
                    raise ValueError("nonfinite_training_loss")
                total_loss += value / recipe.accumulate_steps
                (loss / recipe.accumulate_steps).backward()
            torch.nn.utils.clip_grad_norm_(
                parameters, recipe.clip_grad_norm, error_if_nonfinite=True
            )
            optimizer.step()
            loss_log.write(json.dumps({"step": step + 1, "loss": total_loss}) + "\n")
            print(
                json.dumps({"stage": "finetune", "completed": step + 1, "total": recipe.max_steps}),
                flush=True,
            )
    del optimizer
    return {
        "optimizer_steps": recipe.max_steps,
        "unique_train_samples_seen": len(seen),
        "sample_presentations": recipe.max_steps * recipe.accumulate_steps,
        "trainable_parameters": sum(p.numel() for p in parameters),
        "total_parameters": sum(p.numel() for p in model.parameters()),
        "encoder_frozen": True,
        "optimizer": "AdamW_constant_lr_no_schedule",
        "objective": recipe.training_objective,
        "selection": "single_fixed_final_step_validation_comparison_no_test_tuning",
        "wall_seconds": time.monotonic() - started,
    }

"""R4 owner-run training and evaluation primitives; never executes on import."""

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

from research.english.nist import LETTERS
from research.english.r4_metrics import letter_metrics
from research.english.r4_recipe import R4Recipe
from research.pretrained.artifacts import canonical_hash, digest_file
from research.pretrained.runtime import (
    peak_rss_mb,
    teacher_forced_loss,
    write_json,
)
from research.pretrained.study import encode_label


class Budget:
    def __init__(self, recipe: R4Recipe, output: Path):
        self.recipe, self.output = recipe, output
        self.started = time.monotonic()
        self.stop = threading.Event()

    def __enter__(self):
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = self.recipe.max_cpu_seconds
        if hard != resource.RLIM_INFINITY:
            ceiling = min(ceiling, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (ceiling, ceiling))

        def watch():
            while not self.stop.wait(0.5):
                reason = None
                if time.monotonic() - self.started > self.recipe.max_wall_seconds:
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
                            {"status": "incomplete", "reason": reason},
                        )
                    finally:
                        os._exit(124)

        threading.Thread(target=watch, daemon=True).start()
        return self

    def __exit__(self, *_):
        self.stop.set()


def setup(recipe: R4Recipe):
    import torch

    random.seed(recipe.seed)
    torch.manual_seed(recipe.seed)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    return torch


def source_path(staging: Path, row: dict) -> Path:
    if row.get("path") != f"images/{row.get('hash')}.png":
        raise ValueError("r4_image_reference_not_content_addressed")
    path = staging / row["path"]
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(staging):
        raise ValueError("r4_source_missing_or_unsafe")
    return path


def pixels(processor, staging: Path, row: dict):
    path = source_path(staging, row)
    if digest_file(path) != row["hash"]:
        raise ValueError("r4_source_changed_during_run")
    with Image.open(path) as image:
        if image.format != "PNG" or image.size != (128, 128) or getattr(image, "n_frames", 1) != 1:
            raise ValueError("r4_source_image_format_or_dimensions_changed")
        image.load()
        return processor(images=image.convert("RGB"), return_tensors="pt").pixel_values


def pixel_batch(torch, processor, staging: Path, rows: list[dict]):
    values = [pixels(processor, staging, row) for row in rows]
    return torch.cat(values, dim=0)


def batches(rows: list[dict], size: int, seed: int, stage: str) -> list[list[dict]]:
    if not rows or size < 1:
        raise ValueError("r4_batches_require_rows_and_positive_size")
    ordered = sorted(rows, key=lambda row: canonical_hash([seed, stage, row["id"]]))
    result = [ordered[start : start + size] for start in range(0, len(ordered), size)]
    if [row["id"] for group in result for row in group] != [row["id"] for row in ordered]:
        raise ValueError("r4_batching_repeated_or_dropped_samples")
    return result


def class_index(label: str) -> int:
    if len(label) != 1 or label not in LETTERS:
        raise ValueError("r4_classifier_label_outside_case_sensitive_52_classes")
    return LETTERS.index(label)


def _pad_labels(torch, tokenizer, labels: list[str], maximum: int):
    encoded = [encode_label(tokenizer, label, maximum) for label in labels]
    width = max(map(len, encoded))
    values = [item + [-100] * (width - len(item)) for item in encoded]
    return torch.tensor(values, dtype=torch.long)


def train_trocr(torch, model, processor, tokenizer, rows, staging, recipe, output):
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    decoder = model.decoder.model.decoder
    for module in [*decoder.layers[-2:], decoder.embed_tokens, model.decoder.output_projection]:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not parameters or any(parameter.requires_grad for parameter in model.encoder.parameters()):
        raise ValueError("r4_trocr_trainable_parameter_scope_mismatch")
    optimizer = torch.optim.AdamW(
        parameters,
        lr=recipe.trocr_learning_rate,
        weight_decay=recipe.weight_decay,
    )
    groups = batches(rows, recipe.trocr_batch_size, recipe.seed, "trocr-one-pass")
    seen: list[str] = []
    optimizer_steps = 0
    model.train()
    model.encoder.eval()
    with (output / "trocr-loss-private.jsonl").open("x", encoding="utf-8") as log:
        for group_start in range(0, len(groups), recipe.trocr_accumulate_batches):
            accumulation = groups[group_start : group_start + recipe.trocr_accumulate_batches]
            optimizer.zero_grad(set_to_none=True)
            combined_loss = 0.0
            for microbatch in accumulation:
                labels = _pad_labels(
                    torch,
                    tokenizer,
                    [row["label"] for row in microbatch],
                    recipe.max_label_tokens,
                )
                values = pixel_batch(torch, processor, staging, microbatch)
                loss = teacher_forced_loss(torch, model, values, labels)
                number = float(loss.detach())
                if not math.isfinite(number):
                    raise ValueError("r4_trocr_nonfinite_loss")
                (loss / len(accumulation)).backward()
                combined_loss += number / len(accumulation)
                seen.extend(row["id"] for row in microbatch)
            torch.nn.utils.clip_grad_norm_(
                parameters, recipe.clip_grad_norm, error_if_nonfinite=True
            )
            optimizer.step()
            optimizer_steps += 1
            log.write(json.dumps({"step": optimizer_steps, "loss": combined_loss}) + "\n")
            print(
                json.dumps(
                    {
                        "stage": "trocr-full-data-pass",
                        "completed_batches": min(group_start + len(accumulation), len(groups)),
                        "total_batches": len(groups),
                    }
                ),
                flush=True,
            )
    if len(seen) != len(rows) or len(set(seen)) != len(rows):
        raise ValueError("r4_trocr_pass_did_not_use_every_training_row_exactly_once")
    return {
        "status": "complete_one_pass",
        "samples": len(seen),
        "unique_samples": len(set(seen)),
        "batches": len(groups),
        "optimizer_steps": optimizer_steps,
        "accumulation_batches": recipe.trocr_accumulate_batches,
        "last_partial_accumulation_supported": len(groups) % recipe.trocr_accumulate_batches != 0,
        "encoder_frozen": True,
        "trainable_scope": "last_two_decoder_layers_and_embedding_output_projection",
    }


def _classifier_forward(torch, encoder, head, pixel_values):
    outputs = encoder(pixel_values=pixel_values, return_dict=True)
    return head(outputs.last_hidden_state[:, 0, :])


def _classifier_head_only_forward(torch, encoder, head, pixel_values):
    with torch.no_grad():
        features = encoder(pixel_values=pixel_values, return_dict=True).last_hidden_state[:, 0, :]
    return head(features)


def _set_head(encoder, head, phase: str):
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    for parameter in head.parameters():
        parameter.requires_grad_(True)
    last_block = encoder.encoder.layer[-1]
    if phase == "final_block":
        for parameter in last_block.parameters():
            parameter.requires_grad_(True)
    elif phase != "head_only":
        raise ValueError("r4_unknown_classifier_phase")
    return last_block


def train_classifier_pass(torch, encoder, head, processor, rows, staging, recipe, *, phase: str):
    last_block = _set_head(encoder, head, phase)
    parameters = [
        parameter
        for parameter in [*encoder.parameters(), *head.parameters()]
        if parameter.requires_grad
    ]
    if not parameters:
        raise ValueError("r4_classifier_has_no_trainable_parameters")
    rate = (
        recipe.classifier_head_learning_rate
        if phase == "head_only"
        else recipe.classifier_last_block_learning_rate
    )
    optimizer = torch.optim.AdamW(parameters, lr=rate, weight_decay=recipe.weight_decay)
    phase_seed = recipe.seed + (0 if phase == "head_only" else 1)
    groups = batches(rows, recipe.classifier_batch_size, phase_seed, f"classifier-{phase}")
    seen: list[str] = []
    encoder.eval()
    head.train()
    if phase == "final_block":
        last_block.train()
    for batch_number, group in enumerate(groups, 1):
        optimizer.zero_grad(set_to_none=True)
        values = pixel_batch(torch, processor, staging, group)
        targets = torch.tensor([class_index(row["label"]) for row in group], dtype=torch.long)
        if phase == "head_only":
            logits = _classifier_head_only_forward(torch, encoder, head, values)
        else:
            logits = _classifier_forward(torch, encoder, head, values)
        loss = torch.nn.functional.cross_entropy(logits, targets)
        if not math.isfinite(float(loss.detach())):
            raise ValueError("r4_classifier_nonfinite_loss")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(parameters, recipe.clip_grad_norm, error_if_nonfinite=True)
        optimizer.step()
        seen.extend(row["id"] for row in group)
        if batch_number % 20 == 0 or batch_number == len(groups):
            print(
                json.dumps(
                    {
                        "stage": f"classifier-{phase}",
                        "completed_batches": batch_number,
                        "total_batches": len(groups),
                    }
                ),
                flush=True,
            )
    if len(seen) != len(rows) or len(set(seen)) != len(rows):
        raise ValueError("r4_classifier_pass_did_not_use_every_training_row_exactly_once")
    return {
        "status": "complete_one_pass",
        "samples": len(seen),
        "unique_samples": len(set(seen)),
        "batches": len(groups),
        "encoder_trainable": phase == "final_block",
        "trainable_scope": "classifier_head"
        if phase == "head_only"
        else "classifier_head_and_final_vit_block",
    }


def evaluate_trocr(torch, model, processor, tokenizer, rows, staging, recipe, output, name):
    model.eval()
    predictions: list[str] = []
    per_crop_ms: list[float] = []
    evaluated_rows: list[dict] = []
    truncated = 0
    evidence_path = output / f"{name}-predictions-private.jsonl"
    with evidence_path.open("x", encoding="utf-8") as evidence:
        for group in batches(rows, 8, recipe.seed, f"evaluate-{name}"):
            tick = time.monotonic()
            values = pixel_batch(torch, processor, staging, group)
            with torch.inference_mode():
                generated = model.generate(
                    pixel_values=values,
                    do_sample=False,
                    num_beams=1,
                    max_new_tokens=recipe.max_label_tokens,
                    use_cache=True,
                )
            elapsed = (time.monotonic() - tick) * 1000 / len(group)
            for row, ids in zip(group, generated.tolist(), strict=True):
                evaluated_rows.append(row)
                text = tokenizer.decode(
                    ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
                )
                predictions.append(text)
                # TrOCR can share the decoder start token and EOS token. Only a
                # generated EOS terminates the output; the initial token does not.
                truncated += int(tokenizer.eos_token_id not in ids[1:])
                per_crop_ms.append(elapsed)
                evidence.write(
                    json.dumps({"sample_id": row["id"], "truth": row["label"], "prediction": text})
                    + "\n"
                )
    report = letter_metrics(
        [row["label"] for row in evaluated_rows],
        predictions,
        [row["writer"] for row in evaluated_rows],
        seed=recipe.seed,
    )
    import numpy as np

    report.update(
        generation_limit_hits=truncated,
        per_crop_ms_p50=float(np.quantile(per_crop_ms, 0.5)),
        per_crop_ms_p95=float(np.quantile(per_crop_ms, 0.95)),
    )
    return report


def evaluate_classifier(torch, encoder, head, processor, rows, staging, recipe, output, name):
    encoder.eval()
    head.eval()
    predictions: list[str] = []
    per_crop_ms: list[float] = []
    evaluated_rows: list[dict] = []
    with (output / f"{name}-predictions-private.jsonl").open("x", encoding="utf-8") as evidence:
        for group in batches(rows, recipe.classifier_batch_size, recipe.seed, f"evaluate-{name}"):
            tick = time.monotonic()
            values = pixel_batch(torch, processor, staging, group)
            with torch.inference_mode():
                class_ids = _classifier_forward(torch, encoder, head, values).argmax(dim=1).tolist()
            elapsed = (time.monotonic() - tick) * 1000 / len(group)
            for row, class_id in zip(group, class_ids, strict=True):
                evaluated_rows.append(row)
                prediction = LETTERS[class_id]
                predictions.append(prediction)
                per_crop_ms.append(elapsed)
                evidence.write(
                    json.dumps(
                        {"sample_id": row["id"], "truth": row["label"], "prediction": prediction}
                    )
                    + "\n"
                )
    report = letter_metrics(
        [row["label"] for row in evaluated_rows],
        predictions,
        [row["writer"] for row in evaluated_rows],
        seed=recipe.seed,
    )
    import numpy as np

    report.update(
        generation_limit_hits=0,
        per_crop_ms_p50=float(np.quantile(per_crop_ms, 0.5)),
        per_crop_ms_p95=float(np.quantile(per_crop_ms, 0.95)),
    )
    return report


def save_classifier_checkpoint(path: Path, encoder, head, *, include_last_block: bool):
    from safetensors.torch import save_file

    tensors = {
        f"head.{name}": value.detach().cpu().contiguous()
        for name, value in head.state_dict().items()
    }
    if include_last_block:
        tensors.update(
            {
                f"last_block.{name}": value.detach().cpu().contiguous()
                for name, value in encoder.encoder.layer[-1].state_dict().items()
            }
        )
    save_file(tensors, str(path))


def load_classifier_checkpoint(path: Path, encoder, head, *, include_last_block: bool):
    from safetensors.torch import load_file

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 150_000_000:
        raise ValueError("r4_classifier_checkpoint_missing_or_too_large")
    tensors = load_file(str(path), device="cpu")
    expected_head = {f"head.{name}" for name in head.state_dict()}
    expected_block = (
        {f"last_block.{name}" for name in encoder.encoder.layer[-1].state_dict()}
        if include_last_block
        else set()
    )
    if set(tensors) != expected_head | expected_block:
        raise ValueError("r4_classifier_checkpoint_tensor_inventory_changed")
    head.load_state_dict(
        {name.removeprefix("head."): tensors[name] for name in expected_head}, strict=True
    )
    if include_last_block:
        encoder.encoder.layer[-1].load_state_dict(
            {name.removeprefix("last_block."): tensors[name] for name in expected_block},
            strict=True,
        )


def checkpoint_files(folder: Path) -> dict[str, str]:
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("r4_checkpoint_folder_missing_or_unsafe")
    result = {}
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError("r4_checkpoint_folder_must_contain_regular_files_only")
        result[path.name] = digest_file(path)
    if not result:
        raise ValueError("r4_checkpoint_folder_empty")
    return result


def validate_checkpoint_files(folder: Path, expected: dict[str, str]):
    if folder.is_symlink() or not folder.is_dir() or checkpoint_files(folder) != expected:
        raise ValueError("r4_checkpoint_files_changed")


def cpu_usage_seconds() -> float:
    return (
        resource.getrusage(resource.RUSAGE_SELF).ru_utime
        + resource.getrusage(resource.RUSAGE_SELF).ru_stime
    )


def peak_memory_mb() -> float:
    return peak_rss_mb()


def prepare_output(path: Path, protected: tuple[Path, ...]) -> Path:
    if path.is_symlink():
        raise ValueError("r4_output_must_not_be_symlink")
    output = path.resolve()
    root = Path(__file__).resolve().parents[2]
    if output == root or (
        output.is_relative_to(root) and not output.is_relative_to(root / "research/runs")
    ):
        raise ValueError("r4_output_must_be_private_run_or_external")
    if output.exists() or any(
        output.is_relative_to(item.resolve()) or item.resolve().is_relative_to(output)
        for item in protected
    ):
        raise ValueError("r4_output_must_be_new_and_separate_from_inputs")
    output.mkdir(parents=True, mode=0o700)
    return output


def save_trocr_checkpoint(model, folder: Path):
    if folder.exists():
        raise ValueError("r4_checkpoint_destination_exists")
    folder.mkdir(mode=0o700)
    model.save_pretrained(folder, safe_serialization=True, max_shard_size="2GB")


def source_code_inventory(root: Path) -> dict[str, str]:
    files = [
        "research/english/r4_study.py",
        "research/english/r4_recipe.py",
        "research/english/r4_metrics.py",
        "research/english/r4_runtime.py",
        "research/english/r4_preflight.py",
        "research/english/r4_run.py",
        "research/english/r4_select.py",
        "research/english/r4_resume.py",
        "research/english/nist.py",
        "research/english/acquire.py",
        "research/english/study.py",
        "research/models.py",
        "research/validate.py",
        "research/preparation/archive_audit.py",
        "research/pretrained/artifacts.py",
        "research/pretrained/runtime.py",
        "research/pretrained/study.py",
        "research/evaluation/harness.py",
        "research/configs/english-r4-letters.json",
        "research/manifests/writers-v1.yaml",
        "scripts/train_english_r4_research.sh",
        "pyproject.toml",
        "uv.lock",
    ]
    return {name: digest_file(root / name) for name in files}


def ensure_source_inventory(root: Path, frozen: dict[str, str]):
    if source_code_inventory(root) != frozen:
        raise ValueError("r4_source_code_changed_during_execution")

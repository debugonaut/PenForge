"""End-to-end R5 mechanics with a synthetic tiny module and inert fixtures.

No pretrained model, tokenizer, processor, image pipeline or dataset is touched. The
module here is two 6-wide ``Linear`` layers and an embedding; it exists only to exercise
one real optimizer step, checkpoint writing, pass ordering, early stopping and resume.
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from PIL import Image

from research.english.r5_model import checkpoint, experiment
from research.english.r5_model.budgets import BudgetExceeded
from research.english.r5_model.rows import CLASSES

VOCAB = 64
WEIGHT_BYTES = b"w" * 4096
CONFIG = {
    "model_type": "vision-encoder-decoder",
    "encoder": {"model_type": "vit"},
    "decoder": {"model_type": "trocr"},
    "decoder_start_token_id": 2,
    "eos_token_id": 2,
    "pad_token_id": 1,
    "tie_word_embeddings": False,
}


def png_bytes(salt: int) -> bytes:
    image = Image.new("L", (128, 128), 255)
    image.putpixel((salt % 128, (salt * 7) % 128), 0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pixel_hash(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        return hashlib.sha256(image.convert("L").tobytes()).hexdigest()


def build_staging(root: Path, labels: str = CLASSES) -> Path:
    staging = root / "stage"
    (staging / "images").mkdir(parents=True)
    records = []
    for index, label in enumerate(labels):
        for offset, (split, writer) in enumerate((("train", "w-train"), ("validation", "w-val"))):
            salt = index * 2 + offset + 1
            data = png_bytes(salt)
            digest = hashlib.sha256(data).hexdigest()
            (staging / "images" / f"{digest}.png").write_bytes(data)
            records.append(
                {
                    "id": f"r5-{label}-{split}",
                    "hash": digest,
                    "path": f"images/{digest}.png",
                    "pixel_hash": pixel_hash(data),
                    "label": label,
                    "writer": writer,
                    "source_group": f"nist-page-{salt}",
                    "source_partition": 0,
                    "split": split,
                    "source_member": f"by_write/hsf_0/w{salt}/u{salt}_{salt}.png",
                }
            )
    (staging / "study.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in records)
    )
    return staging


def build_bundle_and_checkpoint(root: Path) -> tuple[Path, Path]:
    bundle = root / "bundle"
    bundle.mkdir()
    (bundle / "config.json").write_text(json.dumps(CONFIG))
    (bundle / "model.safetensors").write_bytes(WEIGHT_BYTES)
    checkpoint = root / "init"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(json.dumps(CONFIG))
    (checkpoint / "generation_config.json").write_text(
        json.dumps({"bos_token_id": 2, "eos_token_id": 2, "pad_token_id": 1})
    )
    (checkpoint / "model.safetensors").write_bytes(WEIGHT_BYTES)
    return bundle, checkpoint


def fake_verifier(_path: Path) -> dict:
    return {"model_id": "fixture", "revision": "fixture", "files": {}, "scope": "fixture"}


class FakeTokenizer:
    pad_token_id = 1
    eos_token_id = 2
    unk_token_id = 3
    all_special_ids = [0, 1, 2, 3]

    def encode(self, text: str, add_special_tokens: bool = True, truncation: bool = False):
        return [4 + CLASSES.index(text), 2]

    def decode(self, ids, **_):
        return CLASSES[ids[0] - 4] if ids and 4 <= ids[0] < 56 else ""


class FakeProcessor:
    size = {"height": 384, "width": 384}

    def __call__(self, images, return_tensors="pt", do_resize=True):
        return SimpleNamespace(pixel_values=torch.zeros(1, 3, 8, 8))


class Generated:
    def __init__(self, rows):
        self.rows = rows

    def tolist(self):
        return self.rows


class TinyTrOCR(torch.nn.Module):
    """Two decoder layers and one output projection; not a text-recognition model."""

    def __init__(self) -> None:
        super().__init__()
        self.encoder = torch.nn.Linear(1, 1, bias=False)
        self.first = torch.nn.Linear(6, 6)
        self.last = torch.nn.Linear(6, 6)
        self.embed_tokens = torch.nn.Embedding(VOCAB, 6)
        self.output_projection = torch.nn.Linear(6, VOCAB)
        self.decoder = SimpleNamespace(
            model=SimpleNamespace(
                decoder=SimpleNamespace(
                    layers=[self.first, self.last], embed_tokens=self.embed_tokens
                )
            ),
            output_projection=self.output_projection,
        )

    def prepare_decoder_input_ids_from_labels(self, labels):
        shifted = labels.new_zeros(labels.shape)
        shifted[:, 1:] = labels[:, :-1].clone()
        shifted[:, 0] = 0
        shifted.masked_fill_(shifted == -100, self.embed_tokens.num_embeddings - 1)
        return shifted

    def forward(self, pixel_values, decoder_input_ids, use_cache=False):
        batch, length = decoder_input_ids.shape
        pooled = pixel_values.reshape(batch, -1).mean(dim=1, keepdim=True)
        hidden = pooled.unsqueeze(1).expand(-1, length, 6)
        hidden = hidden + self.embed_tokens(decoder_input_ids.clamp(min=0))
        hidden = torch.tanh(self.first(hidden))
        hidden = torch.tanh(self.last(hidden))
        return SimpleNamespace(logits=self.output_projection(hidden))

    def generate(self, pixel_values, **_):
        return Generated([[5, 2]] * pixel_values.shape[0])


class FakeRuntime:
    def setup(self, recipe):
        torch.manual_seed(0)
        return torch

    def tokenizer(self):
        return FakeTokenizer()

    def processor(self):
        return FakeProcessor()

    def load_initial_model(self, checkpoint, tokenizer):
        return TinyTrOCR()


class SequenceScorer:
    def __init__(self, accuracies):
        self.accuracies = list(accuracies)
        self.calls: list[int] = []

    def __call__(self, records, rows):
        self.calls.append(len(records))
        accuracy = self.accuracies.pop(0)
        count = len(records)
        correct = round(accuracy * count)
        return {
            "accuracy": correct / count,
            "correct": correct,
            "n": count,
            "scalar_cer": 1 - correct / count,
            "exact_case_sensitive": True,
            "per_crop_ms_p50": 2.5,
        }


class InterruptingBudget:
    def __init__(self, plan, output):
        self.output = output

    def install_cpu_limit(self):
        return 1.0

    def start(self):
        return None

    def checkpoint(self):
        return None

    @contextmanager
    def phase(self, name, wall_seconds):
        if name.startswith("train-") and name != "train-1":
            raise BudgetExceeded("simulated_phase_interruption")
        yield

    def close(self):
        return None

    def report(self):
        return {"limits": {}, "observed_wall_seconds": 0.0, "phase_wall_seconds": {}}


def recipe_path(root: Path, *, patience: int) -> Path:
    pinned = json.loads(
        (Path(__file__).resolve().parents[2] / "research/configs/english-r5-model.json").read_text()
    )
    pinned["early_stopping_patience"] = patience
    path = root / f"recipe-patience-{patience}.json"
    path.write_text(json.dumps(pinned))
    return path


def make_plan(root: Path, *, patience: int, output_name: str = "run", labels=CLASSES):
    staging = build_staging(root, labels)
    bundle, checkpoint = build_bundle_and_checkpoint(root)
    return experiment.load_plan(
        staging=staging,
        mapping_name="study.jsonl",
        model_bundle=bundle,
        init_checkpoint=checkpoint,
        output=root / output_name,
        recipe_path=recipe_path(root, patience=patience),
    )


def run(plan, scorer, **kwargs):
    return experiment.run_bounded_training(
        plan,
        scorer,
        owner_run=True,
        runtime=FakeRuntime(),
        bundle_verifier=fake_verifier,
        enforce_cpu_limit=False,
        **kwargs,
    )


def test_token_map_may_live_at_root_or_under_decoder(tmp_path):
    bundle = tmp_path / "bundle"
    init = tmp_path / "init"
    nested_bundle = {key: value for key, value in CONFIG.items() if not key.endswith("_token_id")}
    nested_bundle["decoder"] = {
        **CONFIG["decoder"],
        "decoder_start_token_id": 2,
        "eos_token_id": 2,
        "pad_token_id": 1,
    }
    for folder, config in ((bundle, nested_bundle), (init, CONFIG)):
        folder.mkdir()
        (folder / "config.json").write_text(json.dumps(config))
        (folder / "model.safetensors").write_bytes(WEIGHT_BYTES)
    assert "eos_token_id" not in json.loads((bundle / "config.json").read_text())
    experiment._check_configs(init, bundle)
    broken = dict(CONFIG, eos_token_id=3)
    (init / "config.json").write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="token_map"):
        experiment._check_configs(init, bundle)


def test_preflight_rejects_missing_classes(tmp_path):
    plan = make_plan(tmp_path, patience=0, labels="Aa")
    with pytest.raises(ValueError, match="missing_case_sensitive_classes"):
        experiment.preflight(plan, bundle_verifier=fake_verifier)


def test_preflight_rejects_final_test_rows(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    mapping = plan.staging / "study.jsonl"
    row = json.loads(mapping.read_text().splitlines()[0])
    mapping.write_text(mapping.read_text() + json.dumps(dict(row, split="test", id="leak")) + "\n")
    with pytest.raises(ValueError, match="split_outside_train_validation"):
        experiment.preflight(plan, bundle_verifier=fake_verifier)


def test_training_requires_owner_acknowledgment(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    with pytest.raises(ValueError, match="owner_run"):
        experiment.run_bounded_training(
            plan, SequenceScorer([0.5]), owner_run=False, runtime=FakeRuntime()
        )


def test_three_passes_select_the_best_validation_pass_without_a_test(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    scorer = SequenceScorer([0.5, 0.25, 0.75])
    report = run(plan, scorer)

    assert report["status"] == "training_complete_validation_selected"
    assert report["test_evaluated"] is False
    assert report["final_test"] == "not_run_by_part_b"
    assert [item["pass_index"] for item in report["pass_results"]] == [1, 2, 3]
    assert report["selection"]["winner"]["pass_index"] == 3
    assert report["selection"]["rule"].startswith("highest_exact_case_sensitive")
    assert scorer.calls and set(scorer.calls) == {len(CLASSES)}

    output = plan.output
    best_state = json.loads((output / "best" / "state.json").read_text())
    winner = report["selection"]["winner"]
    assert best_state["status"] == "pass_complete"
    assert best_state["weights_only"] is True
    assert best_state["lineage_hash"] == report["lineage_hash"]
    assert best_state["pass_index"] == winner["pass_index"]
    assert best_state["tensors_sha256"] == winner["checkpoint_sha256"]
    assert (
        hashlib.sha256((output / "best" / "state.safetensors").read_bytes()).hexdigest()
        == winner["checkpoint_sha256"]
    )
    assert (output / "best" / "state.json").is_file()
    assert (output / "training-report.json").is_file()
    assert (output / "selection.json").is_file()
    for index in (1, 2, 3):
        assert (output / f"pass-{index:04d}-validation-predictions-private.jsonl").is_file()
        assert (output / f"pass-{index:04d}-loss-private.jsonl").is_file()
        assert (output / f"pass-{index:04d}-score.json").is_file()

    records = [
        json.loads(line)
        for line in (output / "pass-0003-validation-predictions-private.jsonl")
        .read_text()
        .splitlines()
    ]
    assert len({record["sample_id"] for record in records}) == len(records) == len(CLASSES)
    assert {record["truth"] for record in records} == set(CLASSES)
    assert all(record["prediction"] == "B" for record in records)


def test_early_stopping_uses_validation_only(tmp_path):
    plan = make_plan(tmp_path, patience=1)
    scorer = SequenceScorer([0.5, 0.25, 0.75])
    report = run(plan, scorer)
    assert report["status"] == "training_complete_validation_selected"
    assert report.get("early_stopped_after_pass") == 2
    assert len(report["pass_results"]) == 2
    assert report["selection"]["winner"]["pass_index"] == 1
    assert len(scorer.accuracies) == 1, "the third pass must not run"


def test_interrupted_run_is_incomplete_and_resumes_from_the_last_complete_pass(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    first_scorer = SequenceScorer([0.5])
    interrupted = run(
        plan,
        first_scorer,
        budget_factory=lambda budget_plan, output: InterruptingBudget(budget_plan, output),
    )
    assert interrupted["status"] == "incomplete_budget_exceeded"
    assert interrupted["selection"] is None
    assert interrupted["test_evaluated"] is False
    assert checkpoint.load_state(plan.output / "latest")[1].pass_index == 1

    resumed = run(plan, SequenceScorer([0.25, 0.75]), resume=True)
    assert resumed["status"] == "training_complete_validation_selected"
    assert [item["pass_index"] for item in resumed["pass_results"]] == [1, 2, 3]
    assert resumed["resume"]["requested"] is True
    assert resumed["selection"]["winner"]["pass_index"] == 3
    selected = checkpoint.load_selected(plan.output / "best")[1]
    assert selected.pass_index == 3
    assert selected.weights_only is True
    assert selected.tensors_sha256 == resumed["selection"]["winner"]["checkpoint_sha256"]


def test_completed_run_cannot_be_resumed_in_place(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    run(plan, SequenceScorer([0.5, 0.75, 0.25]))
    with pytest.raises(ValueError, match="completed_run_must_not_be_resumed"):
        run(plan, SequenceScorer([0.5]), resume=True)


def test_existing_output_directory_is_refused(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    plan.output.mkdir(parents=True)
    with pytest.raises(ValueError, match="output_directory_must_be_new"):
        run(plan, SequenceScorer([0.5]))


class ExhaustedBudget(InterruptingBudget):
    def __init__(self, plan, output):
        super().__init__(plan, output)
        self.calls = 0

    def checkpoint(self):
        self.calls += 1
        if self.calls > 3:
            raise BudgetExceeded("aggregate_wall_ceiling_exceeded")


def test_budget_exceeded_marks_the_run_incomplete(tmp_path):
    plan = make_plan(tmp_path, patience=0)
    report = run(
        plan,
        SequenceScorer([0.5, 0.75, 0.25]),
        budget_factory=lambda budget_plan, output: ExhaustedBudget(budget_plan, output),
    )
    assert report["status"] == "incomplete_budget_exceeded"
    assert report["reason"] == "aggregate_wall_ceiling_exceeded"
    assert report["selection"] is None
    assert report["test_evaluated"] is False

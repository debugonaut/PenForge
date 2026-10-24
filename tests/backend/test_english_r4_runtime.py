"""Pure batching and class-index checks; no Torch/model execution."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from research.english import r4_runtime
from research.english.r4_runtime import _classifier_head_only_forward, batches, class_index


def test_r4_deterministic_batches_visit_every_row_once_with_a_partial_last_batch():
    rows = [{"id": f"r-{index}"} for index in range(10)]

    first = batches(rows, 4, 5, "test")
    second = batches(rows, 4, 5, "test")

    assert [[row["id"] for row in group] for group in first] == [
        [row["id"] for row in group] for group in second
    ]
    flattened = [row["id"] for group in first for row in group]
    assert len(flattened) == len(set(flattened)) == len(rows)
    assert sorted(flattened) == sorted(row["id"] for row in rows)
    assert [len(group) for group in first] == [4, 4, 2]


def test_r4_class_order_is_exactly_uppercase_then_lowercase():
    assert class_index("A") == 0
    assert class_index("Z") == 25
    assert class_index("a") == 26
    assert class_index("z") == 51
    for invalid in ("AA", "é", "1"):
        with pytest.raises(ValueError):
            class_index(invalid)


def test_frozen_encoder_runs_without_grad_but_trainable_head_runs_with_grad():
    class FakeTorch:
        no_grad_active = False

        class NoGrad:
            def __init__(self, torch):
                self.torch = torch

            def __enter__(self):
                self.torch.no_grad_active = True

            def __exit__(self, *_):
                self.torch.no_grad_active = False

        def no_grad(self):
            return self.NoGrad(self)

    class HiddenStates:
        def __getitem__(self, index):
            assert index == (slice(None), 0, slice(None))
            return "cls-features"

    class Encoder:
        def __call__(self, **kwargs):
            assert kwargs["return_dict"] is True
            assert torch.no_grad_active
            return type("Output", (), {"last_hidden_state": HiddenStates()})()

    class Head:
        def __call__(self, features):
            assert features == "cls-features"
            assert not torch.no_grad_active
            return "logits-with-gradient"

    torch = FakeTorch()

    assert _classifier_head_only_forward(torch, Encoder(), Head(), "pixels") == (
        "logits-with-gradient"
    )


@pytest.mark.parametrize("kind", ["trocr", "classifier"])
def test_evaluator_scores_predictions_against_the_same_shuffled_sample(monkeypatch, tmp_path, kind):
    rows = [
        {"id": "one", "label": "A", "writer": "w1"},
        {"id": "two", "label": "b", "writer": "w2"},
    ]
    monkeypatch.setattr(r4_runtime, "batches", lambda *args: [[rows[1]], [rows[0]]])
    monkeypatch.setattr(r4_runtime, "pixel_batch", lambda torch, processor, staging, group: group)
    torch = SimpleNamespace(inference_mode=nullcontext)
    recipe = SimpleNamespace(seed=17, classifier_batch_size=16, max_label_tokens=8)

    class Values:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

        def argmax(self, **_):
            return self

    class Model:
        def eval(self):
            pass

        def generate(self, pixel_values, **_):
            return Values([[2, ord(row["label"]), 2] for row in pixel_values])

    model = Model()
    if kind == "trocr":
        tokenizer = SimpleNamespace(
            eos_token_id=2,
            decode=lambda ids, **_: chr(ids[1]),
        )
        result = r4_runtime.evaluate_trocr(
            torch, model, None, tokenizer, rows, tmp_path, recipe, tmp_path, kind
        )
    else:
        monkeypatch.setattr(
            r4_runtime,
            "_classifier_forward",
            lambda torch, encoder, head, group: Values(
                [class_index(row["label"]) for row in group]
            ),
        )
        result = r4_runtime.evaluate_classifier(
            torch, model, model, None, rows, tmp_path, recipe, tmp_path, kind
        )
    assert result["accuracy"] == 1
    assert result["correct"] == 2
    assert result["by_writer"]["w1"]["correct"] == 1
    assert result["generation_limit_hits"] == 0

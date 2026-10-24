"""Fixed loss arithmetic and inert call substitutes only; never fits or loads a model."""

import math
from types import SimpleNamespace

import pytest

from research.pretrained.runtime import aligned_token_loss, teacher_forced_loss

# Optional recognition dependencies stay out of ordinary API/worker installs.
torch = pytest.importorskip("torch")


def fixed_logits():
    # Fixed predictions: BOS, two byte-token stand-ins, EOS. No gradients exist.
    labels = torch.tensor([[0, 5, 6, 2]])
    logits = torch.full((1, 4, 8), -12.0)
    for index, token in enumerate(labels[0].tolist()):
        logits[0, index, token] = 0.0
    assert not logits.requires_grad
    return logits, labels


def test_loss_scores_same_target_positions_including_first_and_end_tokens():
    logits, labels = fixed_logits()
    expected = math.log1p(7 * math.exp(-12))
    assert float(aligned_token_loss(torch, logits, labels)) == pytest.approx(expected, abs=1e-7)
    # The old causal fallback uses these wrong next-position targets; this must
    # be expensive even though the same-position predictions above are correct.
    shifted = torch.tensor([[5, 6, 2, -100]])
    assert float(aligned_token_loss(torch, logits, shifted)) > 11


def test_loss_ignores_masked_padding_without_discarding_end_token():
    logits, labels = fixed_logits()
    reference = float(aligned_token_loss(torch, logits, labels))
    padded_logits = torch.cat((logits, torch.full((1, 1, 8), 100.0)), dim=1)
    padded_labels = torch.cat((labels, torch.tensor([[-100]])), dim=1)
    assert float(aligned_token_loss(torch, padded_logits, padded_labels)) == reference
    wrong_end = labels.clone()
    wrong_end[0, -1] = 7
    assert float(aligned_token_loss(torch, logits, wrong_end)) > 2


def test_teacher_forcing_passes_shifted_inputs_once_and_bypasses_wrapper_loss():
    logits, labels = fixed_logits()
    calls = []
    shifted_inputs = torch.tensor([[2, 0, 5, 6]])
    pixels = object()

    class InertModel:
        def prepare_decoder_input_ids_from_labels(self, **kwargs):
            assert kwargs["labels"] is labels
            calls.append("prepare_inputs")
            return shifted_inputs

        def __call__(self, **kwargs):
            assert set(kwargs) == {"pixel_values", "decoder_input_ids", "use_cache"}
            assert kwargs["pixel_values"] is pixels
            assert kwargs["decoder_input_ids"] is shifted_inputs
            assert kwargs["use_cache"] is False
            calls.append("fixed_logits")
            return SimpleNamespace(logits=logits)

    loss = teacher_forced_loss(torch, InertModel(), pixels, labels)
    assert float(loss) < 0.0001
    assert calls == ["prepare_inputs", "fixed_logits"]
    assert not loss.requires_grad


def test_loss_rejects_misaligned_lengths():
    logits, labels = fixed_logits()
    with pytest.raises(ValueError, match="matching_sequence_positions"):
        aligned_token_loss(torch, logits[:, :-1], labels)

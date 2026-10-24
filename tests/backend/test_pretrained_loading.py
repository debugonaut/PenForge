"""Loader control flow with inert substitutes; no real model or tensors are created."""

import sys
from types import SimpleNamespace

import pytest

from research.pretrained.runtime import load_model


@pytest.fixture
def loader_substitutes(monkeypatch):
    calls = []

    class EncoderConfig(SimpleNamespace):
        pass

    config = SimpleNamespace(
        encoder=EncoderConfig(), decoder=SimpleNamespace(decoder_start_token_id=2)
    )
    model = SimpleNamespace(config=config, generation_config=SimpleNamespace())
    model.to = lambda device: calls.append(("device", device))
    info = dict(missing_keys=[], unexpected_keys=[], mismatched_keys=[], error_msgs=[])

    def read_config(path, **kwargs):
        assert kwargs == {"local_files_only": True, "attn_implementation": "eager"}
        calls.append(("config", path))
        return config

    def build_encoder(encoder_config, *, add_pooling_layer):
        assert encoder_config is config.encoder
        assert encoder_config._attn_implementation == "eager"
        assert add_pooling_layer is False
        calls.append(("encoder_without_pooler",))
        return SimpleNamespace(config=encoder_config, pooler=None)

    def read_checkpoint(path, **kwargs):
        assert kwargs["config"] is config
        assert config.decoder._attn_implementation == "eager"
        assert kwargs["encoder"].config is config.encoder
        assert kwargs["encoder"].pooler is None
        assert kwargs["local_files_only"] is True
        assert kwargs["use_safetensors"] is True
        assert kwargs["output_loading_info"] is True
        assert kwargs["attn_implementation"] == "eager"
        calls.append(("checkpoint", path))
        return model, info

    # Replace every lazy framework import with non-neural objects. These tests
    # cannot construct a real model, read weight tensors, infer or train.
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            VisionEncoderDecoderConfig=SimpleNamespace(from_pretrained=read_config),
            ViTConfig=EncoderConfig,
            ViTModel=build_encoder,
            VisionEncoderDecoderModel=SimpleNamespace(from_pretrained=read_checkpoint),
        ),
    )
    return model, info, calls


def test_loader_uses_checkpoint_encoder_without_pooler_and_keeps_token_ids(
    loader_substitutes, tmp_path
):
    model, _, calls = loader_substitutes
    tokenizer = SimpleNamespace(pad_token_id=1, eos_token_id=2)
    assert load_model(tmp_path, tokenizer) is model
    assert calls == [
        ("config", str(tmp_path)),
        ("encoder_without_pooler",),
        ("checkpoint", str(tmp_path)),
        ("device", "cpu"),
    ]
    assert model.config.decoder_start_token_id == 2
    assert model.generation_config.decoder_start_token_id == 2
    assert model.generation_config.eos_token_id == 2
    assert model.generation_config.pad_token_id == 1


@pytest.mark.parametrize(
    "field,problem",
    [
        ("missing_keys", ["encoder.embeddings.patch_embeddings.projection.weight"]),
        ("missing_keys", ["encoder.pooler.dense.weight"]),
        ("unexpected_keys", ["encoder.pooler.dense.weight"]),
        ("mismatched_keys", [("decoder.model.decoder.embed_tokens.weight", (1,), (2,))]),
        ("error_msgs", ["fixed loading failure"]),
    ],
)
def test_loader_still_refuses_every_checkpoint_discrepancy(
    loader_substitutes, tmp_path, field, problem
):
    _, info, calls = loader_substitutes
    info[field] = problem
    with pytest.raises(ValueError, match="checkpoint_not_loaded_exactly"):
        load_model(tmp_path, SimpleNamespace(pad_token_id=1, eos_token_id=2))
    assert ("device", "cpu") not in calls


def test_loader_refuses_missing_decoder_start_token(loader_substitutes, tmp_path):
    model, _, _ = loader_substitutes
    model.config.decoder.decoder_start_token_id = None
    with pytest.raises(ValueError, match="missing_decoder_start_token"):
        load_model(tmp_path, SimpleNamespace(pad_token_id=1, eos_token_id=2))


def test_loader_refuses_other_encoder_architectures(loader_substitutes, tmp_path):
    model, _, calls = loader_substitutes
    model.config.encoder = SimpleNamespace()
    with pytest.raises(ValueError, match="checkpoint_encoder_must_be_vit"):
        load_model(tmp_path, SimpleNamespace(pad_token_id=1, eos_token_id=2))
    assert calls == [("config", str(tmp_path))]

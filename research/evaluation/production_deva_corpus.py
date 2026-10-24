"""Future licensed corpus admission/execution. No production word list is supplied."""

import hashlib
from collections import Counter
from typing import Literal, Self

from penforge_contracts.schemas import Contract, Digest
from penforge_contracts.unicode import normalize, scalars
from pydantic import Field, model_validator


class CorpusWord(Contract):
    text: str = Field(min_length=1, max_length=128)
    codepoints: list[int] = Field(min_length=1, max_length=128)
    frequency: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def exact(self) -> Self:
        if scalars(self.text) != self.codepoints or any(c.isspace() for c in self.text):
            raise ValueError("corpus_word_identity_mismatch")
        return self


class ProductionCorpus(Contract):
    schema_version: Literal[1] = 1
    corpus: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    license: str = Field(min_length=1)
    license_status: Literal["independently_reviewed"]
    license_evidence: Digest
    content_hash: Digest
    tokenization: Literal["unicode_whitespace_v1"]
    normalization_policy: Literal["NFC", "NFD", "preserve_input"]
    unicode_version: Literal["17.0.0"] = "17.0.0"
    frequency_method: Literal["count_normalized_tokens_v1"]
    sampling_method: Literal["top_n_frequency_scalar_tiebreak_v1"]
    reviewer_evidence: Digest
    evidence_domain: Literal["licensed_natural_text"]
    words: list[CorpusWord] = Field(min_length=200, max_length=500)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({tuple(w.codepoints) for w in self.words}) != len(self.words):
            raise ValueError("duplicate_corpus_word")
        return self


def ingest(manifest: ProductionCorpus, source: bytes):
    if "sha256:" + hashlib.sha256(source).hexdigest() != manifest.content_hash:
        raise ValueError("corpus_content_hash_mismatch")
    text = source.decode("utf-8", errors="strict")
    scalars(text)

    def norm(token):
        return (
            token
            if manifest.normalization_policy == "preserve_input"
            else "".join(map(chr, normalize(scalars(token), manifest.normalization_policy)))
        )

    counts = Counter(norm(token) for token in text.split())
    expected = sorted(counts, key=lambda w: (-counts[w], scalars(w)))[: len(manifest.words)]
    if expected != [w.text for w in manifest.words] or any(
        counts[w.text] != w.frequency for w in manifest.words
    ):
        raise ValueError("corpus_frequency_sampling_mismatch")
    return manifest


def execute_corpus(manifest, source, shape):
    ingest(manifest, source)
    results = [shape(word.codepoints) for word in manifest.words]
    return dict(
        corpus=manifest.corpus,
        version=manifest.version,
        content_hash=manifest.content_hash,
        n=len(results),
        results=results,
        production_validated=False,
        remaining_gates=["real_writer_disjoint_handwriting", "independent_human_shaping_review"],
    )

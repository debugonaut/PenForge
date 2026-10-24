"""Fixed strings only. No model inference, training or calibration."""

import pytest

from research.english.metrics import letter_metrics, word_metrics


def test_letter_metrics_preserve_case_and_do_not_count_multiletter_guesses_as_correct():
    result = letter_metrics(
        ["A", "a", "I", "l"], ["A", "A", "l", "letter"], ["w1", "w1", "w2", "w2"]
    )
    assert result["accuracy"] == 0.25
    assert result["case_only_errors"] == 1
    assert result["by_case"]["uppercase"] == {"n": 2, "accuracy": 0.5}
    assert result["by_case"]["lowercase"] == {"n": 2, "accuracy": 0.0}
    assert result["by_letter"]["Z"] == {"n": 0, "accuracy": None}
    assert {"true": "l", "predicted": "<other>", "n": 1} in result["confusion"]


def test_letter_metrics_never_fold_compatibility_characters_or_trim_predictions():
    result = letter_metrics(["K", "A"], ["K", "A "], ["w1", "w2"])
    assert result["accuracy"] == 0
    assert result["case_only_errors"] == 0


def test_word_metrics_separate_word_and_character_errors_and_known_labels():
    result = word_metrics(["cat", "Dog"], ["cat", "dog"], ["w1", "w2"], {"cat"})
    assert result["exact_word_accuracy"] == 0.5
    assert result["scalar_cer"] == pytest.approx(1 / 6)
    assert result["in_vocabulary"]["wer"] == 0
    assert result["out_of_vocabulary"]["wer"] == 1
    assert result["by_writer"]["w2"]["wer"] == 1


def test_word_insertions_can_exceed_one_hundred_percent_character_error():
    result = word_metrics(["a"], ["aaaa"], ["w1"], set())
    assert result["scalar_cer"] == 3
    assert result["exact_word_accuracy"] == 0


@pytest.mark.parametrize(
    "truth,prediction,writers",
    [([], [], []), (["a"], [], ["w"]), ([""], ["a"], ["w"]), (["a"], ["a"], [""])],
)
def test_english_metrics_refuse_missing_or_misaligned_observations(truth, prediction, writers):
    with pytest.raises(ValueError):
        word_metrics(truth, prediction, writers, set())


def test_letter_metrics_reject_words_and_unrequested_script_labels():
    for label in ("cat", "é", "क"):
        with pytest.raises(ValueError, match="single_ascii_letters"):
            letter_metrics([label], [label], ["w1"])

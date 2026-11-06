import importlib.util
from pathlib import Path

import pytest


def corpus_module():
    path = Path(__file__).resolve().parents[1] / "prepare_corpus.py"
    assert path.is_file(), "Code retrieval corpus builder is not implemented"
    spec = importlib.util.spec_from_file_location("code_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_preserve_end_and_overlap_without_truncation():
    windows = corpus_module().windows("abcdefghijklmnop", size=8, overlap=2)
    assert windows == [(0, 8, "abcdefgh"), (6, 14, "ghijklmn"), (12, 16, "mnop")]


def test_qrels_are_from_declared_source_symbol_not_model_suggestions():
    module = corpus_module()
    source = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
    begin, end = module.declaration_span(source, "beta", ".py")
    assert source[begin:end] == "def beta("
    with pytest.raises(ValueError):
        module.declaration_span(source, "missing", ".py")

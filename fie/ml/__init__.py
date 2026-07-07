"""Trainable ML backend: generate a large labeled dataset, train a classifier,
serve it through the same reconstruction contract as the other engines.

This is the honest answer to "can we train it on a large dataset?" — yes, and
the pipeline is here: dataset -> train -> `--engine ml`, scored by the SAME
evaluation harness as the rule-based and LLM engines.
"""
from .dataset import generate_dataset                       # noqa: F401
from .train import train_model, train_external, load_latest  # noqa: F401

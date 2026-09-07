"""Answering natural-language questions against the local warehouse.

A small router classifies the question and a deterministic template answers it;
anything no template covers falls through to a tool-calling agent.
"""

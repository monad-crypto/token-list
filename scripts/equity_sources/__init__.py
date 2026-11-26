"""Issuer sources for the tokenized equities list."""

from equity_sources import anchored, xstocks

SOURCES = [xstocks.SOURCE, anchored.SOURCE]

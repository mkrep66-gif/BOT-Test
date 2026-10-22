"""XRPL arbitrage bot system.

Finds XRP -> token -> ... -> XRP loops across XRPL order books and AMM pools,
executes them atomically (Batch / all-or-nothing) where the ledger allows,
and logs everything for review and tax export.
"""

__version__ = "0.1.0"

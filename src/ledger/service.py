from pathlib import Path

import pandas as pd

from .loader import LedgerLoader
from .mapper import build_account_mapping
from .splitter import LedgerSplitter


class LedgerService:
    """
    High-level interface for working with the master ledger.
    """

    def __init__(self, csv_path: str | Path):
        self.loader = LedgerLoader(csv_path)

        self.df: pd.DataFrame | None = None
        self.account_mapping: dict[str, str] = {}
        self.scenario_ledgers: dict[str, pd.DataFrame] = {}

    def initialize(self) -> None:
        """Load and preprocess the ledger."""
        self.df = self.loader.load()
        self.account_mapping = build_account_mapping(self.df)
        self.scenario_ledgers = LedgerSplitter.split(self.df)

    def get_scenario(self, account_id: str) -> str:
        if self.df is None:
            raise RuntimeError("LedgerService.initialize() must be called before use")
        if account_id not in self.account_mapping:
            raise ValueError(f"Unknown account_id: {account_id}")

        return self.account_mapping[account_id]

    def get_ledger(self, scenario_id: str) -> pd.DataFrame:
        if self.df is None:
            raise RuntimeError("LedgerService.initialize() must be called before use")
        if scenario_id not in self.scenario_ledgers:
            raise ValueError(f"Unknown scenario_id: {scenario_id}")

        return self.scenario_ledgers[scenario_id].copy()

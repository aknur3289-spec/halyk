from pathlib import Path
import pandas as pd


class LedgerLoader:
    REQUIRED_COLUMNS = {
        "txn_id",
        "date",
        "account_id",
        "counterparty",
        "description",
        "amount",
        "currency",
    }

    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> pd.DataFrame:

        if not self.path.exists():
            raise FileNotFoundError(self.path)

        try:
            df = pd.read_csv(self.path)
        except Exception as e:
            raise RuntimeError(
                f"Failed to read ledger: {e}"
            )

        missing = self.REQUIRED_COLUMNS - set(df.columns)

        if missing:
            raise ValueError(
                f"Missing columns: {missing}"
            )

        return df
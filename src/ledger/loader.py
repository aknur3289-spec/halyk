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

        if df["txn_id"].isna().any() or (df["txn_id"].astype(str).str.strip() == "").any():
            raise ValueError("Ledger contains a blank txn_id")
        if df["txn_id"].duplicated().any():
            duplicates = df.loc[df["txn_id"].duplicated(), "txn_id"].head(5).tolist()
            raise ValueError(f"Ledger contains duplicate txn_id values: {duplicates}")
        if df["account_id"].isna().any() or (df["account_id"].astype(str).str.strip() == "").any():
            raise ValueError("Ledger contains a blank account_id")

        try:
            df["date"] = pd.to_datetime(df["date"], errors="raise")
            df["amount"] = pd.to_numeric(df["amount"], errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Ledger has an invalid date or amount: {exc}") from exc

        if df["currency"].isna().any() or (df["currency"].astype(str).str.strip() == "").any():
            raise ValueError("Ledger contains a blank currency")
        df["currency"] = df["currency"].astype(str).str.upper().str.strip()

        return df

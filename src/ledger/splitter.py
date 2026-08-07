import pandas as pd
from .mapper import extract_scenario_id

class LedgerSplitter:

    @staticmethod
    def split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:

        scenarios = {}

        grouped = df.groupby(
            df["txn_id"].apply(extract_scenario_id)
        )

        for scenario, group in grouped:
            scenarios[scenario] = group.reset_index(drop=True)

        return scenarios

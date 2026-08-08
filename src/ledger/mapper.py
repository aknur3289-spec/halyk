import re
import pandas as pd

TXN_PATTERN = re.compile(r"^TXN-([A-Z0-9]+)-\d+$")

def extract_scenario_id(txn_id: str) -> str:
    match = TXN_PATTERN.match(txn_id)

    if not match:
        raise ValueError(
            f"Invalid transaction id: {txn_id}"
        )

    return match.group(1)


def build_account_mapping(
    ledger: pd.DataFrame,
) -> dict[str, str]:

    mapping: dict[str, str] = {}

    for _, row in ledger.iterrows():

        scenario = extract_scenario_id(
            row["txn_id"]
        )

        account = row["account_id"]

        if account in mapping:

            if mapping[account] != scenario:
                raise ValueError(
                    f"{account} belongs to multiple scenarios."
                )

        mapping[account] = scenario

    return mapping
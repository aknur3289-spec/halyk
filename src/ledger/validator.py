REQUIRED_COLUMNS = [
    "txn_id",
    "account_id",
]

def validate_columns(df):

    missing = [
        c
        for c in REQUIRED_COLUMNS
        if c not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing columns: {missing}"
        )
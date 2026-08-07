from src.ledger.service import LedgerService


def main():
    ledger = LedgerService("data/master_ledger_2025.csv")

    ledger.initialize()

    print(f"Loaded {len(ledger.df)} transactions")
    print(f"Found {len(ledger.account_mapping)} accounts")
    print(f"Found {len(ledger.scenario_ledgers)} scenarios")

    # Example
    account = next(iter(ledger.account_mapping))
    scenario = ledger.get_scenario(account)

    print(f"{account} -> {scenario}")

    borrower_ledger = ledger.get_ledger(scenario)

    print(borrower_ledger.head())


if __name__ == "__main__":
    main()
from datetime import datetime

from pydantic import BaseModel


class LedgerTransaction(BaseModel):

    txn_id: str

    date: datetime

    account_id: str

    counterparty: str

    description: str

    amount: float

    currency: str
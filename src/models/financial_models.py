from pydantic import BaseModel


class FinancialFacts(BaseModel):
    revenue: float | None = None
    ebitda: float | None = None
    debt: float | None = None
    equity: float | None = None
    cash: float | None = None
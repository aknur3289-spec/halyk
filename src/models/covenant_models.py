from pydantic import BaseModel


class CovenantSpec(BaseModel):
    clause: str
    metric: str
    calculator: str
    operator: str
    threshold: float
    currency: str
    period: str
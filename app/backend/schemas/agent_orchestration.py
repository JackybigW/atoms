from pydantic import BaseModel


class DraftPlanItem(BaseModel):
    id: str
    text: str


class TodoItemPayload(BaseModel):
    id: str
    text: str
    status: str

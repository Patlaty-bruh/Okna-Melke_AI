from enum import Enum
from typing import Annotated
from typing_extensions import TypedDict

from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


# ---------- ENUMS ----------

class BudgetLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ManagerHandoffStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    COMPLETED = "completed"


# ---------- USER PREFERENCES ----------

class Preferences(BaseModel):
    profile_color: str | None = None
    lamination_color: str | None = None
    lamination_type: str | None = None
    mass_coloration_of_the_profile: bool | None = None

    window_shape: str | None = None
    number_of_leaves: int | None = None
    opening_type: str | None = None

    insulated_glass_unit: str | None = None
    decorative_grilles: bool | None = None
    windowsills: str | None = None
    mosquito_nets: bool | None = None
    child_safety: bool | None = None
    window_sills_and_reveals: bool | None = None
    hardware: str | None = None


# ---------- USER CONTEXT ----------

class ContextUser(BaseModel):
    budget_amount: int | None = None
    budget_level: BudgetLevel | None = None
    currency: str | None = None

    legal_entity: bool | None = None
    clients_building_type: str | None = None
    product_category: str | None = None

    selected_products: list[int] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)


# ---------- PRODUCT SEARCH ----------

class ProductCandidate(BaseModel):
    product_id: int
    name: str
    characteristics: dict = Field(default_factory=dict)


# ---------- MANAGER HANDOFF ----------

class ContactInfo(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class ManagerHandoff(BaseModel):
    contact: ContactInfo = Field(default_factory=ContactInfo)
    status: ManagerHandoffStatus = ManagerHandoffStatus.NOT_REQUESTED
    additional_comment: str | None = None


# ---------- USER ----------

class User(BaseModel):
    id_user: int
    context_user: ContextUser = Field(default_factory=ContextUser)
    contact_info: ContactInfo = Field(default_factory=ContactInfo)


# ---------- LANGGRAPH STATE ----------

class State(TypedDict):
    user: User

    messages: Annotated[list[AnyMessage], add_messages]

    last_user_message: str | None

    candidate_products: list[ProductCandidate]

    manager_handoff: ManagerHandoff

    calculator_url: str | None
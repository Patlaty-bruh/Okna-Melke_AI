"""Pydantic-модели агента: критерии поиска в каталоге, результаты инструментов, ответ клиенту."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from state import ManagerHandoff

# Допустимые категории базы знаний (совпадают с категориями чанков RAG).
KNOWLEDGE_CATEGORIES: tuple[str, ...] = (
    "company",
    "products",
    "technology",
    "warranty",
    "installation",
    "maintenance",
    "ordering",
    "delivery",
    "faq",
    "documents",
)

# Категории из описания агента, которых нет в базе знаний отдельным документом.
KNOWLEDGE_CATEGORY_ALIASES: dict[str, str] = {"measurement": "installation"}

CATALOG_LIMIT_MAX = 10


class ProductType(str, Enum):
    PROFILE = "profile"
    GLASS_UNIT = "glass_unit"
    LAMINATION = "lamination"
    HARDWARE = "hardware"


class CoatingType(str, Enum):
    LAMINATION = "lamination"
    COOL_COLOURS = "cool_colours"


class ThermalPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolStatus(str, Enum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    ERROR = "error"


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    ERROR = "error"


class ActionType(str, Enum):
    CALCULATOR_LINK = "calculator_link"


class ProductSearchQuery(BaseModel):
    """Критерии поиска продукции. Формируются отдельным вызовом LLM, проверяются Python."""

    model_config = ConfigDict(extra="ignore")

    targets: list[ProductType] = Field(default_factory=lambda: [ProductType.PROFILE])

    # Профильные системы
    profile_names: list[str] = Field(default_factory=list)
    min_thermal_protection: float | None = None
    min_air_chambers: int | None = None
    min_mounting_width_mm: int | None = None
    profile_class: str | None = None

    # Стеклопакеты
    glass_unit_names: list[str] = Field(default_factory=list)
    glass_unit_chambers: int | None = None
    is_heat_saving: bool | None = None

    # Ламинация и Cool Colours
    lamination_names: list[str] = Field(default_factory=list)
    lamination_coating_type: CoatingType | None = None

    # Фурнитура
    hardware_manufacturer: str | None = None
    hardware_model: str | None = None

    thermal_priority: ThermalPriority | None = None
    limit: int = 5

    @field_validator("targets")
    @classmethod
    def _targets_not_empty(cls, value: list[ProductType]) -> list[ProductType]:
        return value or [ProductType.PROFILE]

    @field_validator("limit")
    @classmethod
    def _limit_within_bounds(cls, value: int) -> int:
        return max(1, min(value, CATALOG_LIMIT_MAX))

    @field_validator("min_thermal_protection")
    @classmethod
    def _thermal_not_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("Теплозащита не может быть отрицательной")
        return value


class ProductHit(BaseModel):
    """Найденная запись каталога. Только данные из БД."""

    product_type: ProductType
    product_id: int
    name: str
    characteristics: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None


class KnowledgeHit(BaseModel):
    """Фрагмент базы знаний."""

    text: str
    source_file: str
    section: str | None = None
    category: str | None = None
    source_url: str | None = None


class AgentAction(BaseModel):
    """Дополнительное действие для клиента."""

    type: ActionType
    url: str | None = None


class AgentResponse(BaseModel):
    """Структурированный ответ агента для backend и терминала."""

    message: str
    status: ResponseStatus = ResponseStatus.SUCCESS
    actions: list[AgentAction] = Field(default_factory=list)
    handoff: ManagerHandoff = Field(default_factory=ManagerHandoff)

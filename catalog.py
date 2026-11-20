"""Каталог продукции в PostgreSQL: параметризованные запросы, фильтрация и ранжирование."""

from functools import lru_cache
from typing import Any

import psycopg
from psycopg.rows import dict_row

from config import Settings, get_settings
from models import ProductHit, ProductSearchQuery, ProductType, ThermalPriority


class Catalog:
    """Чтение каталога продукции. SQL формируется только этим модулем из разрешённых полей."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        try:
            self._conn = psycopg.connect(
                **settings.conninfo_kwargs, autocommit=True, row_factory=dict_row
            )
        except psycopg.Error as error:
            raise RuntimeError(f"Не удалось подключиться к каталогу PostgreSQL: {error}") from error

    def close(self) -> None:
        self._conn.close()

    def search(self, query: ProductSearchQuery) -> list[ProductHit]:
        """Возвращает записи каталога по критериям, сгруппированные по типу продукции."""

        handlers = {
            ProductType.PROFILE: self._search_profiles,
            ProductType.GLASS_UNIT: self._search_glass_units,
            ProductType.LAMINATION: self._search_laminations,
            ProductType.HARDWARE: self._search_hardware,
        }
        hits: list[ProductHit] = []
        for target in dict.fromkeys(query.targets):
            try:
                hits.extend(handlers[target](query))
            except psycopg.Error as error:
                raise RuntimeError(
                    f"Ошибка запроса к каталогу ({target.value}): {error}"
                ) from error
        return hits

    # ---------- профильные системы ----------

    def _search_profiles(self, query: ProductSearchQuery) -> list[ProductHit]:
        clauses, params = self._profile_clauses(query)
        params["limit"] = query.limit
        order = (
            "thermal_protection ASC NULLS LAST"
            if query.thermal_priority is ThermalPriority.LOW
            else "thermal_protection DESC NULLS LAST"
        )
        rows = self._fetch(
            f"""
            SELECT id, name, mounting_width_mm, air_chambers, thermal_protection,
                   glass_unit_thickness_mm, profile_class, source_url
            FROM profiles
            {self._where(clauses)}
            ORDER BY {order}, name
            LIMIT %(limit)s
            """,
            params,
        )
        return [
            ProductHit(
                product_type=ProductType.PROFILE,
                product_id=row["id"],
                name=row["name"],
                characteristics={
                    "mounting_width_mm": row["mounting_width_mm"],
                    "air_chambers": row["air_chambers"],
                    "thermal_protection": float(row["thermal_protection"])
                    if row["thermal_protection"] is not None
                    else None,
                    "glass_unit_thickness_mm": row["glass_unit_thickness_mm"],
                    "profile_class": row["profile_class"],
                },
                source_url=row["source_url"],
            )
            for row in rows
        ]

    def _profile_clauses(self, query: ProductSearchQuery) -> tuple[list[str], dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if query.profile_names:
            clauses.append(
                "EXISTS (SELECT 1 FROM unnest(%(profile_names)s::text[]) AS n "
                "WHERE strpos(lower(name), n) > 0)"
            )
            params["profile_names"] = [name.lower() for name in query.profile_names]
        if query.min_thermal_protection is not None:
            clauses.append("thermal_protection >= %(min_thermal_protection)s")
            params["min_thermal_protection"] = query.min_thermal_protection
        if query.min_air_chambers is not None:
            clauses.append("%(min_air_chambers)s <= ALL (air_chambers)")
            params["min_air_chambers"] = query.min_air_chambers
        if query.min_mounting_width_mm is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM unnest(mounting_width_mm) AS w "
                "WHERE w >= %(min_mounting_width_mm)s)"
            )
            params["min_mounting_width_mm"] = query.min_mounting_width_mm
        if query.profile_class:
            clauses.append("upper(profile_class) = upper(%(profile_class)s)")
            params["profile_class"] = query.profile_class
        return clauses, params

    # ---------- стеклопакеты ----------

    def _search_glass_units(self, query: ProductSearchQuery) -> list[ProductHit]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": query.limit}
        if query.glass_unit_names:
            clauses.append(
                "EXISTS (SELECT 1 FROM unnest(%(glass_unit_names)s::text[]) AS n "
                "WHERE strpos(lower(name), n) > 0)"
            )
            params["glass_unit_names"] = [name.lower() for name in query.glass_unit_names]
        if query.glass_unit_chambers is not None:
            clauses.append("chambers_count = %(glass_unit_chambers)s")
            params["glass_unit_chambers"] = query.glass_unit_chambers
        if query.is_heat_saving is not None:
            clauses.append("is_heat_saving = %(is_heat_saving)s")
            params["is_heat_saving"] = query.is_heat_saving

        rows = self._fetch(
            f"""
            SELECT id, name, chambers_count, is_heat_saving, description, source_url
            FROM glass_units
            {self._where(clauses)}
            ORDER BY is_heat_saving DESC, chambers_count DESC NULLS LAST, name
            LIMIT %(limit)s
            """,
            params,
        )
        return [
            ProductHit(
                product_type=ProductType.GLASS_UNIT,
                product_id=row["id"],
                name=row["name"],
                characteristics={
                    "chambers_count": row["chambers_count"],
                    "is_heat_saving": row["is_heat_saving"],
                    "description": row["description"],
                },
                source_url=row["source_url"],
            )
            for row in rows
        ]

    # ---------- ламинация ----------

    def _search_laminations(self, query: ProductSearchQuery) -> list[ProductHit]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": query.limit}
        if query.lamination_names:
            clauses.append(
                "EXISTS (SELECT 1 FROM unnest(%(lamination_names)s::text[]) AS n "
                "WHERE strpos(lower(name), n) > 0)"
            )
            params["lamination_names"] = [name.lower() for name in query.lamination_names]
        if query.lamination_coating_type is not None:
            clauses.append("coating_type = %(coating_type)s")
            params["coating_type"] = query.lamination_coating_type.value
        if query.profile_names:
            # Ограничиваем только подтверждённой совместимостью из профиля клиента.
            clauses.append(
                "EXISTS (SELECT 1 FROM profile_lamination_compatibility plc "
                "JOIN profiles p ON p.id = plc.profile_id "
                "WHERE plc.lamination_id = laminations.id "
                "AND EXISTS (SELECT 1 FROM unnest(%(profile_names)s::text[]) AS n "
                "WHERE strpos(lower(p.name), n) > 0))"
            )
            params["profile_names"] = [name.lower() for name in query.profile_names]

        rows = self._fetch(
            f"""
            SELECT id, name, coating_type, manufacturer, side_options, source_url
            FROM laminations
            {self._where(clauses)}
            ORDER BY coating_type, name
            LIMIT %(limit)s
            """,
            params,
        )
        return [
            ProductHit(
                product_type=ProductType.LAMINATION,
                product_id=row["id"],
                name=row["name"],
                characteristics={
                    "coating_type": row["coating_type"],
                    "manufacturer": row["manufacturer"],
                    "side_options": row["side_options"],
                },
                source_url=row["source_url"],
            )
            for row in rows
        ]

    # ---------- фурнитура ----------

    def _search_hardware(self, query: ProductSearchQuery) -> list[ProductHit]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": query.limit}
        if query.hardware_manufacturer:
            clauses.append("strpos(lower(manufacturer), %(manufacturer)s) > 0")
            params["manufacturer"] = query.hardware_manufacturer.lower()
        if query.hardware_model:
            clauses.append("strpos(lower(model), %(model)s) > 0")
            params["model"] = query.hardware_model.lower()

        rows = self._fetch(
            f"""
            SELECT id, manufacturer, model, description, source_url
            FROM hardware
            {self._where(clauses)}
            ORDER BY manufacturer, model NULLS FIRST
            LIMIT %(limit)s
            """,
            params,
        )
        return [
            ProductHit(
                product_type=ProductType.HARDWARE,
                product_id=row["id"],
                name=f"{row['manufacturer']} {row['model']}".strip(),
                characteristics={
                    "manufacturer": row["manufacturer"],
                    "model": row["model"],
                    "description": row["description"],
                },
                source_url=row["source_url"],
            )
            for row in rows
        ]

    # ---------- служебное ----------

    def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    @staticmethod
    def _where(clauses: list[str]) -> str:
        return f"WHERE {' AND '.join(clauses)}" if clauses else ""


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    """Каталог создаётся один раз на процесс: одно подключение к PostgreSQL."""
    return Catalog(get_settings())

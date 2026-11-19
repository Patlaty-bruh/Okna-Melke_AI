# Melke catalog database — MVP data model

## 1. Purpose

This document describes the initial PostgreSQL catalog for the Melke window-selection agent.

The database stores structured product data. The agent uses it to search for compatible components, compare options and explain recommendations.

The database is not a replacement for the company's technical documentation or manager approval. If compatibility or a custom configuration is not confirmed, the agent must not promise that it can be manufactured.

## 2. Main entities

A window configuration (`windows`) references the components selected for it:

- `profiles` — PVC profile systems;
- `glass_units` — glass-unit types;
- `laminations` — decorative laminations and Cool Colours coatings;
- `hardware` — hardware manufacturers and models.

A window may also have dimensions, shape, number of sashes and opening configuration. These values may remain unknown until a measurement visit.

## 3. Profiles

Table: `profiles`

| Field | Type | Meaning |
|---|---|---|
| `id` | BIGINT | Primary key |
| `name` | VARCHAR | Profile system name |
| `mounting_width_mm` | INTEGER[] | Published mounting-width values |
| `air_chambers` | INTEGER[] | Number of air chambers in order: frame / sash / mullion |
| `thermal_protection` | NUMERIC(5,2) | Melke's published thermal-protection value |
| `glass_unit_thickness_mm` | INTEGER[] | Published supported glass-unit thickness values, in mm |
| `profile_class` | VARCHAR | Published profile class |
| `source_url` | TEXT | Source page |

### Seeded profile systems

| Profile | Mounting width, mm | Air chambers (frame/sash/mullion) | Thermal protection | Glass unit, mm | Class |
|---|---:|---:|---:|---:|---|
| Melke Lite 60 | 60 | 3/3/4 | 0.70 | 24, 32 | A |
| Melke Lite 70 | 70 | 4/4/4 | 0.76 | 40 | A |
| Melke Smart Ultra 65 | 65 | 4/4/4 | 0.92 | 28, 36 | A |
| Melke Evolution | 70 | 5/5/5 | 1.06 | 40 | A |
| Melke Wide | 80, 95 | 5/6/5 | 1.12 | 42 | A |
| Melke Centum | 80, 100 | 6/7/6 | 1.23 | 50 | A |

`air_chambers` is an array because Melke publishes the values for frame, sash and mullion separately. Do not flatten these three values into one integer.

`thermal_protection` is the value shown by Melke under the label «Теплозащита», in m²·°C/W. The old `sound_insulation` field is not part of this schema.

The `glass_unit_thickness_mm` values reproduce the profile-page field «Стеклопакет, мм». They are not glass-unit formulas and should not be interpreted as a complete compatibility matrix.

## 4. Glass units

Table: `glass_units`

| Field | Type | Meaning |
|---|---|---|
| `id` | BIGINT | Primary key |
| `name` | VARCHAR | Name shown to the user |
| `chambers_count` | SMALLINT, nullable | Number of chambers where explicitly known |
| `is_heat_saving` | BOOLEAN | Whether Melke describes the option as heat-saving |
| `description` | TEXT | Confirmed short description |
| `source_url` | TEXT | Source page |

### Confirmed records

| Name | Chambers | Heat-saving |
|---|---:|---|
| Однокамерный стеклопакет | 1 | No claim |
| Двухкамерный стеклопакет | 2 | No claim |
| Трёхкамерный стеклопакет | 3 | No claim |
| Climatherm | Not set universally | Yes |

Climatherm is described by Melke as a heat-saving glass unit. Its chamber count is left `NULL` because the calculator presents it as a separate option and the public information does not establish one universal formula for every configuration.

Unconfirmed fields have deliberately been omitted: gas filling, exact glass formula, sound-insulation value, solar protection, tinting, mirror finish, matte finish, triplex, tempered glass and other unverified properties. Add them only after receiving a current technical document or explicit confirmation from Melke.

## 5. Laminations and coatings

Table: `laminations`

| Field | Type | Meaning |
|---|---|---|
| `id` | BIGINT | Primary key |
| `name` | VARCHAR | Color/coating name |
| `coating_type` | VARCHAR | `lamination` or `cool_colours` |
| `manufacturer` | VARCHAR, nullable | Film/coating manufacturer or brand |
| `side_options` | TEXT[] | Side selection options: `inside`, `outside` |
| `source_url` | TEXT | Source page |

### Standard laminations confirmed in the Melke calculator

| Name | Type | Manufacturer |
|---|---|---|
| Золотой дуб | lamination | LG Hausys |
| Угольный серый | lamination | LG Hausys |
| Коричневый дуб | lamination | LG Hausys |
| Угольно-коричневый | lamination | LG Hausys |

### Cool Colours confirmed in Melke materials

| Name | Type |
|---|---|
| Onix | cool_colours |
| Графит | cool_colours |
| Табак | cool_colours |
| Сангрия | cool_colours |
| Off White | cool_colours |

The calculator lists Onix, Graphite, Tobacco and Sangria for Smart Ultra 65 and Evolution 70. Off White is listed in Smart Ultra product materials; the schema therefore seeds its compatibility only with Smart Ultra.

Standard lamination is listed by Melke for Centum, Wide, Evolution, Smart Ultra and Lite systems. The SQL seed links the listed standard laminations to these systems. The exact color, side and production availability should still be checked when preparing an individual configuration.

`profile_lamination_compatibility` stores the profile-to-coating relationship. Do not infer compatibility for other profiles or colors.

## 6. Hardware

Table: `hardware`

| Field | Type | Meaning |
|---|---|---|
| `id` | BIGINT | Primary key |
| `manufacturer` | VARCHAR | Manufacturer |
| `model` | VARCHAR, nullable | Model/series, if confirmed |
| `description` | TEXT | Confirmed information |
| `source_url` | TEXT | Source page |

### Confirmed manufacturers and models

| Manufacturer | Model | Confirmed information |
|---|---|---|
| FUTURUSS | Not specified | Used in Melke window configurations |
| Siegenia | Classic | For rectangular turn and tilt-and-turn sashes without additional functions |
| Siegenia | Titan AF | Anti-burglary protection and additional ventilation options are mentioned |
| VORNE | Not specified | Mentioned by Melke as a hardware manufacturer |
| Roto | Not specified | Mentioned by Melke as a hardware manufacturer |

The model is nullable because a specific model was not confirmed for every manufacturer.

`profile_hardware_compatibility` is created but intentionally has no seed rows. Public Melke pages do not provide a sufficiently consistent and complete mapping for every profile system. Populate it only from current technical cards or a confirmed company specification.

## 7. Window configurations

Table: `windows`

| Field | Type | Meaning |
|---|---|---|
| `id` | BIGINT | Primary key |
| `name` | VARCHAR, nullable | Configuration name |
| `profile_id` | BIGINT | Required profile reference |
| `glass_unit_id` | BIGINT, nullable | Glass-unit reference |
| `lamination_id` | BIGINT, nullable | Lamination/coating reference |
| `hardware_id` | BIGINT, nullable | Hardware reference |
| `width_mm` | INTEGER, nullable | Width |
| `height_mm` | INTEGER, nullable | Height |
| `shape` | VARCHAR, nullable | Window shape |
| `number_of_leaves` | SMALLINT, nullable | Number of sashes |
| `opening_configuration` | TEXT, nullable | Opening configuration |
| `is_active` | BOOLEAN | Whether the configuration is active |
| `source_url` | TEXT, nullable | Source page |

The table supports saved standard configurations and manually confirmed configurations. The initial SQL does not invent ready-made windows or prices.

An individually designed window may not have final dimensions or a fully confirmed component set before the measuring visit. Keep unknown values `NULL`; do not invent them.

## 8. Compatibility tables

Three many-to-many tables are created:

- `profile_glass_compatibility`;
- `profile_lamination_compatibility`;
- `profile_hardware_compatibility`.

A row means that the combination has been confirmed by a reliable source. A profile's maximum glass-unit thickness alone is not enough to claim that every glass-unit type is compatible.

The initial SQL populates only the lamination compatibility that is explicitly listed in Melke materials. Glass-unit and hardware compatibility remain empty until confirmed.

## 9. Search behavior for the agent

Recommended product-search flow:

1. LLM extracts the customer's preferences into a validated search-criteria object.
2. Python validates values and normalizes names/aliases.
3. Python queries active profiles and compatible components using parameterized SQL.
4. Python filters hard constraints and ranks remaining options according to soft preferences.
5. LLM explains the results using only the data returned by the database and the customer's stated priorities.
6. If no confirmed compatible configuration is found, the agent says that a manager must verify it instead of promising availability.

The LLM must not generate or execute arbitrary SQL.

## 10. Important data rules

- `NULL` means that the value is unknown or not confirmed. It does not mean `false`.
- Do not fill missing technical values using typical values from the window industry.
- Do not interpret a profile's maximum glass-unit thickness as a guaranteed compatibility record.
- Do not state that a hardware model or coating is available with a profile unless the compatibility is confirmed.
- Prices are not seeded because the website calculator gives an indicative price and the final amount is confirmed after measurement.
- Keep source URLs with records so that facts can be checked and refreshed.

## 11. Sources

The initial records are based on public Melke pages:

- Profile comparison and technical values: https://www.melke.ru/blog/kak-vybrat-profil-dlia-plastikovyx-okon-20230228/
- Profile catalogue: https://partnership.melke.ru/profile/
- Lite 60: https://www.melke.ru/okna/lite60/
- Lite 70: https://www.melke.ru/okna/lite70/
- Smart Ultra: https://partnership.melke.ru/profile/smart-ultra/
- Evolution: https://partnership.melke.ru/profile/evolution/
- Wide: https://partnership.melke.ru/profile/wide/
- Centum: https://partnership.melke.ru/profile/centum/
- Melke calculator: https://www.melke.ru/wincalc/
- Melke prices/calculator: https://www.melke.ru/ceny/
- Lamination and Cool Colours: https://www.melke.ru/okna/
- Hardware information: https://www.melke.ru/blog/vaznoe-ob-oknax-pocemu-furnitura-uplotnitel-i-zagluska-paza-stapika-dolzny-byt-kacestvennymi-20230228/
- Ready-made windows and hardware: https://www.melke.ru/okna/gotovye/

This is an MVP starter catalog, not a complete technical specification or a guarantee of current manufacturing availability.

# FAQ-датасет для NLU

Заготовленные вопросы и ответы по материалам базы знаний Melke (`knowledge_base/`).
Используется как справочник для распознавания типовых вопросов клиента и как источник
готовых формулировок ответа.

## Формат файла

Один файл — одна категория:

```json
{
  "category": "warranty",
  "title": "Гарантия, сервис и возврат",
  "source_doc": "knowledge_base/warranty.md",
  "entries": [
    {
      "id": "warranty-01",
      "question": "Какая гарантия на окна Melke?",
      "variants": ["какая гарантия", "сколько лет гарантия на окна"],
      "answer": "…",
      "source_url": "https://www.melke.ru/services/garantiya-i-vozvrat/"
    }
  ]
}
```

Поля записи:

- `id` — уникальный идентификатор (`<категория>-<номер>`), используется в логах и метриках;
- `question` — канонический вопрос;
- `variants` — типичные формулировки клиента (для сопоставления NLU);
- `answer` — готовый ответ, Markdown; только факты из базы знаний;
- `source_url` — страница сайта, откуда взят факт (для ссылки в ответе и проверки).

## Категории

| Файл | Категория | Записей | Источник |
|---|---|---|---|
| `company.json` | company | 14 | `knowledge_base/company.md` |
| `products.json` | products | 28 | `knowledge_base/products_overview.md` |
| `technology.json` | technology | 13 | `knowledge_base/production_and_technology.md` |
| `warranty.json` | warranty | 16 | `knowledge_base/warranty.md` |
| `installation.json` | installation | 16 | `knowledge_base/installation.md` |
| `maintenance.json` | maintenance | 25 | `knowledge_base/maintenance.md` |
| `ordering.json` | ordering | 15 | `knowledge_base/ordering.md` |
| `delivery.json` | delivery | 10 | `knowledge_base/delivery.md` |
| `documents.json` | documents | 9 | `knowledge_base/official_documents.md` |
| `balcony.json` | balcony | 12 | `knowledge_base/faq.md` |

Всего 158 записей.

## Правила ведения

- Ответ формулируется только по фактам из `knowledge_base/`. Если данных нет — так и
  указывается («в открытых материалах не опубликовано, уточняйте у менеджера»).
- Цены и сроки акций в датасет не переносятся: они меняются и подтверждаются после замера.
- Там, где источники противоречат друг другу (гарантийные сроки, «зимний режим»,
  температурный порог тёплого монтажа, число цветов уплотнителя), ответ приводит
  диапазон и рекомендует уточнить условия у менеджера или в договоре.
- Новый вопрос добавляется в файл своей категории с очередным `id` и 2–4 вариантами
  формулировки; после правки `questions`/`variants` стоит убедиться, что `source_url`
  действительно подтверждает ответ.

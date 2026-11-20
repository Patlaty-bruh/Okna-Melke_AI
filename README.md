# Frontend — экран приветствия и чат AI-помощника Melke

Статический интерфейс без сборки: `index.html`, `styles.css`, `app.js`.
Оформление повторяет стиль melke.ru (тёмный `#00171F`, бордо `#87374C`, зелёный `#A7CC70`, шрифт Clear Sans).

## Экраны

1. Приветствие: заголовок и две кнопки — «Перейти на сайт» (https://www.melke.ru) и «AI-помощник».
2. Чат: открывается по кнопке «AI-помощник» с плавным появлением; возврат — стрелка в шапке или `Esc`.

## Запросы к агенту

Чат отправляет `POST {chatApiUrl}` с телом `{"conversation_id": "...", "message": "..."}`
и ожидает ответ по контракту из `ARCHITECTURE.md` (§11): `{"message": "...", "status": "success|no_results|error", "actions": [], "handoff": {...}}`.

Адрес API задаётся в `index.html`:

```html
window.MELKE_CONFIG = { chatApiUrl: "/api/chat" };
```

HTTP-эндпоинт агента пока не реализован, поэтому без backend в чате появится сообщение,
что помощник недоступен.

## Docker

```bash
docker build -t melke-frontend .
docker run --rm -p 8080:80 melke-frontend
```

Интерфейс будет доступен на http://localhost:8080.

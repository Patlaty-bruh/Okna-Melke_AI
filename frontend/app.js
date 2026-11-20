const config = window.MELKE_CONFIG || {};
const SESSIONS_API_URL = config.sessionsApiUrl || "/sessions";
const STREAM_API_URL = config.streamApiUrl || "/chat/stream";

// Сессию создаёт backend: по этому идентификатору он хранит контекст диалога.
let sessionId = null;

const welcomeScreen = document.getElementById("screen-welcome");
const chatScreen = document.getElementById("screen-chat");
const messages = document.getElementById("chat-messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendButton = form.querySelector(".chat__send");
const themeToggle = document.getElementById("theme-toggle");

// ---------- Тема чата ----------

const THEME_KEY = "melke-theme";

function applyTheme(theme) {
    const isDark = theme === "dark";
    document.documentElement.dataset.theme = isDark ? "dark" : "light";
    themeToggle.setAttribute("aria-pressed", String(isDark));
    themeToggle.setAttribute("aria-label", isDark ? "Включить светлую тему" : "Включить тёмную тему");
    try {
        localStorage.setItem(THEME_KEY, isDark ? "dark" : "light");
    } catch (error) {
        // В приватном режиме браузер может запретить localStorage: тема останется до перезагрузки.
        console.warn("Не удалось сохранить тему", error);
    }
}

function storedTheme() {
    try {
        return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
    } catch (error) {
        return "light";
    }
}

applyTheme(storedTheme());

themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

function showScreen(screen) {
    const active = screen === "chat" ? chatScreen : welcomeScreen;
    const inactive = screen === "chat" ? welcomeScreen : chatScreen;

    inactive.classList.remove("is-active");
    inactive.setAttribute("aria-hidden", "true");
    active.classList.add("is-active");
    active.setAttribute("aria-hidden", "false");

    if (screen === "chat") {
        input.focus();
    }
}

function addMessage(text, kind) {
    const message = document.createElement("div");
    message.className = `message message--${kind}`;
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    message.append(paragraph);
    messages.append(message);
    messages.scrollTop = messages.scrollHeight;
    return message;
}

function showTyping() {
    const typing = document.createElement("div");
    typing.className = "message message--typing";
    typing.innerHTML = "<span></span><span></span><span></span>";
    messages.append(typing);
    messages.scrollTop = messages.scrollHeight;
    return typing;
}

function setMessageText(message, text) {
    message.querySelector("p").textContent = text;
    messages.scrollTop = messages.scrollHeight;
}

async function ensureSession() {
    if (sessionId) {
        return sessionId;
    }
    const response = await fetch(SESSIONS_API_URL, { method: "POST" });
    if (!response.ok) {
        throw new Error(await errorMessage(response));
    }
    const data = await response.json();
    if (typeof data.session_id !== "string" || !data.session_id) {
        throw new Error("Backend не вернул идентификатор сессии");
    }
    sessionId = data.session_id;
    return sessionId;
}

async function errorMessage(response) {
    try {
        const data = await response.json();
        if (data && data.error && typeof data.error.message === "string") {
            return data.error.message;
        }
    } catch (error) {
        // Тело ответа не JSON: показываем общее сообщение.
    }
    return `API вернул статус ${response.status}`;
}

// Читает поток событий SSE: delta — часть ответа, done — итог, error — ошибка.
async function streamAnswer(text, onDelta) {
    const id = await ensureSession();
    const response = await fetch(STREAM_API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: id, message: text }),
    });
    if (!response.ok) {
        throw new Error(await errorMessage(response));
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result = null;

    while (true) {
        const { value, done } = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        let end;
        while ((end = buffer.indexOf("\n\n")) !== -1) {
            const event = parseEvent(buffer.slice(0, end));
            buffer = buffer.slice(end + 2);
            if (!event) {
                continue;
            }
            if (event.name === "delta" && event.data.text) {
                onDelta(event.data.text);
            } else if (event.name === "done") {
                result = event.data;
            } else if (event.name === "error") {
                throw new Error(event.data.message || "AI-помощник не смог ответить");
            }
        }
    }

    if (!result) {
        throw new Error("Backend не вернул итоговый ответ");
    }
    return result;
}

function parseEvent(raw) {
    let name = "message";
    const dataLines = [];
    for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) {
            name = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trim());
        }
    }
    if (!dataLines.length) {
        return null;
    }
    try {
        return { name, data: JSON.parse(dataLines.join("\n")) };
    } catch (error) {
        return null;
    }
}

async function sendMessage(text) {
    addMessage(text, "user");
    const typing = showTyping();
    input.value = "";
    input.disabled = true;
    sendButton.disabled = true;

    let bubble = null;
    try {
        const data = await streamAnswer(text, (delta) => {
            if (!bubble) {
                typing.remove();
                bubble = addMessage("", "agent");
            }
            setMessageText(bubble, bubble.querySelector("p").textContent + delta);
        });
        typing.remove();
        const answer = typeof data.message === "string" ? data.message.trim() : "";
        const kind = data.status === "error" ? "error" : "agent";
        if (answer && bubble) {
            bubble.className = `message message--${kind}`;
            setMessageText(bubble, answer);
        } else if (answer) {
            addMessage(answer, kind);
        } else if (!bubble) {
            addMessage("AI-помощник не смог подготовить ответ. Попробуйте переформулировать вопрос.", "error");
        }
    } catch (error) {
        typing.remove();
        console.error(error);
        addMessage(
            "AI-помощник сейчас недоступен. Попробуйте позже или позвоните нам: 8 (495) 661 90 01.",
            "error"
        );
    } finally {
        input.disabled = false;
        sendButton.disabled = false;
        input.focus();
    }
}

document.getElementById("open-chat").addEventListener("click", () => showScreen("chat"));
document.getElementById("close-chat").addEventListener("click", () => showScreen("welcome"));

form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || input.disabled) {
        return;
    }
    sendMessage(text);
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && chatScreen.classList.contains("is-active")) {
        showScreen("welcome");
    }
});

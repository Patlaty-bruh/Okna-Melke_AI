const config = window.MELKE_CONFIG || {};
const CHAT_API_URL = config.chatApiUrl || "/api/chat";
// Идентификатор диалога: backend должен возвращать историю по этому же идентификатору.
const conversationId = window.crypto && crypto.randomUUID
    ? crypto.randomUUID()
    : String(Date.now());

const welcomeScreen = document.getElementById("screen-welcome");
const chatScreen = document.getElementById("screen-chat");
const messages = document.getElementById("chat-messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendButton = form.querySelector(".chat__send");

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

async function requestAnswer(text) {
    const response = await fetch(CHAT_API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId, message: text }),
    });
    if (!response.ok) {
        throw new Error(`API вернул статус ${response.status}`);
    }
    return response.json();
}

async function sendMessage(text) {
    addMessage(text, "user");
    const typing = showTyping();
    input.value = "";
    input.disabled = true;
    sendButton.disabled = true;

    try {
        const data = await requestAnswer(text);
        typing.remove();
        const answer = typeof data.message === "string" ? data.message.trim() : "";
        if (answer) {
            addMessage(answer, data.status === "error" ? "error" : "agent");
        } else {
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

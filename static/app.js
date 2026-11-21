const form = document.getElementById("chatForm");
const input = document.getElementById("query");
const messages = document.getElementById("messages");
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const sendButton = document.getElementById("sendButton");
const newChat = document.getElementById("newChat");
const uploadButton = document.getElementById("uploadButton");
const documentFiles = document.getElementById("documentFiles");
const logoutButton = document.getElementById("logoutButton");
let activeConversationId = null;

function scrollMessagesToBottom() {
    requestAnimationFrame(() => {
        messages.scrollTo({
            top: messages.scrollHeight,
            behavior: "smooth"
        });
    });
}

function escapeHtml(value) {
    const element = document.createElement("div");
    element.textContent = value;
    return element.innerHTML;
}

function removeWelcome() {
    const welcome = messages.querySelector(".welcome");
    if (welcome) welcome.remove();
}

function addMessage(text, role) {
    removeWelcome();
    const row = document.createElement("div");
    row.className = `message-row ${role}`;
    row.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
    messages.appendChild(row);
    scrollMessagesToBottom();
    return row;
}

function formatInlineText(value) {
    let text = String(value).trim();
    text = text.replace(/<br\s*\/?>/gi, "\n");

    const escaped = escapeHtml(text);
    return escaped
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/(?<!\*)\*([^*]+?)\*(?!\*)/g, "<em>$1</em>")
        .replace(/&lt;br\s*\/?>/gi, "<br>");
}

function cleanPipeArtifacts(line) {
    let cleaned = line.trim();
    if (/^\|?[-:|\s]+\|?$/.test(cleaned) && cleaned.includes("-")) {
        return "";
    }
    cleaned = cleaned.replace(/^\s*\|\s*/, "").replace(/\s*\|\s*$/, "");
    return cleaned.replace(/\s*\|\s*/g, " · ").trim();
}

function formatAssistantText(value) {
    const normalized = String(value)
        .replace(/\r/g, "")
        .replace(/<br\s*\/?>/gi, "\n")
        .replace(/\[\s*([\w -]+)\s*\]/g, "$1")
        .replace(/\n{3,}/g, "\n\n");

    const lines = normalized.split("\n");
    const html = [];
    let listItems = [];

    const flushList = () => {
        if (!listItems.length) return;
        html.push(`<ul class="answer-list">${listItems.map((item) => `<li>${formatInlineText(item)}</li>`).join("")}</ul>`);
        listItems = [];
    };

    for (let index = 0; index < lines.length; index++) {
        let line = lines[index].trim();
        if (!line) {
            flushList();
            continue;
        }

        if (line.startsWith("|") && index + 1 < lines.length) {
            const nextLine = lines[index + 1].trim();
            if (/^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(nextLine)) {
                const headers = line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
                html.push('<div class="answer-table-wrap"><table class="answer-table"><thead><tr>');
                html.push(headers.map((header) => `<th>${formatInlineText(header)}</th>`).join(""));
                html.push("</tr></thead><tbody>");
                index += 2;
                while (index < lines.length) {
                    const row = lines[index].trim();
                    if (!row.startsWith("|")) break;
                    if (/^\|?[-:|\s]+\|?$/.test(row)) {
                        index++;
                        continue;
                    }
                    const cells = row.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
                    html.push("<tr>");
                    html.push(cells.map((cell) => `<td>${formatInlineText(cell)}</td>`).join(""));
                    html.push("</tr>");
                    index++;
                }
                html.push("</tbody></table></div>");
                index--;
                continue;
            }
        }

        const separator = cleanPipeArtifacts(line);
        if (!separator) {
            flushList();
            continue;
        }

        const heading = separator.match(/^#{1,3}\s+(.+)$/);
        if (heading) {
            flushList();
            html.push(`<h3>${formatInlineText(heading[1])}</h3>`);
            continue;
        }

        const boldHeading = separator.match(/^\*\*(.+?):\*\*\s*(.*)$/);
        if (boldHeading) {
            flushList();
            html.push(`<h3>${formatInlineText(boldHeading[1])}</h3>`);
            if (boldHeading[2]) html.push(`<p>${formatInlineText(boldHeading[2])}</p>`);
            continue;
        }

        const bullet = separator.match(/^[-*•]\s+(.+)$/);
        if (bullet) {
            listItems.push(bullet[1]);
            continue;
        }

        const numbered = separator.match(/^\d+[.)]\s+(.+)$/);
        if (numbered) {
            flushList();
            html.push(`<p class="numbered-item"><strong>${numbered[1] ? String(lines[index].match(/^\d+[.)]/)?.[0] || "") : ""}</strong> ${formatInlineText(numbered[1])}</p>`);
            continue;
        }

        flushList();
        html.push(`<p>${formatInlineText(separator)}</p>`);
    }

    flushList();
    return html.join("");
}

async function sendQuery(query) {
    const text = query.trim();
    if (!text) return;

    addMessage(text, "user");
    input.value = "";
    input.style.height = "auto";
    sendButton.disabled = true;
    const typing = addMessage("Thinking...", "assistant");
    typing.querySelector(".bubble").classList.add("typing");

    try {
        const response = await fetch("/api/search", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken
            },
            body: JSON.stringify({ query: text, conversation_id: activeConversationId })
        });
        const data = await response.json();

        if (!response.ok) throw new Error(data.error || "Request failed.");
        activeConversationId = data.conversation_id || activeConversationId;
        typing.querySelector(".bubble").innerHTML = formatAssistantText(data.answer);
        typing.querySelector(".bubble").classList.remove("typing");
    } catch (error) {
        typing.querySelector(".bubble").textContent = `Error: ${error.message}`;
        typing.querySelector(".bubble").classList.remove("typing");
    } finally {
        sendButton.disabled = false;
        input.focus();
        scrollMessagesToBottom();
    }
}

async function initializeFullStackUi() {
    try {
        const meResponse = await fetch("/api/me");
        if (meResponse.ok) {
            const me = await meResponse.json();
            document.getElementById("userName").textContent = me.name;
            document.getElementById("userAvatar").textContent = me.name
                .split(/\s+/)
                .map((part) => part[0])
                .join("")
                .slice(0, 2)
                .toUpperCase();
        }

        const conversationsResponse = await fetch("/api/conversations");
        if (conversationsResponse.ok) {
            const data = await conversationsResponse.json();
            if (data.conversations.length) {
                activeConversationId = data.conversations[0].id;
            }
        }
    } catch (error) {
        console.error(error);
    }
}

uploadButton.addEventListener("click", () => documentFiles.click());
documentFiles.addEventListener("change", async () => {
    if (!documentFiles.files.length) return;
    const formData = new FormData();
    for (const file of documentFiles.files) formData.append("files", file);

    uploadButton.disabled = true;
    uploadButton.textContent = "Uploading...";
    try {
        const response = await fetch("/api/upload", {
            method: "POST",
            headers: {"X-CSRF-Token": csrfToken},
            body: formData
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Upload failed.");
        uploadButton.textContent = data.saved.length ? `Added ${data.saved.length} document(s)` : "No valid documents";
    } catch (error) {
        uploadButton.textContent = error.message;
    } finally {
        documentFiles.value = "";
        setTimeout(() => { uploadButton.disabled = false; uploadButton.textContent = "＋ Add documents"; }, 1800);
    }
});

logoutButton.addEventListener("click", async () => {
    await fetch("/api/logout", {
        method: "POST",
        headers: {"X-CSRF-Token": csrfToken}
    });
    window.location.href = "/login";
});

form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendQuery(input.value);
});

input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
});

input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
    }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => sendQuery(button.dataset.prompt));
});

newChat.addEventListener("click", () => {
    messages.innerHTML = `
        <div class="welcome">
            <div class="welcome-mark">D</div>
            <h1>What do you want to know?</h1>
            <p>Ask questions across your connected documents.</p>
            <div class="suggestions">
                <button type="button" data-prompt="Summarize the main topics in my documents"><span>✦</span><span><strong>Summarize documents</strong><small>Find the key ideas</small></span></button>
                <button type="button" data-prompt="What are the important details I should know?"><span>⌕</span><span><strong>Discover insights</strong><small>Explore important details</small></span></button>
                <button type="button" data-prompt="Compare the information across my documents"><span>⇄</span><span><strong>Compare sources</strong><small>Find similarities and differences</small></span></button>
            </div>
        </div>`;

    document.querySelectorAll("[data-prompt]").forEach((button) => {
        button.addEventListener("click", () => sendQuery(button.dataset.prompt));
    });
});

initializeFullStackUi();

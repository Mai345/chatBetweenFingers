const $ = (selector) => document.querySelector(selector);
const state = {
    user: null,
    settings: {},
    socket: null,
    sessionId: null,
    stream: null,
    page: 1,
    authMode: "login",
    draftTimer: null,
    stopping: false,
    modelReady: false,
    modelAvailable: false,
    startingSession: false,
    awaitingFrame: false,
    frameTimer: null,
};
const notice = (message, error = false) => {
    const el = $("#notice");
    el.textContent = message;
    el.classList.toggle("error", error);
    el.hidden = false;
    clearTimeout(notice.timer);
    notice.timer = setTimeout(() => {
        el.hidden = true;
    }, 5000);
};

async function api(path, options = {}) {
    const response = await fetch(path, {
        credentials: "same-origin",
        ...options,
        headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...options.headers,
        },
    });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok)
        throw new Error(body.error?.message || `请求失败 (${response.status})`);
    return body.data;
}

function renderAuth() {
    const register = state.authMode === "register";
    document
        .querySelectorAll("[data-auth]")
        .forEach((button) =>
            button.classList.toggle(
                "active",
                button.dataset.auth === state.authMode,
            ),
        );
    $("#display-name-field").hidden = !register;
    $("#auth-form").elements.display_name.required = register;
    $("#auth-form").elements.password.autocomplete = register
        ? "new-password"
        : "current-password";
    $("#auth-title").textContent = register ? "创建账号" : "登录";
    $("#auth-submit").textContent = register ? "创建账号并登录" : "登录工作台";
    $("#auth-error").textContent = "";
}
document.querySelectorAll("[data-auth]").forEach((button) =>
    button.addEventListener("click", () => {
        state.authMode = button.dataset.auth;
        renderAuth();
    }),
);
$("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    if (state.authMode === "login") delete data.display_name;
    $("#auth-submit").disabled = true;
    try {
        state.user = await api(`/api/v1/auth/${state.authMode}`, {
            method: "POST",
            body: JSON.stringify(data),
        });
        form.reset();
        await enterWorkspace();
    } catch (error) {
        $("#auth-error").textContent = error.message;
    } finally {
        $("#auth-submit").disabled = false;
    }
});

async function enterWorkspace() {
    $("#auth").hidden = false;
    $("#workspace").hidden = true;
    if (!state.user) {
        $("#user-label").textContent = "未登录";
        $("#logout").hidden = true;
        renderAuth();
        return;
    }
    $("#auth").hidden = true;
    $("#workspace").hidden = false;
    $("#user-label").textContent = state.user.display_name;
    $("#logout").hidden = false;
    try {
        state.settings = await api("/api/v1/settings");
        applySettings();
    } catch (error) {
        notice(error.message, true);
    }
    await navigate();
}
$("#logout").addEventListener("click", async () => {
    try {
        await api("/api/v1/auth/logout", { method: "POST" });
    } catch (error) {
        notice(error.message, true);
        return;
    }
    stopCamera();
    resetSession();
    state.user = null;
    setModelAvailability(false);
    $("#draft").value = "";
    updateCount();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    await enterWorkspace();
});

async function navigate() {
    const name = location.hash.slice(1) || "recognition";
    const page = ["recognition", "gestures", "history", "settings"].includes(
        name,
    )
        ? name
        : "recognition";
    document.querySelectorAll(".page").forEach((el) => {
        el.hidden = el.id !== page;
    });
    document.querySelectorAll("[data-page]").forEach((el) => {
        el.classList.toggle("active", el.dataset.page === page);
        if (el.dataset.page === page) el.setAttribute("aria-current", "page");
        else el.removeAttribute("aria-current");
    });
    if (!state.user) return;
    try {
        if (page === "gestures") {
            if (typeof refreshTraining === "function") await refreshTraining();
            else await loadGestures();
        }
        if (page === "recognition") await refreshModelStatus();
        if (page === "history") await loadHistory();
        if (page === "settings") fillSettings();
    } catch (error) {
        notice(error.message, true);
    }
}
window.addEventListener("hashchange", navigate);

async function loadGestures() {
    const items = await api(
        `/api/v1/gestures?keyword=${encodeURIComponent($("#gesture-search").value)}`,
    );
    const list = $("#gesture-list");
    list.replaceChildren();
    if (!items.length) {
        const p = document.createElement("p");
        p.className = "muted";
        p.textContent = "没有找到匹配的词条。";
        list.append(p);
        return;
    }
    for (const item of items) {
        const card = document.createElement("article");
        card.className = "gesture-item";
        const meta = document.createElement("small");
        meta.textContent =
            item.category === "custom" ? "自定义手势" : "示例手势";
        const title = document.createElement("h2");
        title.textContent = item.display_name;
        const description = document.createElement("p");
        description.textContent = item.description;
        const tag = document.createElement("small");
        tag.textContent =
            item.category === "custom"
                ? `已采集 ${item.sample_count} 张`
                : "内置示例";
        card.append(meta, title, description, tag);
        list.append(card);
    }
}
$("#gesture-search").addEventListener("input", () => {
    clearTimeout(loadGestures.timer);
    loadGestures.timer = setTimeout(
        () => loadGestures().catch((e) => notice(e.message, true)),
        200,
    );
});

async function loadHistory() {
    const entries = await api(
        `/api/v1/sessions?page=${state.page}&page_size=10`,
    );
    const list = $("#history-list");
    list.replaceChildren();
    $("#history-page").textContent = `第 ${state.page} 页`;
    $("#history-prev").disabled = state.page <= 1;
    $("#history-next").disabled = entries.length < 10;
    if (!entries.length) {
        const empty = document.createElement("div");
        empty.className = "panel muted";
        empty.textContent = "暂无历史记录";
        list.append(empty);
        return;
    }
    for (const entry of entries) {
        const card = document.createElement("article");
        card.className = "history-item";
        const text = document.createElement("div");
        const title = document.createElement("h2");
        title.className = "history-text";
        title.textContent = entry.draft_text;
        const time = document.createElement("p");
        time.textContent = new Date(entry.started_at).toLocaleString("zh-CN");
        text.append(title, time);
        const del = document.createElement("button");
        del.className = "button subtle";
        del.textContent = "删除";
        del.addEventListener("click", async () => {
            if (!confirm("确定删除这条历史记录吗？")) return;
            try {
                await api(`/api/v1/sessions/${encodeURIComponent(entry.id)}`, {
                    method: "DELETE",
                });
                await loadHistory();
                notice("记录已删除");
            } catch (error) {
                notice(error.message, true);
            }
        });
        card.append(text, del);
        list.append(card);
    }
}
$("#refresh-history").addEventListener("click", () =>
    loadHistory().catch((e) => notice(e.message, true)),
);
$("#history-prev").addEventListener("click", () => {
    if (state.page > 1) {
        state.page--;
        loadHistory().catch((e) => notice(e.message, true));
    }
});
$("#history-next").addEventListener("click", () => {
    state.page++;
    loadHistory().catch((e) => notice(e.message, true));
});

function applySettings() {
    $("#camera-video").classList.toggle(
        "mirrored",
        !!state.settings.mirror_preview,
    );
}
function fillSettings() {
    const form = $("#settings-form");
    for (const [key, value] of Object.entries(state.settings)) {
        const input = form.elements[key];
        if (input) {
            if (input.type === "checkbox") input.checked = value;
            else input.value = value;
        }
    }
}
$("#settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = {};
    for (const key of [
        "confidence_threshold",
        "stable_duration_ms",
        "cooldown_ms",
        "audio_rate",
        "audio_volume",
    ])
        data[key] = Number(form.elements[key].value);
    for (const key of [
        "mirror_preview",
        "show_landmarks",
        "show_confidence",
        "audio_enabled",
    ])
        data[key] = form.elements[key].checked;
    try {
        state.settings = await api("/api/v1/settings", {
            method: "PATCH",
            body: JSON.stringify(data),
        });
        applySettings();
        notice("设置已保存");
    } catch (error) {
        notice(error.message, true);
    }
});

function updateCameraMessage() {
    if (!state.modelAvailable) {
        $("#camera-message").textContent = state.stream
            ? "预览中，模型未训练"
            : "";
    } else if (!state.stream) {
        $("#camera-message").textContent = "模型已接入";
    } else if (
        state.modelReady &&
        state.socket?.readyState === WebSocket.OPEN
    ) {
        $("#camera-message").textContent = "模型已接入，识别中";
    } else if (!state.socket && !state.startingSession) {
        $("#camera-message").textContent = "模型已接入，待开始会话";
    } else {
        $("#camera-message").textContent = "模型已接入，连接中";
    }
}

function setModelAvailability(available) {
    state.modelAvailable = available;
    $("#model-banner").hidden = available;
    updateCameraMessage();
}

async function refreshModelStatus() {
    if (!state.user) return;
    const userId = state.user.id;
    const data = await api("/api/v1/training/status");
    if (state.user?.id !== userId) return;
    setModelAvailability(!!data.model);
}

function stopCamera() {
    clearTimeout(state.frameTimer);
    state.frameTimer = null;
    state.awaitingFrame = false;
    if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
    $("#camera-video").srcObject = null;
    $("#camera-empty").hidden = false;
    $("#camera-status").textContent = "未开启";
    $("#start-camera").disabled = false;
    $("#stop-camera").disabled = true;
    updateCameraMessage();
}
async function startCamera(deviceId = "") {
    if (!navigator.mediaDevices?.getUserMedia) {
        notice("当前浏览器无法使用摄像头，请通过 localhost 访问。", true);
        return;
    }
    stopCamera();
    $("#start-camera").disabled = true;
    try {
        state.stream = await navigator.mediaDevices.getUserMedia({
            video: deviceId
                ? { deviceId: { exact: deviceId } }
                : { width: { ideal: 640 }, height: { ideal: 480 } },
            audio: false,
        });
        $("#camera-video").srcObject = state.stream;
        await $("#camera-video").play();
        $("#camera-empty").hidden = true;
        $("#camera-status").textContent = "预览中";
        $("#stop-camera").disabled = false;
        await refreshModelStatus();
        if (state.modelAvailable) await startSession();
        updateCameraMessage();
        scheduleFrame();
        const devices = await navigator.mediaDevices.enumerateDevices();
        const selected = state.stream
            .getVideoTracks()[0]
            .getSettings().deviceId;
        const picker = $("#camera-select");
        picker.replaceChildren();
        devices
            .filter((d) => d.kind === "videoinput")
            .forEach((device, i) => {
                const option = document.createElement("option");
                option.value = device.deviceId;
                option.textContent = device.label || `摄像头 ${i + 1}`;
                option.selected = device.deviceId === selected;
                picker.append(option);
            });
    } catch (error) {
        stopCamera();
        notice(`无法开启摄像头：${error.message}`, true);
    }
}
$("#start-camera").addEventListener("click", () =>
    startCamera($("#camera-select").value),
);
$("#stop-camera").addEventListener("click", stopCamera);
$("#camera-select").addEventListener("change", (event) => {
    if (state.stream) startCamera(event.target.value);
});

function updateCount() {
    $("#draft-count").textContent = `${$("#draft").value.length} / 10000`;
}
function sendDraft() {
    clearTimeout(state.draftTimer);
    if (state.socket?.readyState === WebSocket.OPEN) {
        state.socket.send(
            JSON.stringify({
                type: "sentence_command",
                action: "replace_text",
                text: $("#draft").value,
            }),
        );
        $("#draft-status").textContent = "正在保存";
    }
}
$("#draft").addEventListener("input", () => {
    updateCount();
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(sendDraft, 300);
});
$("#clear").addEventListener("click", () => {
    $("#draft").value = "";
    updateCount();
    if (state.socket?.readyState === WebSocket.OPEN)
        state.socket.send(
            JSON.stringify({ type: "sentence_command", action: "clear" }),
        );
});
$("#undo").addEventListener("click", () => {
    if (state.socket?.readyState === WebSocket.OPEN) {
        sendDraft();
        state.socket.send(
            JSON.stringify({ type: "sentence_command", action: "undo_last" }),
        );
    } else notice("请先开始会话。");
});
$("#speak").addEventListener("click", () => {
    const text = $("#draft").value.trim();
    if (!text) {
        notice("请先输入要朗读的文字。");
        return;
    }
    if (!state.settings.audio_enabled) {
        notice("请先在设置中启用语音。");
        return;
    }
    if (!("speechSynthesis" in window)) {
        notice("当前浏览器不支持语音朗读。", true);
        return;
    }
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = state.settings.audio_rate;
    utterance.volume = state.settings.audio_volume;
    speechSynthesis.speak(utterance);
});
function resetSession() {
    clearTimeout(state.draftTimer);
    clearTimeout(state.frameTimer);
    state.frameTimer = null;
    state.awaitingFrame = false;
    state.modelReady = false;
    state.socket = null;
    state.sessionId = null;
    state.stopping = false;
    state.startingSession = false;
    $("#start-session").disabled = false;
    $("#finish-session").disabled = true;
    $("#cancel-session").disabled = true;
    $("#connection-status").textContent = "尚未开始";
    $("#draft-status").textContent = "未开始会话";
    updateCameraMessage();
}
function scheduleFrame() {
    if (
        !state.stream ||
        !state.modelReady ||
        state.awaitingFrame ||
        state.frameTimer ||
        state.socket?.readyState !== WebSocket.OPEN
    )
        return;
    state.frameTimer = setTimeout(sendFrame, 100);
}
function sendFrame() {
    state.frameTimer = null;
    if (
        !state.stream ||
        !state.modelReady ||
        state.socket?.readyState !== WebSocket.OPEN
    )
        return;
    const video = $("#camera-video");
    const canvas = $("#capture");
    const scale = Math.min(640 / video.videoWidth, 480 / video.videoHeight, 1);
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
        (blob) => {
            if (
                !blob ||
                !state.stream ||
                state.socket?.readyState !== WebSocket.OPEN
            )
                return;
            if (blob.size > 307200) {
                notice("图像过大，已跳过这一帧。", true);
                scheduleFrame();
                return;
            }
            state.awaitingFrame = true;
            state.socket.send(blob);
        },
        "image/jpeg",
        0.72,
    );
}
function renderHands(hands) {
    const canvas = $("#landmarks");
    const context = canvas.getContext("2d");
    const video = $("#camera-video");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.settings.show_landmarks) return;
    context.fillStyle = "#ffffff";
    context.strokeStyle = "#202020";
    context.lineWidth = 1.5;
    for (const hand of hands || [])
        for (const point of hand.landmarks || []) {
            context.beginPath();
            context.arc(
                (state.settings.mirror_preview ? 1 - point.x : point.x) *
                    canvas.width,
                point.y * canvas.height,
                4,
                0,
                Math.PI * 2,
            );
            context.fill();
            context.stroke();
        }
}
async function startSession() {
    if (state.startingSession) return;
    if (
        state.socket &&
        (state.socket.readyState === WebSocket.OPEN ||
            state.socket.readyState === WebSocket.CONNECTING)
    )
        return;
    state.startingSession = true;
    $("#start-session").disabled = true;
    try {
        const session = await api("/api/v1/sessions", {
            method: "POST",
            body: "{}",
        });
        state.sessionId = session.id;
        const scheme = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(
            `${scheme}://${location.host}/ws/v1/recognition?session_id=${encodeURIComponent(session.id)}`,
        );
        state.socket = ws;
        ws.addEventListener("message", (event) => {
            let message;
            try {
                message = JSON.parse(event.data);
            } catch {
                notice("服务器消息格式错误", true);
                return;
            }
            if (message.type === "connected") {
                state.modelReady = message.model_ready === true;
                setModelAvailability(state.modelReady);
                $("#connection-status").textContent = "会话已连接";
                $("#finish-session").disabled = false;
                $("#cancel-session").disabled = false;
                $("#draft-status").textContent = "会话已开始";
                updateCameraMessage();
                if ($("#draft").value) sendDraft();
                scheduleFrame();
            }
            if (message.type === "frame_result") {
                state.awaitingFrame = false;
                renderHands(message.hands);
                const score =
                    state.settings.show_confidence && message.candidate
                        ? ` · ${Math.round(message.candidate.confidence * 100)}%`
                        : "";
                $("#camera-status").textContent =
                    (message.candidate?.label || "等待手势") + score;
                scheduleFrame();
            }
            if (message.type === "sentence_updated") {
                if (message.action !== "replace_text") {
                    $("#draft").value = message.draft_text;
                    updateCount();
                }
                $("#draft-status").textContent = "已同步";
            }
            if (message.type === "gesture_committed") {
                $("#draft").value = message.draft_text;
                updateCount();
            }
            if (message.type === "error") {
                state.awaitingFrame = false;
                notice(message.message, true);
                if (message.recoverable) scheduleFrame();
            }
            if (message.type === "session_finished") {
                state.stopping = true;
                notice(message.cancelled ? "会话已取消" : "会话已结束");
            }
        });
        ws.addEventListener("close", async () => {
            const wasStopping = state.stopping;
            const sid = state.sessionId;
            resetSession();
            if (!wasStopping) {
                notice("连接已断开", true);
                if (sid) {
                    try {
                        const item = await api(
                            `/api/v1/sessions/${encodeURIComponent(sid)}`,
                        );
                        if (item.status === "created")
                            await api(
                                `/api/v1/sessions/${encodeURIComponent(sid)}`,
                                { method: "DELETE" },
                            );
                    } catch {}
                }
            }
        });
        ws.addEventListener("error", () => notice("无法连接会话服务。", true));
    } catch (error) {
        resetSession();
        notice(error.message, true);
    } finally {
        state.startingSession = false;
    }
}
$("#start-session").addEventListener("click", startSession);
$("#finish-session").addEventListener("click", () => {
    if (state.socket?.readyState === WebSocket.OPEN) {
        sendDraft();
        state.socket.send(JSON.stringify({ type: "stop" }));
        $("#finish-session").disabled = true;
        $("#cancel-session").disabled = true;
    }
});
$("#cancel-session").addEventListener("click", () => {
    if (
        state.socket?.readyState === WebSocket.OPEN &&
        confirm("取消会话将不会保存本次内容，确定继续吗？")
    ) {
        clearTimeout(state.draftTimer);
        state.socket.send(JSON.stringify({ type: "cancel" }));
        $("#finish-session").disabled = true;
        $("#cancel-session").disabled = true;
    }
});
window.addEventListener("pagehide", () => {
    stopCamera();
    if (state.socket?.readyState === WebSocket.OPEN) {
        sendDraft();
        state.socket.close(1000, "page closed");
    }
});
api("/api/v1/auth/me")
    .then((user) => {
        state.user = user;
        return enterWorkspace();
    })
    .catch(() => enterWorkspace());

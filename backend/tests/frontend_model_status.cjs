const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function createPage() {
    const elements = new Map();
    function element(selector) {
        if (!elements.has(selector)) {
            elements.set(selector, {
                textContent: "",
                value: "",
                disabled: false,
                hidden: false,
                videoWidth: 640,
                videoHeight: 480,
                classList: { toggle() {} },
                addEventListener() {},
                replaceChildren() {},
                play: async () => {},
            });
        }
        return elements.get(selector);
    }
    class Socket {
        static OPEN = 1;
        static CONNECTING = 0;
        constructor() {
            this.readyState = Socket.CONNECTING;
            this.handlers = {};
        }
        addEventListener(name, handler) { this.handlers[name] = handler; }
    }
    const stream = { getTracks: () => [], getVideoTracks: () => [{ getSettings: () => ({}) }] };
    const context = vm.createContext({
        document: { querySelector: element, querySelectorAll: () => [] },
        window: { addEventListener() {} },
        navigator: { mediaDevices: { getUserMedia: async () => stream, enumerateDevices: async () => [] } },
        location: { hash: "", protocol: "http:", host: "localhost" },
        WebSocket: Socket,
        fetch: () => new Promise(() => {}),
        setTimeout: () => 1,
        clearTimeout() {},
        console,
    });
    const source = fs.readFileSync(path.resolve(__dirname, "../../frontend/v1/app.js"), "utf8");
    vm.runInContext(source, context);
    vm.runInContext('state.user = { id: "user-one" }', context);
    return { context, element, evaluate: (source) => vm.runInContext(source, context) };
}

test("模型状态刷新后，不再显示模型未接入", async () => {
    const page = createPage();
    page.context.api = async () => ({ model: { version: "new-model" } });
    await page.context.refreshModelStatus();
    assert.equal(page.evaluate("state.modelAvailable"), true);
    assert.equal(page.element("#model-banner").hidden, true);
    assert.match(page.element("#camera-message").textContent, /已接入/);
    page.context.resetSession();
    assert.equal(page.evaluate("state.modelAvailable"), true);
    assert.equal(page.evaluate("state.modelReady"), false);
});

test("已训练模型时，开启摄像头自动创建会话", async () => {
    const page = createPage();
    let started = 0;
    page.context.api = async () => ({ model: { version: "new-model" } });
    page.context.startSession = async () => { started++; };
    await page.context.startCamera();
    assert.equal(started, 1);
    assert.equal(page.evaluate("state.modelAvailable"), true);
    assert.match(page.element("#camera-message").textContent, /已接入/);
});

test("连接尚在建立时，重复开始不会重复创建会话", async () => {
    const page = createPage();
    let requests = 0;
    page.context.api = async () => { requests++; return { id: "session-one" }; };
    await page.context.startSession();
    await page.context.startSession();
    assert.equal(requests, 1);
    page.evaluate('state.socket.readyState = WebSocket.OPEN; state.socket.handlers.message({ data: JSON.stringify({ type: "connected", model_ready: true }) });');
    assert.equal(page.evaluate("state.modelReady"), true);
    assert.equal(page.evaluate("state.modelAvailable"), true);
});

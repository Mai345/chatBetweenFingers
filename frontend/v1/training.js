const trainingState = {
    stream: null,
    batchId: crypto.randomUUID(),
    busy: false,
    previewTimer: null,
    previewAbort: null,
    cameraGeneration: 0,
};

const TRAINING_HAND_CONNECTIONS = [
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 4],
    [0, 5],
    [5, 6],
    [6, 7],
    [7, 8],
    [5, 9],
    [9, 10],
    [10, 11],
    [11, 12],
    [9, 13],
    [13, 14],
    [14, 15],
    [15, 16],
    [13, 17],
    [17, 18],
    [18, 19],
    [19, 20],
    [0, 17],
];

function drawTrainingHands(hands) {
    const canvas = document.querySelector("#training-landmarks");
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#ffffff";
    const lineWidth = Math.max(2, canvas.width / 250);

    for (const hand of hands) {
        const points = new Map(
            hand.landmarks.map((point) => [point.index, point]),
        );
        for (const [startIndex, endIndex] of TRAINING_HAND_CONNECTIONS) {
            const start = points.get(startIndex);
            const end = points.get(endIndex);
            if (!start || !end) continue;
            context.beginPath();
            context.moveTo(start.x * canvas.width, start.y * canvas.height);
            context.lineTo(end.x * canvas.width, end.y * canvas.height);
            context.strokeStyle = "#202020";
            context.lineWidth = lineWidth + 3;
            context.stroke();
            context.strokeStyle = "#ffffff";
            context.lineWidth = lineWidth;
            context.stroke();
        }
        for (const point of hand.landmarks) {
            context.beginPath();
            context.arc(
                point.x * canvas.width,
                point.y * canvas.height,
                Math.max(3, canvas.width / 180),
                0,
                Math.PI * 2,
            );
            context.fill();
            context.strokeStyle = "#202020";
            context.lineWidth = 1.5;
            context.stroke();
        }
    }
}

async function previewTrainingHands(generation) {
    trainingState.previewTimer = null;
    const stream = trainingState.stream;
    if (!stream || generation !== trainingState.cameraGeneration) return;
    const video = document.querySelector("#training-video");
    const status = document.querySelector("#training-preview-status");
    const controller = new AbortController();
    trainingState.previewAbort = controller;
    let delay = 150;

    try {
        if (!video.videoWidth || !video.videoHeight) return;
        const canvas = document.createElement("canvas");
        const scale = Math.min(
            640 / video.videoWidth,
            480 / video.videoHeight,
            1,
        );
        canvas.width = Math.round(video.videoWidth * scale);
        canvas.height = Math.round(video.videoHeight * scale);
        canvas
            .getContext("2d")
            .drawImage(video, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise((resolve) =>
            canvas.toBlob(resolve, "image/jpeg", 0.72),
        );
        if (controller.signal.aborted) return;
        if (!blob) throw new Error("无法生成预览画面");
        if (blob.size > 307200) throw new Error("预览图片超过300 KB");
        const result = await api("/api/v1/training/preview", {
            method: "POST",
            headers: { "Content-Type": "image/jpeg" },
            body: blob,
            signal: controller.signal,
        });
        if (generation !== trainingState.cameraGeneration) return;
        const overlay = document.querySelector("#training-landmarks");
        overlay.width = canvas.width;
        overlay.height = canvas.height;
        document.querySelector("#training-camera-stage").style.aspectRatio =
            `${canvas.width} / ${canvas.height}`;
        drawTrainingHands(result.hands);
        if (result.hands.length === 1) {
            status.textContent = "已检测到1只手";
        } else if (result.hands.length === 0) {
            status.textContent = "未检测到手";
        } else {
            status.textContent = "请只保留一只手";
        }
    } catch (error) {
        if (
            error.name !== "AbortError" &&
            generation === trainingState.cameraGeneration
        ) {
            drawTrainingHands([]);
            status.textContent = `关键点预览失败：${error.message}`;
            delay = 2000;
        }
    } finally {
        if (
            generation === trainingState.cameraGeneration &&
            trainingState.stream === stream
        ) {
            trainingState.previewAbort = null;
            trainingState.previewTimer = setTimeout(
                () => previewTrainingHands(generation),
                delay,
            );
        }
    }
}

function trainingMessage(message) {
    document.querySelector("#training-message").textContent = message;
}

async function refreshTraining() {
    if (!state.user) return;
    const data = await api("/api/v1/training/status");
    const select = document.querySelector("#training-label");
    const selected = select.value;
    select.replaceChildren();
    for (const gesture of data.gestures) {
        const option = document.createElement("option");
        option.value = gesture.label;
        option.textContent = `${gesture.label}（${gesture.sample_count} 张）`;
        option.selected = gesture.label === selected;
        select.append(option);
    }
    document.querySelector("#training-batch-label").textContent =
        `批次：${trainingState.batchId.slice(0, 8)}`;
    const model = data.model;
    const canTrain =
        data.gestures.length >= 2 &&
        data.gestures.every((gesture) => gesture.sample_count >= 30);
    document.querySelector("#training-train").disabled = !canTrain;
    document.querySelector("#training-train").title = canTrain
        ? "数据已满足训练门槛"
        : "至少2种手势，且每种至少采集30张";
    setModelAvailability(!!model);
    let report = "尚未训练模型。";
    if (model) {
        report = `模型：${model.version}\n样本：${model.sample_count}\n类别：${model.labels.join("、")}\n邻居数：${model.k}\n${model.evaluation.message}`;
        if (model.evaluation.available)
            report += `\n批次留出准确率：${(model.evaluation.accuracy * 100).toFixed(2)}%`;
    }
    document.querySelector("#training-result").textContent = report;
    await loadGestures();
}

function stopTrainingCamera() {
    trainingState.cameraGeneration += 1;
    clearTimeout(trainingState.previewTimer);
    trainingState.previewTimer = null;
    if (trainingState.previewAbort) trainingState.previewAbort.abort();
    trainingState.previewAbort = null;
    if (trainingState.stream)
        trainingState.stream.getTracks().forEach((track) => track.stop());
    trainingState.stream = null;
    document.querySelector("#training-video").srcObject = null;
    drawTrainingHands([]);
    document.querySelector("#training-preview-status").textContent =
        "摄像头已关闭";
}

document
    .querySelector("#gesture-create")
    .addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            await api("/api/v1/training/gestures", {
                method: "POST",
                body: JSON.stringify(
                    Object.fromEntries(new FormData(event.currentTarget)),
                ),
            });
            event.target.reset();
            await refreshTraining();
            trainingMessage("手势已创建");
        } catch (error) {
            trainingMessage(error.message);
        }
    });

document
    .querySelector("#training-camera")
    .addEventListener("click", async () => {
        let generation;
        try {
            stopCamera();
            stopTrainingCamera();
            generation = trainingState.cameraGeneration;
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 640 }, height: { ideal: 480 } },
                audio: false,
            });
            if (generation !== trainingState.cameraGeneration) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            trainingState.stream = stream;
            const video = document.querySelector("#training-video");
            video.srcObject = trainingState.stream;
            await video.play();
            if (generation !== trainingState.cameraGeneration) return;
            previewTrainingHands(generation);
            trainingMessage("采集摄像头已开启");
        } catch (error) {
            if (generation !== trainingState.cameraGeneration) return;
            stopTrainingCamera();
            trainingMessage(`摄像头启动失败：${error.message}`);
        }
    });
document
    .querySelector("#training-camera-stop")
    .addEventListener("click", stopTrainingCamera);
document.querySelector("#training-batch").addEventListener("click", () => {
    trainingState.batchId = crypto.randomUUID();
    refreshTraining().catch((error) => trainingMessage(error.message));
});
document
    .querySelector("#training-capture")
    .addEventListener("click", async (event) => {
        if (trainingState.busy) return;
        const label = document.querySelector("#training-label").value;
        const video = document.querySelector("#training-video");
        if (!label || !trainingState.stream || !video.videoWidth) {
            trainingMessage("请先创建并选择手势，再打开摄像头。");
            return;
        }
        trainingState.busy = true;
        event.currentTarget.disabled = true;
        try {
            const canvas = document.createElement("canvas");
            const scale = Math.min(
                640 / video.videoWidth,
                480 / video.videoHeight,
                1,
            );
            canvas.width = Math.round(video.videoWidth * scale);
            canvas.height = Math.round(video.videoHeight * scale);
            canvas
                .getContext("2d")
                .drawImage(video, 0, 0, canvas.width, canvas.height);
            const blob = await new Promise((resolve) =>
                canvas.toBlob(resolve, "image/jpeg", 0.8),
            );
            if (!blob) throw new Error("无法生成照片，请重试");
            const result = await api(
                `/api/v1/training/capture?label=${encodeURIComponent(label)}&batch_id=${encodeURIComponent(trainingState.batchId)}`,
                {
                    method: "POST",
                    headers: { "Content-Type": "image/jpeg" },
                    body: blob,
                },
            );
            trainingMessage(result.saved ? "采集成功" : "重复样本，未保存");
            await refreshTraining();
        } catch (error) {
            trainingMessage(error.message);
        } finally {
            trainingState.busy = false;
            document.querySelector("#training-capture").disabled = false;
        }
    });

document
    .querySelector("#training-export")
    .addEventListener("click", async () => {
        try {
            const response = await fetch("/api/v1/training/dataset", {
                credentials: "same-origin",
            });
            if (!response.ok) throw new Error("导出失败，请重新登录");
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = "gesture-dataset.json";
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            trainingMessage("数据已导出。");
        } catch (error) {
            trainingMessage(error.message);
        }
    });
document
    .querySelector("#training-import")
    .addEventListener("change", async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        try {
            if (file.size > 15000000) throw new Error("文件最大15 MB");
            const data = JSON.parse(await file.text());
            const result = await api("/api/v1/training/dataset/import", {
                method: "POST",
                body: JSON.stringify(data),
            });
            trainingMessage(
                `导入 ${result.imported} 张，跳过重复 ${result.duplicates} 张。`,
            );
            await refreshTraining();
        } catch (error) {
            trainingMessage(error.message);
        } finally {
            event.target.value = "";
        }
    });
document
    .querySelector("#training-train")
    .addEventListener("click", async () => {
        const button = document.querySelector("#training-train");
        button.disabled = true;
        trainingMessage("正在训练，请稍候…");
        try {
            await api("/api/v1/training/train", { method: "POST" });
            await refreshTraining();
            if (state.stream && state.modelAvailable) await startSession();
            trainingMessage("新模型已启用");
        } catch (error) {
            trainingMessage(error.message);
        } finally {
            button.disabled = false;
        }
    });
document
    .querySelector("#training-refresh")
    .addEventListener("click", () =>
        refreshTraining().catch((error) => trainingMessage(error.message)),
    );
window.addEventListener("hashchange", () => {
    if (location.hash === "#gestures")
        refreshTraining().catch((error) => trainingMessage(error.message));
    else stopTrainingCamera();
});
document.querySelector("#logout").addEventListener("click", stopTrainingCamera);
window.addEventListener("pagehide", stopTrainingCamera);
if (location.hash === "#gestures")
    setTimeout(
        () =>
            refreshTraining().catch((error) => trainingMessage(error.message)),
        1000,
    );

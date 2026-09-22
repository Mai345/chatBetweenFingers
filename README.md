进入后端目录：

cd backend

创建虚拟环境：

py -3.13 -m venv .venv

启用虚拟环境：

.\.venv\Scripts\Activate.ps1

启用成功后，终端开头会显示：

(.venv)

如果 PowerShell 提示禁止运行脚本，可以临时执行：

Set-ExecutionPolicy -Scope Process Bypass

然后重新启用：

.\.venv\Scripts\Activate.ps1

该设置只对当前终端有效，关闭终端后自动失效。

安装项目依赖

确认终端前面有 (.venv)，然后运行：

python -m pip install --upgrade pip

安装项目依赖：

python -m pip install -r requirements.txt

requirements.txt 当前包含：

MediaPipe

OpenCV

scikit-learn

joblib

验证主要依赖：

python -c "import cv2, mediapipe, sklearn; print('OpenCV:', cv2.__version__); print('MediaPipe:', mediapipe.__version__); print('scikit-learn:', sklearn.__version__)"

下载 MediaPipe 模型

MediaPipe手部检测需要单独下载模型：

下载 hand_landmarker.task

在 backend 中创建：

models

将模型保存为：

backend/models/hand_landmarker.task

正确结构：

backend
└── models
    └── hand_landmarker.task

模型文件不会上传到GitHub，因此每位开发者第一次配置环境时都需要下载。

十、测试 MediaPipe

确保当前终端位于：

workspace/backend

运行：

python mediapipeTest/hand_landmark_test.py

预期效果：

打开摄像头窗口

检测手部21个关键点

绘制绿色关键点

绘制白色骨架连线

显示左手或右手

显示检测置信度

按 Q 退出

摄像头方向说明

当前测试代码包含：

frame = cv2.rotate(
    frame,
    cv2.ROTATE_90_CLOCKWISE
)

这是因为最初开发电脑的摄像头画面需要顺时针旋转90度。

如果其他电脑的摄像头画面本来就是正的，可以注释或删除这一行：

# frame = cv2.rotate(
#     frame,
#     cv2.ROTATE_90_CLOCKWISE
# )

如果方向相反，可以改为：

cv2.ROTATE_90_COUNTERCLOCKWISE

采集手势数据

运行：

python mediapipeTest/collect_data.py

当前按键：

按键

功能

S

保存当前手势样本

Q

退出程序

采集前需要在 collect_data.py 中设置：

CURRENT_LABEL = "open_palm"
PARTICIPANT_ID = "p01"
PARTICIPANT_NAME = "你的姓名或代号"
TARGET_HAND = "Right"
TARGET_SAMPLES = 30

不同参与者使用不同编号：

p01 zfyh
p02 hxa
p03 myq

不同手势使用不同标签：

open_palm
fist
v_sign

采集数据默认保存到：

backend/data/gesture_samples.csv

CSV数据不会上传到GitHub。
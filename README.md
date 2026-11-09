# 谈指间 v1

基础接口以 [本机第一版接口规范](docs/api-local-v1-spec.md) 为准，个人模型训练扩展见下文。
后端使用 FastAPI 和 SQLite，前端由同一个服务提供。

## 启动项目

在 `workspace/backend` 目录创建虚拟环境：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动应用：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app
```

浏览器打开 http://127.0.0.1:8000/app/ 。

首次使用时创建账号。前端和后端使用同一个地址，不需要额外启动前端服务器。
当前配置用于本机开发，请仅绑定本机地址。

## 已有功能

- 注册、登录、退出和 HttpOnly Cookie 登录会话。
- SQLite 保存用户设置、交流文字和历史记录。
- 摄像头预览、摄像头切换和镜像设置。
- 示例手势词库、自定义手势与关键点采集预览。
- 关键点数据导入导出、个人模型训练和自动接入。
- 句子编辑、清空、撤销识别词和浏览器语音朗读。
- 结束并保存、取消会话、断线后保存非空内容。
- 实时识别的逐帧发送、稳定确认和冷却逻辑。

## 目录结构

```text
backend/
  app/              FastAPI 接口、训练与识别逻辑
  tests/            自动化测试
  mediapipeTest/    保留的离线算法实验
  data/            本机数据库和个人采集数据（不上传 Git）
  models/          本机下载与训练生成的模型（不上传 Git）
frontend/
  v1/              当前前端页面、样式和脚本
  pages/           旧入口，跳转到当前页面
docs/              v1 基础接口规范
```

## 接口和代码位置

- REST：`/api/v1`。
- 实时连接：`/ws/v1/recognition?session_id=...`。
- 健康检查：`/api/v1/health`。
- 接口文档：http://127.0.0.1:8000/docs 。
- 应用启动和错误处理：`backend/app/main.py`。
- 业务接口：`backend/app/api.py`。
- 前端：`frontend/v1/`。

正式服务只提供 v1 接口。启动时不会加载离线实验中的 MediaPipe 或KNN模型。

## 自定义手势、采集和重训

在前端“手势词库”页面创建自定义名称，打开采集摄像头，选择手势后逐张拍照。
采集预览实时显示关键点和骨架连线；预览接口 `/api/v1/training/preview`
只检测画面，不保存照片或增加样本。关闭摄像头或离开页面时停止检测。
只保存归一化关键点，不保存原始照片。请使用同一只手，正对摄像头并覆盖不同距离和角度。
每类至少30张、至少2类才能训练；换人或换一轮采集时点击“开始新批次”。

点击“导出数据”下载 JSON；使用“导入数据文件”在后端导入并合并，重复样本会跳过。
不接受外来 joblib 模型，只接受版本一致、63维有限数值的关键点数据。
点击“训练并启用新模型”后，先按批次留出评估，再用全部样本训练最终模型。
只有单批数据时仍可训练，但会明确提示没有独立测试成绩。报告中的留出集也用于选择K，
最终识别能力需要额外的独立人员和场景验收。

模型使用距离加权KNN，并以同类别训练样本的近邻距离估计拒识阈值；拒识阈值是启发式，
不是已校准的真实概率。摄像头采集和实时识别共用同一套特征处理，不旋转、不镜像发送。
训练前先结束已有的实时会话。训练完成后自动启用新模型并同步前端状态；
打开实时交流摄像头会自动建立识别会话，不需要再次手动接入模型。

数据与模型按账号隔离。模型保存到数据库旁的 `personal_models/<用户ID>.joblib`，
被 Git 忽略，服务重启后自动加载。需要本地 `backend/models/hand_landmarker.task`
才能提取关键点；没有该文件时训练/采集会明确返回错误，原模型不被替换。

接口：`/api/v1/training/gestures`、`/capture`、`/dataset`、`/dataset/import`、`/train`、`/status`，
除导出的 JSON 外，均使用现有登录 Cookie 和REST响应格式。

## 模型接入边界

没有个人模型时，健康检查返回 `503 MODEL_NOT_READY`，文字会话仍可使用。
完成个人模型训练后，该账号的新识别会话自动使用训练后的模型。

后续可在应用启动时，把 `app.state.recognizer` 设置为实现
`process(image_bgr)` 的实例。该方法返回：

```python
hands, candidate
```

`hands` 是接口规范中的手部观察列表；`candidate` 是包含 `label`、
`confidence` 的字典，或 `None`。置信度使用0到1之间的数字。
后端负责稳定确认、冷却、事件和句子保存。

浏览器发送的JPEG不镜像，镜像仅用于预览。模型的图像方向和特征处理方式
需要与训练过程一致。

## 本机数据

数据库默认保存为 `backend/data/local_v1.sqlite3`，已由 Git 忽略。
可以用 `CBF_DB_PATH` 环境变量指定另一个数据库位置。
本次接口迁移不改变数据库路径、表结构或用户数据。

## 验证

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -v -p no:cacheprovider
```

测试使用临时数据库和模拟识别器，不需要摄像头或本地模型文件。
临时文件由 pytest 放在系统临时目录，无需在 `backend` 中手动创建 `.pytest-run-*`。

在仓库根目录运行前端检查（需要 Node.js）：

```powershell
node --check frontend/v1/app.js
node --check frontend/v1/training.js
node --test backend/tests/frontend_model_status.cjs
```

## 离线算法实验

`backend/mediapipeTest/` 中保留了原来的摄像头测试、数据采集和KNN训练脚本。
这些脚本不由正式应用导入，也不是启动v1的前置条件。

实验需要单独准备 `backend/models/hand_landmarker.task`。采集数据保存到
`backend/data/gesture_samples.csv`，训练模型保存到 `backend/models/`。
数据和模型文件不会提交到 Git。只加载可信来源的模型。

运行实验脚本前，检查摄像头旋转和镜像配置是否适合当前设备。

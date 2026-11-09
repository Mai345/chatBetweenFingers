## 1. 流程

```text
本机注册/登录
  → 浏览器打开摄像头
  → WebSocket发送JPEG
  → Python提取关键点并识别静态手势
  → 稳定确认并拼接句子
  → 编辑或朗读句子
  → 保存到本机SQLite
  → 查看自己的历史记录
```
## 2. 本机架构

```text
浏览器前端（localhost）
  ├── 注册与登录
  ├── 摄像头预览
  ├── Canvas关键点绘制
  └── speechSynthesis语音朗读
          │
          ├── HTTP
          └── WebSocket JPEG
                    ↓
FastAPI后端（localhost）
  ├── 本地登录会话
  ├── MediaPipe关键点
  ├── 静态分类器
  ├── 稳定确认与句子状态
  └── SQLite
```
## 3. 接口清单

REST 前缀：`/api/v1`

| 方法 | 路径 | 是否登录 | 用途 |
|---|---|---:|---|
| GET | `/health` | 否 | 检查后端和模型 |
| POST | `/auth/register` | 否 | 本机注册 |
| POST | `/auth/login` | 否 | 本机登录 |
| POST | `/auth/logout` | 是 | 退出登录 |
| GET | `/auth/me` | 是 | 获取当前用户 |
| GET | `/gestures` | 否 | 查询静态手势词库 |
| GET | `/gestures/{id}` | 否 | 查询手势详情 |
| GET | `/settings` | 是 | 获取当前用户设置 |
| PATCH | `/settings` | 是 | 修改当前用户设置 |
| POST | `/sessions` | 是 | 创建识别会话 |
| GET | `/sessions` | 是 | 查询历史记录 |
| GET | `/sessions/{id}` | 是 | 查询历史详情 |
| DELETE | `/sessions/{id}` | 是 | 删除历史记录 |
| WS | `/ws/recognition?session_id=...` | Cookie | 实时静态识别 |

系统接口清单以上表为准。

## 4. 通用REST格式

成功：

```json
{
  "data": {},
  "message": "ok"
}
```

失败：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法"
  }
}
```

状态码：

| 状态码 | 用途 |
|---:|---|
| 200 | 查询或修改成功 |
| 201 | 创建成功 |
| 204 | 删除或退出成功 |
| 400 | 请求参数错误 |
| 401 | 未登录或登录已失效 |
| 404 | 资源不存在或不属于当前用户 |
| 409 | 用户名或识别会话冲突 |
| 500 | 后端内部错误 |
| 503 | 识别模型未准备好 |

## 5. 本地登录

### 5.1 实现方式

登录认证使用服务端会话 Cookie。登录成功后：

1. 后端生成随机登录会话令牌。
2. 数据库只保存令牌哈希。
3. 浏览器通过 HttpOnly Cookie 保存原始令牌。
4. 登录成功后的 HTTP 请求和 WebSocket 握手自动携带 Cookie。

Cookie建议：

```text
名称：local_session
HttpOnly：true
SameSite：Lax
Path：/
有效期：7天
Secure：本机HTTP测试时为false
```

密码必须使用 Argon2id 哈希保存，不得保存明文。

### 5.2 注册

```http
POST /api/v1/auth/register
```

```json
{
  "username": "zhangsan",
  "password": "Example123!",
  "display_name": "张三"
}
```

规则：

- 用户名4～32个字符且唯一。
- 密码8～64个字符。
- 显示名称1～32个字符。
- 注册成功后自动登录并设置Cookie。

### 5.3 登录

```http
POST /api/v1/auth/login
```

```json
{
  "username": "zhangsan",
  "password": "Example123!"
}
```

成功：

```json
{
  "data": {
    "id": "user-uuid",
    "username": "zhangsan",
    "display_name": "张三"
  },
  "message": "login_success"
}
```

用户名不存在或密码错误统一返回：

```json
{
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "用户名或密码错误"
  }
}
```

### 5.4 查询当前用户

```http
GET /api/v1/auth/me
```

返回Cookie对应的当前用户。

### 5.5 退出

```http
POST /api/v1/auth/logout
```

- 删除数据库登录会话。
- 清除Cookie。
- 关闭当前用户的识别连接。
- 成功返回 `204 No Content`。

## 6. 健康检查

```http
GET /api/v1/health
```

```json
{
  "data": {
    "status": "ok",
    "database_ready": true,
    "landmark_model_ready": true,
    "static_model_ready": true
  },
  "message": "ok"
}
```

模型未加载时返回 `503 MODEL_NOT_READY`。

## 7. 手语词库

```http
GET /api/v1/gestures
GET /api/v1/gestures/{gesture_id}
```

第一版词库由初始化脚本写入，只读取、不在线编辑。

词条：

```json
{
  "id": "gesture-uuid",
  "label": "谢谢",
  "display_name": "谢谢",
  "category": "daily",
  "description": "动作说明",
  "example_image_url": "/assets/gestures/thanks.jpg",
  "difficulty": "easy",
  "enabled": true
}
```

词库返回本系统支持的静态手势。

## 8. 用户设置

### 8.1 获取

```http
GET /api/v1/settings
```

```json
{
  "data": {
    "confidence_threshold": 0.8,
    "stable_duration_ms": 1000,
    "cooldown_ms": 1200,
    "mirror_preview": true,
    "show_landmarks": true,
    "show_confidence": true,
    "audio_enabled": true,
    "audio_rate": 1.0,
    "audio_volume": 1.0
  },
  "message": "ok"
}
```

### 8.2 修改

```http
PATCH /api/v1/settings
```

只修改请求中出现的字段。

范围：

| 字段 | 范围 |
|---|---|
| `confidence_threshold` | 0.5～0.99 |
| `stable_duration_ms` | 300～3000 |
| `cooldown_ms` | 300～5000 |
| `audio_rate` | 0.5～2.0 |
| `audio_volume` | 0.0～1.0 |

语音朗读由浏览器 `speechSynthesis` 完成，后端不处理音频。

## 9. 识别会话REST接口

### 9.1 创建

```http
POST /api/v1/sessions
```

请求体为空或 `{}`。

后端使用当前用户设置创建会话：

```json
{
  "data": {
    "id": "session-uuid",
    "status": "created",
    "settings": {
      "confidence_threshold": 0.8,
      "stable_duration_ms": 1000,
      "cooldown_ms": 1200
    }
  },
  "message": "session_created"
}
```

同一用户只允许一个未结束会话。重复创建返回 `409 ACTIVE_SESSION_EXISTS`。

### 9.2 查询历史

```http
GET /api/v1/sessions?page=1&page_size=20
```

只返回当前用户的数据，按开始时间倒序。

```json
{
  "data": [
    {
      "id": "session-uuid",
      "draft_text": "我需要帮助",
      "gesture_count": 3,
      "average_confidence": 0.91,
      "started_at": "2026-09-25T15:30:00+08:00",
      "finished_at": "2026-09-25T15:31:12+08:00"
    }
  ],
  "message": "ok"
}
```

分页使用 `page` 和 `page_size` 参数。

### 9.3 查询详情

```http
GET /api/v1/sessions/{session_id}
```

返回识别事件、词语序列、最终文本、置信度和时间。

### 9.4 删除

```http
DELETE /api/v1/sessions/{session_id}
```

直接从SQLite删除当前用户自己的会话和事件，成功返回 `204 No Content`。

## 10. 实时识别WebSocket

### 10.1 连接

```text
ws://localhost:8000/ws/v1/recognition?session_id=<session_uuid>
```

浏览器自动携带 `local_session` Cookie。后端必须确认：

- 用户已经登录；
- 识别会话属于该用户；
- 会话尚未结束。

### 10.2 连接成功

连接成功后自动开始识别，不需要额外发送 `start`：

```json
{
  "type": "connected",
  "session_id": "session-uuid",
  "max_frame_size_bytes": 307200,
  "recommended_width": 640,
  "recommended_height": 480
}
```

### 10.3 JPEG帧

- 前端发送JPEG二进制消息。
- 一个消息是一张完整JPEG。
- 推荐640×480，最大1280×720。
- 单帧最大300KB。
- 推荐10～15 FPS。
- 前端收到上一帧结果后才能发送下一帧。
- 浏览器可以镜像预览，但发送的图像不镜像。

### 10.4 帧结果

```json
{
  "type": "frame_result",
  "frame_id": 18,
  "state": "candidate",
  "processing_time_ms": 63,
  "hands": [
    {
      "handedness": "Right",
      "detection_score": 0.97,
      "landmarks": [
        {
          "index": 0,
          "x": 0.42,
          "y": 0.63,
          "z": -0.08
        }
      ]
    }
  ],
  "candidate": {
    "label": "谢谢",
    "confidence": 0.91
  },
  "stable_ms": 620,
  "draft_text": "我需要"
}
```

`state`：

```text
no_hand
rejected
candidate
committed
cooldown
```

### 10.5 稳定确认

- 同一标签持续达到 `stable_duration_ms` 后加入句子。
- 标签变化或没有手时重新计时。
- 确认后进入 `cooldown_ms` 冷却。
- 冷却期间不重复加入同一标签。

确认事件：

```json
{
  "type": "gesture_committed",
  "event": {
    "id": "event-uuid",
    "label": "谢谢",
    "confidence": 0.94
  },
  "recognized_tokens": ["我", "谢谢"],
  "draft_text": "我谢谢"
}
```

### 10.6 句子编辑

```json
{
  "type": "sentence_command",
  "action": "replace_text",
  "text": "我需要你的帮助"
}
```

允许：

```text
undo_last
clear
replace_text
```

后端完成修改后返回：

```json
{
  "type": "sentence_updated",
  "recognized_tokens": ["我", "需要", "帮助"],
  "draft_text": "我需要你的帮助"
}
```

### 10.7 正常结束

```json
{
  "type": "stop"
}
```

- 有确认内容时保存到SQLite。
- 空会话不保存历史。
- 返回 `session_finished` 后关闭连接。

### 10.8 取消

```json
{
  "type": "cancel"
}
```

取消后不保存历史。

### 10.9 意外断开

连接意外断开时：

- 有确认内容则立即保存当前会话。
- 没有内容则取消会话。
- 用户需要创建新会话重新开始。

### 10.10 错误

```json
{
  "type": "error",
  "code": "INVALID_IMAGE_FRAME",
  "message": "无法解析JPEG图像",
  "recoverable": true
}
```

错误码：

```text
NOT_AUTHENTICATED
SESSION_NOT_FOUND
SESSION_ALREADY_FINISHED
INVALID_IMAGE_FRAME
FRAME_TOO_LARGE
MODEL_NOT_READY
INFERENCE_FAILED
INTERNAL_ERROR
```

## 11. 接口字段中文说明

本节集中解释文档中出现的全部请求和响应字段。字段路径中的 `[]` 表示数组中的每一个元素。

### 11.1 通用REST字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `data` | 对象或数组 | 成功时是 | 接口成功后返回的业务数据；具体内容由接口决定。 |
| `message` | 字符串 | 成功时是 | 对本次成功操作的简短说明，例如 `ok` 或 `login_success`。 |
| `error` | 对象 | 失败时是 | 接口失败时的错误信息对象。 |
| `error.code` | 字符串 | 失败时是 | 程序可识别的稳定错误码，前端根据它决定如何处理。 |
| `error.message` | 字符串 | 失败时是 | 给开发人员或用户阅读的中文错误说明。 |

### 11.2 注册和登录字段

| 字段 | 类型 | 是否必填 | 中文解释 |
|---|---|---:|---|
| `username` | 字符串 | 是 | 用户登录时使用的唯一用户名，长度为4～32个字符。 |
| `password` | 字符串 | 是 | 用户输入的原始密码，只能在请求过程中短暂使用，后端不得保存明文。 |
| `display_name` | 字符串 | 注册时是 | 页面中展示的用户名称，可以与登录用户名不同。 |
| `data.id` | UUID字符串 | 响应时是 | 当前登录用户在数据库中的唯一编号。 |
| `data.username` | 字符串 | 响应时是 | 当前登录用户的用户名。 |
| `data.display_name` | 字符串 | 响应时是 | 当前登录用户在页面中显示的名称。 |
| `local_session` | Cookie字符串 | 登录后是 | 后端生成的本地登录会话令牌，由浏览器自动保存和发送，JavaScript不能读取。 |

### 11.3 健康检查字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `status` | 字符串 | 是 | 后端整体状态；`ok`表示服务可以正常处理请求。 |
| `database_ready` | 布尔值 | 是 | SQLite是否已经连接并可以正常读写。 |
| `landmark_model_ready` | 布尔值 | 是 | MediaPipe手部关键点检测模型是否加载成功。 |
| `static_model_ready` | 布尔值 | 是 | 静态手势分类模型是否加载成功。 |

### 11.4 手语词条字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `id` | UUID字符串 | 是 | 手语词条在数据库中的唯一编号。 |
| `label` | 字符串 | 是 | 算法输出使用的标准手势标签，例如“谢谢”。 |
| `display_name` | 字符串 | 是 | 页面展示的手势名称。通常与 `label` 相同，但允许使用更友好的名称。 |
| `category` | 字符串 | 是 | 手势所属分类，例如日常交流、数字或问候。 |
| `description` | 字符串 | 是 | 对动作姿势和使用方法的中文说明。 |
| `example_image_url` | 字符串或null | 否 | 手势示例图片在本机网站中的访问路径；没有图片时为 `null`。 |
| `difficulty` | 字符串 | 是 | 动作学习难度，例如 `easy`、`medium`、`hard`。 |
| `enabled` | 布尔值 | 是 | 该手势当前是否在词库和识别系统中启用。 |

### 11.5 用户设置字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `confidence_threshold` | 小数 | 是 | 识别结果被接受所需的最低置信度，数值越高越严格。 |
| `stable_duration_ms` | 整数 | 是 | 同一候选手势必须持续保持多久才正式加入句子，单位为毫秒。 |
| `cooldown_ms` | 整数 | 是 | 一个词语确认后暂停重复确认的时间，单位为毫秒。 |
| `mirror_preview` | 布尔值 | 是 | 是否把浏览器中的摄像头预览水平镜像，使其看起来像镜子。 |
| `show_landmarks` | 布尔值 | 是 | 是否在视频画面上绘制21个手部关键点和连线。 |
| `show_confidence` | 布尔值 | 是 | 是否在识别页面显示当前候选结果的置信度。 |
| `audio_enabled` | 布尔值 | 是 | 是否允许浏览器朗读最终句子。 |
| `audio_rate` | 小数 | 是 | 浏览器语音朗读速度，`1.0`表示正常速度。 |
| `audio_volume` | 小数 | 是 | 浏览器语音音量，`0.0`表示静音，`1.0`表示最大音量。 |

### 11.6 识别会话REST字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `page` | 整数 | 查询时否 | 要查询的页码，从1开始，默认值为1。 |
| `page_size` | 整数 | 查询时否 | 每页最多返回多少条记录，默认值为20。 |
| `id` | UUID字符串 | 是 | 一次识别会话的唯一编号。 |
| `status` | 字符串 | 是 | 识别会话当前状态，例如 `created`、`active` 或 `finished`。 |
| `settings` | 对象 | 创建响应时是 | 本次会话实际使用的识别参数副本。 |
| `draft_text` | 字符串 | 历史响应时是 | 用户最终编辑和保存的句子文本。 |
| `gesture_count` | 整数 | 历史响应时是 | 本次会话正式确认了多少个手势词语。 |
| `average_confidence` | 小数或null | 历史响应时是 | 所有已确认手势的平均置信度；没有手势时为 `null`。 |
| `started_at` | ISO 8601字符串 | 是 | 识别会话开始的本地时间，包含时区。 |
| `finished_at` | ISO 8601字符串或null | 是 | 识别会话结束时间；尚未结束时为 `null`。 |
| `recognized_tokens` | 字符串数组 | 详情时是 | 按确认顺序保存的手势词语列表。 |
| `recognized_events` | 对象数组 | 详情时是 | 每一次词语确认的详细事件列表，包括标签、置信度和确认时间。 |

### 11.7 WebSocket连接字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `session_id` | UUID字符串 | 是 | 当前WebSocket对应的识别会话编号，必须属于当前登录用户。 |
| `type` | 字符串 | 是 | WebSocket消息类型，用于区分连接成功、帧结果、词语确认、编辑命令和错误。 |
| `max_frame_size_bytes` | 整数 | 连接成功时是 | 后端允许接收的单张JPEG最大字节数，当前值为307200。 |
| `recommended_width` | 整数 | 连接成功时是 | 前端建议发送的图像宽度，单位为像素。 |
| `recommended_height` | 整数 | 连接成功时是 | 前端建议发送的图像高度，单位为像素。 |

### 11.8 帧识别结果字段

| 字段路径 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `type` | 字符串 | 是 | 消息类型；帧处理结果固定为 `frame_result`。 |
| `frame_id` | 整数 | 是 | 后端为收到的JPEG帧分配的递增编号。 |
| `state` | 字符串 | 是 | 当前帧的识别状态：`no_hand`、`rejected`、`candidate`、`committed`或`cooldown`。 |
| `processing_time_ms` | 整数 | 是 | 后端处理当前帧所用时间，单位为毫秒，不包含浏览器显示时间。 |
| `hands` | 对象数组 | 是 | 当前帧检测到的手部列表；没有检测到手时为空数组。 |
| `hands[].handedness` | 字符串 | 有手时是 | MediaPipe判断的左手或右手，值为 `Left`、`Right` 或 `Unknown`。 |
| `hands[].detection_score` | 小数 | 有手时是 | MediaPipe对该手部检测结果的可信程度。 |
| `hands[].landmarks` | 对象数组 | 有手时是 | 该手的21个关键点坐标。 |
| `hands[].landmarks[].index` | 整数 | 是 | 关键点编号，范围0～20，每个编号对应固定的手部关节。 |
| `hands[].landmarks[].x` | 小数 | 是 | 关键点在图像水平方向的归一化位置，通常范围为0～1。 |
| `hands[].landmarks[].y` | 小数 | 是 | 关键点在图像垂直方向的归一化位置，通常范围为0～1。 |
| `hands[].landmarks[].z` | 小数 | 是 | 关键点相对手腕的深度值，用于描述前后位置，不是实际厘米距离。 |
| `candidate` | 对象或null | 是 | 当前静态分类器给出的最佳候选手势；没有候选时为 `null`。 |
| `candidate.label` | 字符串 | 有候选时是 | 当前最佳候选的手势标签。 |
| `candidate.confidence` | 小数 | 有候选时是 | 当前候选的模型置信度，范围0～1。 |
| `stable_ms` | 整数 | 是 | 当前候选已经连续稳定存在的时间，单位为毫秒。 |
| `draft_text` | 字符串 | 是 | 当前会话已经形成并允许用户编辑的句子文本。 |

### 11.9 词语确认字段

| 字段路径 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `type` | 字符串 | 是 | 消息类型；词语正式确认时固定为 `gesture_committed`。 |
| `event` | 对象 | 是 | 本次词语确认事件的详细信息。 |
| `event.id` | UUID字符串 | 是 | 本次确认事件的唯一编号。 |
| `event.label` | 字符串 | 是 | 正式加入句子的手势词语。 |
| `event.confidence` | 小数 | 是 | 词语正式确认时使用的置信度。 |
| `recognized_tokens` | 字符串数组 | 是 | 当前会话已经确认的全部手势词语，按时间顺序排列。 |
| `draft_text` | 字符串 | 是 | 加入本次词语后的完整句子文本。 |

### 11.10 句子命令字段

| 字段 | 类型 | 是否必填 | 中文解释 |
|---|---|---:|---|
| `type` | 字符串 | 是 | 消息类型；编辑句子时为 `sentence_command`，编辑成功后为 `sentence_updated`。 |
| `action` | 字符串 | 请求时是 | 要执行的编辑动作：撤销上一词、清空或替换文本。 |
| `text` | 字符串 | `replace_text`时是 | 用户手动编辑后的完整句子文本。 |
| `recognized_tokens` | 字符串数组 | 响应时是 | 执行命令后仍保留的识别词语列表。 |
| `draft_text` | 字符串 | 响应时是 | 执行编辑命令后的完整句子。 |

`action`取值：

| 值 | 中文解释 |
|---|---|
| `undo_last` | 撤销最后一个已确认手势。 |
| `clear` | 清空当前词语列表和句子。 |
| `replace_text` | 使用 `text` 字段替换当前句子文本。 |

### 11.11 控制与错误字段

| 字段 | 类型 | 是否必有 | 中文解释 |
|---|---|---:|---|
| `type=stop` | 字符串 | 正常结束时是 | 通知后端结束当前识别会话并保存已有内容。 |
| `type=cancel` | 字符串 | 取消时是 | 通知后端取消当前识别会话且不保存内容。 |
| `type=error` | 字符串 | 错误时是 | 表示当前WebSocket消息是一条错误消息。 |
| `code` | 字符串 | 错误时是 | 前端程序可以判断的错误码。 |
| `message` | 字符串 | 错误时是 | 错误的中文说明。 |
| `recoverable` | 布尔值 | 错误时是 | `true`表示连接可以继续使用，`false`表示应关闭连接或重新开始。 |

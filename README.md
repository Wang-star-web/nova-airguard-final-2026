# AirGuard 校园空气质量监测平台

> 技术栈：MQTT + Python + HTML + JavaScript + Three.js
> 两条数据链路：实时边缘链（D1~D5、E1~E3） + 离线分析链（CSV → pandas/matplotlib → report.html）

---

## 1. 项目简介

AirGuard 是一个校园空气质量监测平台：三块区域（`zone-n` 宿舍区、`zone-s` 教学区、`zone-w` 食堂区）的传感器数据经 **MQTT（WebSocket）** 实时上报到监测台，平台完成状态判级、优先打分、事件闭环处理，并提供 3D 数字空间、多模态交互、移动端三套扩展页面，以及一套完全离线的 CSV 分析报告工具。

**统一 JSON 报文**（MQTT 主题 `airguard/zones/all`）：

```json
{"zoneId": "zone-w", "pm25": 160, "co2": 900, "crowdLevel": 2, "status": "", "time": "2026-10-08T10:00:00+08:00"}
```

> `status` 恒为空字符串，由平台代码按固定规则计算，禁止硬编码。

**固定判级规则**（顺序严格）：

1. `pm25 > 150` → 重度污染
2. 否则 `pm25 > 75` → 轻度污染
3. 否则 `co2 >= 1500` → 通风不足风险
4. 其余 → 正常

---

## 2. 项目目录结构说明

```
nova-airguard-final-2026/
├── index.html             # Web 监测台（D1/D2/D3/D5 面板 + 运行日志）
├── mqtt_publish.py        # MQTT 模拟发布脚本（18 个测试场景）
├── d5_ml_compare.py       # D5：固定规则 vs ML(IsolationForest) 组合裁决
├── generate_report.py     # 离线分析链：CSV → 统计 + 趋势图 → report.html
├── airguard_data.csv      # 离线分析输入数据（可替换）
├── report.html            # 离线分析报告（脚本自动生成，重跑即更新）
├── three_3d/
│   └── index.html         # E1：Three.js 3D 数字空间
├── multimodal/
│   └── index.html         # E2：摄像头 + 语音指令 + 语音播报 + 感知记录
├── mobile/
│   └── index.html         # E3：移动端 H5（跨端干预同步）
└── Evidence/              # 证据截图与说明文档（D1-D5、E1-E3）
```

---

## 3. 环境依赖清单

| 依赖 | 版本/说明 | 用途 |
|---|---|---|
| Windows 10/11 | — | 运行环境 |
| Mosquitto | 2.1+（2.0.x 的 WebSocket 监听有已知缺陷） | 本地 MQTT Broker（TCP 1883 + WebSocket 8083） |
| Python | python.org 版（用 `py` 启动器） | 运行发布/分析脚本 |
| paho-mqtt + websockets | `py -m pip install paho-mqtt websockets` | Python 侧 MQTT over WebSocket |
| scikit-learn | `py -m pip install scikit-learn` | D5 IsolationForest |
| pandas + matplotlib | `py -m pip install pandas matplotlib` | 离线分析链 |
| Chrome / Edge | 较新版本 | 前端页面（E2 语音/摄像头需要） |
| 网络 | 首次打开页面需联网 | 加载公开 CDN（mqtt.js@5、three@0.147.0，jsdelivr 主 + unpkg 备） |

一条命令装齐 Python 依赖：

```bash
py -m pip install paho-mqtt websockets scikit-learn pandas matplotlib
```

---

## 4. 完整分步启动复现流程

> 总体顺序：① 启动 Broker → ② 打开网页 → ③ 运行脚本。每一步都有"预期现象"，方便对照检查。

### 第 1 步：启动 MQTT Broker（Mosquitto）

安装 Mosquitto 后，新建配置文件 `mosquitto.conf`（内容如下，路径任意，例如 `D:\Program Files\Mosquitto\mosquitto.conf`）：

```ini
allow_anonymous true          # 2.x 默认拒绝匿名连接，必须显式开启
listener 1883 127.0.0.1      # 普通 MQTT（TCP）
listener 8083 127.0.0.1      # MQTT over WebSocket（网页与脚本连接端口）
protocol websockets          # 作用于上方最近的 listener(8083)
# 需局域网内其他设备（如手机）访问时，把 127.0.0.1 改为 0.0.0.0
```

启动 Broker：

```powershell
mosquitto -c mosquitto.conf -v
```

**预期现象**：终端显示监听 1883 与 8083 端口，无报错。

### 第 2 步：打开 Web 监测台

双击打开 `index.html`（直接双击即可，无需本地服务器）。

**预期现象**：右上角连接徽章变为绿色 **"已连接 ws://10.102.128.15:8083/mqtt"**（本机 IPv4；页面会自动回退尝试 `127.0.0.1`）。此时页面 D1 三张区域卡片显示"等待数据…"，属正常现象——还没有任何传感器报文。

### 第 3 步：运行 MQTT 模拟发布脚本

**持续上报（推荐日常演示）**：

```bash
py mqtt_publish.py stream --all-zones
```

**预期现象**：三张卡片每 5 秒刷新一次（Ctrl+C 停止）；约 12% 概率注入异常，异常时自动新建 D3 事件。

**单场景测试**（发完自动退出，取证用）：

```bash
py mqtt_publish.py <场景名>
```

| 场景 | 用途 |
|---|---|
| `normal` / `abnormal` | 正常 / 异常报文 |
| `duplicate` | 同 zoneId+time 重复消息（去重验证） |
| `outoforder` | 乱序时间消息（排序验证） |
| `late` | 迟到旧时间消息（仅追加历史） |
| `worsen` / `improve` / `recover` | 恶化回退 / 好转保持 / 恢复流程（**交互**：提示处先在页面点【执行干预】再回车） |
| `model` | D2 低置信度需人工复核 |
| `demo` | 全流程演示（含两次干预交互） |
| `d2_case1/2/3` | D2 打分取证用例（zone-n/s/w 三区域） |
| `d3_case_abnormal/improve/worsen/dup/late` | D3 状态机五规则取证用例 |

> 提示：每个场景演示前建议刷新页面（F5）以获得确定性结果；交互场景的脚本提示会等你按回车。

### 第 4 步（可选）：打开 E1 / E2 / E3 页面

- `three_3d/index.html`：3D 数字空间，楼栋颜色随状态实时变化
- `multimodal/index.html`：多模态交互（摄像头、语音指令、语音播报）
- `mobile/index.html`：移动端 H5

三个页面与监测台订阅同一 Broker、同一数据主题，**状态经 MQTT 天然同步**。典型联调演示：三页同开 → `py mqtt_publish.py d3_case_abnormal` 产生 OPEN 事件 → 在 **mobile 页点【执行干预】** → 监测台 D3 表、E1 楼栋（开始闪烁）同步变为 HANDLING → 再发正常报文 → 三端同步 RECOVERED。

### 第 5 步：D5 规则 vs ML 组合裁决

```bash
py d5_ml_compare.py
```

**预期现象**：终端打印 4 个构造样本的「输入 | 固定规则结果 | ML判断结果 | 最终组合裁决」（样本 A/B 两者不一致、C/D 一致）；同时把结果推送到 MQTT（主题 `airguard/zones/d5`），**监测台 D5 面板自动刷新三项结果**。Broker 未启动时仅提示跳过推送，脚本照常独立运行。

### 第 6 步：离线分析链

见第 6 章。

---

## 5. D1-D5、E1-E3 各功能简要说明

| 模块 | 所在文件 | 功能说明 |
|---|---|---|
| **D1** | index.html | 三区域实时卡片：pm25 / co2 / crowdLevel / 代码计算的 4 级状态徽章 / 更新时间；未知 zoneId 拒收，区域严格隔离 |
| **D2** | index.html | 优先关注面板：分数 = 环境权重(4/3/2/1)×2 + crowdLevel，按分数降序；`crowdSource="model"` 且 confidence<0.6 标记【需人工复核】、crowdLevel 不计分；展示优先 zoneId 与判断理由 |
| **D3** | index.html | 事件状态机 OPEN/HANDLING/RECOVERED：异常新建 OPEN；按钮 OPEN→HANDLING；仅后续新 MQTT 正常数据可 RECOVERED；恶化回退 OPEN；迟到旧消息仅追加历史；zoneId+time 去重、乱序按 time 排序；event 固定 8 字段 + 干预按钮 + 运行日志 |
| **D4** | 全端 + Evidence/D4 | 实时链路韧性：停止 Broker → 前端全部断开并显示离线；重启 Broker → mqtt.js 自动重连，数据流恢复（证据见 Evidence/D4） |
| **D5** | d5_ml_compare.py + index.html D5 面板 | 固定规则 vs IsolationForest（特征 pm25/co2）对比，代码内写死的**规则优先组合裁决**；结果经 MQTT 推送到 D5 面板实时展示 |
| **E1** | three_3d/index.html | Three.js 3D 数字空间：3 栋楼绑定三区域，楼体颜色跟随状态；优先关注区地面光环发光 + TOP1 徽标；HANDLING 楼体闪烁；点击楼栋弹窗展示全部信息并镜头平滑聚焦 |
| **E2** | multimodal/index.html | 浏览器原生 API：getUserMedia 摄像头；语音指令（"查看宿舍区/教学区/食堂区"切区、"朗读状态"TTS 播报实时数据、"记录现场"拍照）；感知记录 JSON 六字段（zoneId/imageId/crowdLevel/confidence/source/time，source="simulated"），可下载 JSON 与照片；ASR 不可用时提供手动按钮兜底 |
| **E3** | mobile/index.html | 移动端 H5：与监测台同源订阅（无任何手动输入框）；简化界面（区域切换 + 核心指标 + 事件块）；【执行干预】按钮经控制主题 `airguard/zones/control` 发布指令，监测台/E1/本端同步完成 OPEN→HANDLING |

---

## 6. 离线分析报告使用教程

离线分析链完全独立于实时链路：`generate_report.py` 读取 CSV → pandas 区域统计 + matplotlib 时间趋势图（图表以 base64 直接内嵌）→ 输出 `report.html`（不生成任何额外图片文件）。

### 6.1 输入 CSV 格式（airguard_data.csv）

表头固定 6 字段：

```csv
zoneId,pm25,co2,crowdLevel,status,time
zone-n,32,610,1,正常,2026-10-08T09:00:00+08:00
zone-s,46,830,2,正常,2026-10-08T09:00:00+08:00
zone-w,161,905,3,重度污染,2026-10-08T09:00:00+08:00
```

- 编码支持 utf-8 / gbk（中文 Excel 导出兼容）
- 更换数据的两种方式：**方式1** 用新 CSV 覆盖 `airguard_data.csv` 后重跑；**方式2** 命令行指定：`py generate_report.py 你的文件.csv`

### 6.2 运行与查看

```bash
py generate_report.py
```

**预期现象**：终端打印每个区域的统计结果 → 生成 `report.html` → 浏览器打开即可查看：总体概览卡片、各区域统计表（记录数/PM2.5 均值最大最小/CO₂ 均值最大/人流均值/状态分布）、三张内嵌趋势图（PM2.5、CO₂、人流等级，按区域着色）。

> 报告内容全部由代码计算生成，**更换新 CSV 后重新运行脚本即可得到新报告**，无需也无法手动修改报告结果。

---

## 7. Evidence 证据文件夹说明

`Evidence/` 按模块分目录存放取证材料（截图 + 说明文档）：

| 目录 | 内容 |
|---|---|
| `D1/` | 全区域在线、单区域状态变化截图 + topic 说明 |
| `D2/` | case1/2/3 打分取证截图 + 报告 |
| `D3/` | 事件 OPEN→HANDLING→RECOVERED→回退 OPEN、去重日志、乱序/迟到日志截图 + 状态转移表 |
| `D4/` | Broker 断连（d4_broker_down.png）与恢复（d4_broker_recovered.png）+ 故障修复说明 |
| `D5/` | 规则/ML 不一致页面、终端脚本输出、一致样本页面截图 + README |
| `E1/` `E2/` `E3/` | 各扩展模块的演示截图 + 说明 |

补充取证时：截图放入对应模块子目录，命名 `功能_场景.png`，并在该目录 README.md 里补一行说明；跨模块联调（如 E3 干预后三端同步）建议同屏多窗口截图。

---

## 8. 常见报错与问题排查

| 现象 | 原因与解决 |
|---|---|
| 页面右上角一直"重连中…/已断开" | ① Mosquitto 未启动：先执行 `mosquitto -c mosquitto.conf -v`；② 配置缺 `allow_anonymous true`（2.x 默认拒绝匿名）；③ 8083 端口未配 `protocol websockets`；④ 换电脑后 IP 变了：页面与脚本里的候选地址（`10.102.128.15`）需改成新 IP 或 `127.0.0.1` |
| 页面报"mqtt.js CDN 加载失败" | 网络不通或 CDN 被墙：检查联网；主源 jsdelivr 失败会自动回退 unpkg |
| 脚本报"所有候选地址均连接失败" | Broker 未启动或 WebSocket 监听未配置，按上一条排查；`--host` 参数可指定地址（逗号分隔依次尝试） |
| `AssertionError: 非法 zoneId` | 报文 zoneId 不在 `ZONE_NAMES`（zone-n/zone-s/zone-w）内，修改脚本传入的 zone |
| 页面日志出现"重复消息丢弃"而你没想发重复 | 同一 zone 两条不同消息的 time 相差 <1 秒被去重误杀：保证同 zone 消息间隔 ≥1 秒 |
| 交互场景（late/worsen/improve/recover/demo/d3_case_improve/d3_case_worsen）没有按预期流转 | 这些场景需要**先按脚本提示在页面点击【执行干预】按钮，再回终端按回车**继续；且演示前先 F5 刷新页面（页面状态在内存中，刷新即重置） |
| 单场景脚本发完消息但页面没变化 | 页面可能是在脚本之后打开的（单次消息无保留）：先开页面再跑脚本；日常演示直接用 `stream` 持续上报 |
| E2 摄像头授权被拒（NotAllowedError） | file:// 下个别环境策略拒绝：在项目目录运行 `py -m http.server 8000`，浏览器访问 `http://localhost:8000/multimodal/` 重试；摄像头不可用时语音与 MQTT 功能不受影响 |
| E2 语音识别报 network 错误 / 无反应 | 云端识别依赖 Google 服务（大陆网络可能不通）：优先使用 Chrome 139+ 本地识别；或直接使用页面上的**手动按钮**（切区/朗读/拍照），功能等价 |
| E2 语音播报无声 | 系统未安装中文语音包（Windows 一般自带 Microsoft Huihui）；页面能力栏会提示，安装任意 zh-CN TTS 语音即可 |
| D5 脚本报 `ModuleNotFoundError: sklearn` | 执行 `py -m pip install scikit-learn` |
| generate_report.py 报"CSV 缺少必需字段/编码无法识别" | 检查表头是否为 `zoneId,pm25,co2,crowdLevel,status,time`；文件另存为 utf-8 或 gbk |
| 手机真机打不开 mobile 页面 | file:// 无法跨设备：手机与电脑同一局域网，电脑执行 `py -m http.server 8000`（mosquitto.conf 的 listener 需绑定 0.0.0.0），手机浏览器访问 `http://电脑IP:8000/mobile/` |

---

*本 README 与代码保持同步；任何配置或模块变更后请同步更新本文档。*

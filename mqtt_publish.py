#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 AirGuard MQTT 模拟发布脚本（MQTT over WebSocket）
 与 index.html 监测台配套：订阅/发布 topic 均为 airguard/zones/all。

 【安装依赖】（paho-mqtt 的 WebSocket 传输需要 websockets 包）
     py -m pip install paho-mqtt websockets

 【mosquitto.conf 最小配置】（保存为 mosquitto.conf 后执行 mosquitto -c mosquitto.conf -v）
     allow_anonymous true          # 2.x 默认拒绝匿名，必须显式开启
     listener 1883 127.0.0.1      # 普通 MQTT（TCP）
     listener 8083 127.0.0.1      # MQTT over WebSocket（本脚本连接端口）
     protocol websockets          # 作用于上方最近的 listener(8083)
     # 建议使用 Mosquitto 2.1+（2.0.x 的 websocket listener 有已知缺陷）

 【启动顺序】
     1. 启动 mosquitto  ->  2. 打开 index.html  ->  3. 运行本脚本

 【报文约定】
     - 统一 JSON：{"zoneId","pm25","co2","crowdLevel":0-3,"status":"","time":ISO}
     - status 恒为空字符串：状态必须由监测台代码按顺序计算，禁止硬编码
     - 可选扩展：crowdSource="model" 时附加 confidence 字段（D2 需人工复核演示）
     - 铁律：同一 zone 任意两条【不同】消息的 time 必须相差 >= 1 秒
       （秒精度时间戳 + zoneId|time 去重哈希，同秒第二条会被去重误杀）

 【场景】
     normal      正常消息         D1 显示【正常】，无事件
     abnormal    异常消息         新建事件 OPEN（D3 规则1）
     duplicate   重复消息         同 zoneId+time 连发两次，第二次被去重丢弃
     outoforder  乱序时间消息      先发 t2 再发 t1，监测台按 time 排序处理
     late        迟到旧时间消息    事件恢复后发更早时间戳的异常消息，仅追加历史
     worsen      恶化回退          HANDLING 后收到更严重数据 → 回退 OPEN
     improve     好转仍异常        HANDLING 后收到较轻但仍异常数据 → 保持 HANDLING
     recover     恢复流程          HANDLING 后收到正常数据 → RECOVERED
     model       模型低置信度      D2 标记【需人工复核】，crowdLevel 不计分
     stream      持续上报         模拟真实传感器周期性上报（Ctrl+C 停止）
     demo        全流程演示        异常→干预→好转→恶化→干预→恢复→迟到→复核
     d2_case1    D2取证·用例1     一次性发送 zone-n/s/w 三区域报文后退出
     d2_case2    D2取证·用例2     一次性发送 zone-n/s/w 三区域报文后退出
     d2_case3    D2取证·用例3     手动JSON+crowdSource/confidence，一次性发送后退出

 【提示】每个场景演示前建议刷新监测台页面（F5）以获得确定性结果。
     持续演示推荐: py mqtt_publish.py stream --all-zones （Ctrl+C 停止）
=====================================================================
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

# ---------------- 连接配置（与 index.html 保持一致） ----------------
BROKER_HOSTS = ["10.102.128.15", "127.0.0.1"]  # 依次尝试：本机 IPv4 → 回环地址
BROKER_PORT = 8083            # Mosquitto WebSocket 监听端口
WS_PATH = "/mqtt"             # 与监测台约定的 WebSocket 路径
TOPIC = "airguard/zones/all"  # 固定 topic

# 固定三区域；键用于 --zone 参数校验
ZONE_NAMES = {"zone-n": "宿舍区", "zone-s": "教学区", "zone-w": "食堂区"}


def now_iso(offset_sec=0.0):
    """当前时间 + 偏移(秒) 的带时区 ISO 字符串（秒精度）。
    偏移用于构造乱序/迟到时间戳，保证同 zone 消息 time 相差 >= 1 秒。"""
    return (datetime.now().astimezone() + timedelta(seconds=offset_sec)).isoformat(timespec="seconds")


def build_msg(zone_id, pm25, co2, crowd_level, offset_sec=0.0,
              crowd_source=None, confidence=None):
    """构造统一 JSON 报文。

    status 恒为空字符串 ""：状态必须由监测台代码按顺序计算，禁止在报文中硬编码。
    crowd_source == "model" 时附加 crowdSource/confidence（模拟模型人流检测），
    否则不携带这两个字段（视为传感器数据，正常参与 D2 打分）。
    """
    assert zone_id in ZONE_NAMES, "非法 zoneId: %s" % zone_id
    assert 0 <= crowd_level <= 3, "crowdLevel 必须在 0-3"
    msg = {
        "zoneId": zone_id,
        "pm25": pm25,
        "co2": co2,
        "crowdLevel": crowd_level,
        "status": "",                 # 恒为空，由监测台计算
        "time": now_iso(offset_sec),
    }
    if crowd_source == "model":
        msg["crowdSource"] = "model"
        msg["confidence"] = confidence
    return msg


def on_connect(client, userdata, flags, reason_code, properties):
    """paho-mqtt 2.x 回调（VERSION2 五参数签名）"""
    if reason_code.is_failure:
        print("[MQTT] 连接失败: %s（请确认 mosquitto 已按注释配置 WebSocket 监听并启动）" % reason_code)
    else:
        print("[MQTT] 已连接 broker (WebSocket)")


def make_client(client_id):
    """创建 WebSocket 传输的 MQTT 客户端（paho-mqtt 2.x API）"""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=client_id, transport="websockets")
    client.ws_set_options(path=WS_PATH)   # 必须在 connect 之前设置 WebSocket 路径
    client.on_connect = on_connect
    return client


def connect_and_start(client, args):
    """依次尝试候选地址连接 broker 并启动网络线程。
    同机直连下，未监听的地址会立即被拒绝（RST），失败切换代价很小。"""
    hosts = [h.strip() for h in args.host.split(",") if h.strip()]
    last_err = None
    for host in hosts:
        print("[MQTT] 尝试连接 ws://%s:%d%s …" % (host, args.port, WS_PATH))
        try:
            client.connect(host, args.port, keepalive=60)
        except Exception as e:
            last_err = e
            print("[MQTT] 连接 %s 失败: %s，尝试下一个候选" % (host, e))
            continue
        client.loop_start()
        time.sleep(1.0)   # 等待连接回调
        return
    print("[MQTT] 所有候选地址均连接失败: %s" % last_err)
    print("[MQTT] 请确认 mosquitto 已启动且配置了 WebSocket 监听（mosquitto -c mosquitto.conf -v）")
    raise SystemExit(1)


def send(client, msg, expect="", gap=0.3):
    """发布一条报文并打印原文与预期监测台表现。
    gap: 发送后停顿秒数；乱序场景需要小于监测台 300ms 收集窗口，故可传更小值。"""
    payload = json.dumps(msg, ensure_ascii=False)
    client.publish(TOPIC, payload, qos=0)
    print("[发送] %s" % payload)
    if expect:
        print("   → 预期监测台表现: %s" % expect)
    time.sleep(gap)


def wait_enter(prompt):
    """交互等待：演示者先在监测台点击按钮，再回到本脚本按回车继续"""
    try:
        input("\n>>> %s 【完成后按回车继续】\n" % prompt)
    except (KeyboardInterrupt, EOFError):
        print("\n已中断")
        sys.exit(0)


def banner(title, lines):
    """打印场景横幅"""
    print("\n" + "=" * 64)
    print("场景: %s" % title)
    for ln in lines:
        print("  " + ln)
    print("=" * 64)


# ---------------- 各演示场景（函数拆分） ----------------

def run_normal(client, args):
    """正常消息：D1 显示【正常】，无事件产生"""
    banner("normal 正常消息", ["所有指标均在正常范围，状态由监测台按顺序计算为【正常】"])
    send(client, build_msg(args.zone, 30, 600, 1),
         "D1 卡片显示【正常】，D3 无新事件")


def run_abnormal(client, args):
    """异常消息：PM2.5>150 重度污染 → 新建 OPEN 事件"""
    banner("abnormal 异常消息", ["PM2.5=180 > 150 → 状态【重度污染】"])
    send(client, build_msg(args.zone, 180, 700, 3),
         "D1 显示【重度污染】，D3 新建事件且状态=OPEN（规则1）")


def run_duplicate(client, args):
    """重复消息：同 zoneId+time 连发两次，第二次被去重丢弃"""
    banner("duplicate 重复消息", ["同一条报文（zoneId+time 完全相同）连发两次"])
    m = build_msg(args.zone, 100, 900, 2)   # 轻度污染
    send(client, m, "新建事件 OPEN（若该 zone 已有活动事件则按状态机更新）")
    send(client, dict(m), "同 zoneId+time 重复报文 → 监测台日志显示【重复消息丢弃】，状态不变",
         gap=0.2)


def run_outoforder(client, args):
    """乱序时间消息：先发时间戳更晚(t2)的异常消息，再发更早(t1)的正常消息。
    监测台内部按 time 排序：先处理 t1(正常) 再处理 t2(异常，新建 OPEN)。
    时间戳用未来偏移（+5s/+10s），保证两条消息都是"新消息"而非迟到消息。"""
    banner("outoforder 乱序时间消息",
           ["先发 t2=now+10s（异常 重度），0.1s 后发 t1=now+5s（正常）",
            "预期：监测台日志显示【乱序重排】，按 time 排序后先处理 t1 再处理 t2",
            "最终 D1 显示 t2 的重度污染数据，事件 startTime=t2 时刻，状态=OPEN"])
    m2 = build_msg(args.zone, 180, 700, 3, offset_sec=10)   # t2 异常（先发）
    m1 = build_msg(args.zone, 30, 600, 0, offset_sec=5)     # t1 正常（后发）
    send(client, m2, "t2(异常) 先到达，等待排序", gap=0.1)   # 间隔 < 300ms 收集窗口
    send(client, m1, "t1(正常) 后到达 → 排序后先处理 t1，再以 t2 新建 OPEN 事件")


def run_late(client, args):
    """迟到旧时间消息：事件恢复后，发时间戳介于事件创建与恢复之间的异常消息。
    预期：事件状态保持 RECOVERED 不变，该消息仅追加到 verifyDataList 历史。"""
    banner("late 迟到旧时间消息",
           ["① 轻度污染(now) → 新建 OPEN", "② 你点击【执行干预】→ HANDLING",
            "③ 正常(now+30s) → RECOVERED", "④ 重度污染(now+15s，时间戳早于恢复时间) → 迟到消息"])
    m1 = build_msg(args.zone, 100, 900, 2, offset_sec=0)    # 轻度 → OPEN
    send(client, m1, "新建事件 OPEN")
    wait_enter("在监测台 D3 面板点击该事件行的【执行干预】按钮（OPEN→HANDLING）")
    m2 = build_msg(args.zone, 30, 600, 0, offset_sec=30)    # 正常 → RECOVERED
    send(client, m2, "收到后续新数据满足恢复条件：HANDLING→RECOVERED，recoverTime 被记录")
    m3 = build_msg(args.zone, 180, 800, 3, offset_sec=15)   # 时间戳介于 m1、m2 之间
    send(client, m3, "迟到旧消息 → 状态保持 RECOVERED 不变，仅追加到 verifyDataList 历史（规则5）")


def run_worsen(client, args):
    """恶化回退：HANDLING 后收到更严重数据（严重度>=创建时）→ 回退 OPEN"""
    banner("worsen 恶化回退",
           ["① 轻度污染(严重度3) → OPEN", "② 点击【执行干预】→ HANDLING",
            "③ 重度污染(严重度4 >= 创建时3) → 回退 OPEN（规则4，等重也算恶化）"])
    m1 = build_msg(args.zone, 100, 900, 2, offset_sec=0)
    send(client, m1, "新建事件 OPEN（轻度污染）")
    wait_enter("点击【执行干预】按钮（OPEN→HANDLING）")
    m2 = build_msg(args.zone, 180, 900, 3, offset_sec=30)
    send(client, m2, "指标恶化（严重度4>=创建时3）：HANDLING→OPEN，problemDesc 更新")


def run_improve(client, args):
    """好转仍异常：HANDLING 后收到较轻但仍异常数据 → 保持 HANDLING"""
    banner("improve 好转仍异常",
           ["① 重度污染(严重度4) → OPEN", "② 点击【执行干预】→ HANDLING",
            "③ 轻度污染(严重度3 < 创建时4，好转但仍异常) → 保持 HANDLING"])
    m1 = build_msg(args.zone, 180, 700, 3, offset_sec=0)
    send(client, m1, "新建事件 OPEN（重度污染）")
    wait_enter("点击【执行干预】按钮（OPEN→HANDLING）")
    m2 = build_msg(args.zone, 100, 900, 2, offset_sec=30)
    send(client, m2, "好转但仍异常（严重度3<创建时4）：保持 HANDLING，verifyDataList 追加好转快照")


def run_recover(client, args):
    """恢复流程：HANDLING 后收到正常数据 → RECOVERED"""
    banner("recover 恢复流程",
           ["① 轻度污染 → OPEN", "② 点击【执行干预】→ HANDLING",
            "③ 正常(now+30s) → RECOVERED，recoverTime=该消息时间"])
    m1 = build_msg(args.zone, 100, 900, 2, offset_sec=0)
    send(client, m1, "新建事件 OPEN（轻度污染）")
    wait_enter("点击【执行干预】按钮（OPEN→HANDLING）")
    m2 = build_msg(args.zone, 30, 600, 0, offset_sec=30)
    send(client, m2, "HANDLING→RECOVERED（规则3：仅后续新MQTT数据可恢复，按钮无法直达）")


def run_model(client, args):
    """模型低置信度：D2 标记【需人工复核】，crowdLevel 不参与打分"""
    banner("model 模型低置信度（D2 需人工复核）",
           ["① 正常数据 + crowdSource=model, confidence=0.55 → 需人工复核，crowdLevel 不计分",
            "② 正常数据 + crowdSource=model, confidence=0.95 → 置信度达标，正常计分"])
    m1 = build_msg(args.zone, 30, 600, 3, offset_sec=0,
                   crowd_source="model", confidence=0.55)
    send(client, m1, "D2 面板标记【需人工复核】并给出理由，score=环境权重1×2=2（crowdLevel=3 不计分）")
    m2 = build_msg(args.zone, 30, 600, 3, offset_sec=5,
                   crowd_source="model", confidence=0.95)
    send(client, m2, "confidence=0.95≥0.6 → 复核标记消失，score=1×2+3=5")


def run_demo(client, args):
    """全流程演示：异常→干预→好转→恶化→干预→恢复→迟到→低置信度复核"""
    banner("demo 全流程演示（%s）" % ZONE_NAMES[args.zone],
           ["① 重度污染 → OPEN       ② 点击【执行干预】→ HANDLING",
            "③ 轻度污染 → 好转仍异常，保持 HANDLING",
            "④ 重度污染 → 恶化回退 OPEN  ⑤ 再次点击【执行干预】→ HANDLING",
            "⑥ 正常 → RECOVERED      ⑦ 迟到旧消息 → 仅追加历史，状态不变",
            "⑧ 低置信度模型消息 → D2 标记【需人工复核】"])
    z = args.zone
    m = build_msg(z, 180, 700, 3, offset_sec=0)    # ① 重度 → OPEN
    send(client, m, "新建事件 OPEN（重度污染）")
    wait_enter("点击该事件行的【执行干预】按钮（OPEN→HANDLING）")
    m = build_msg(z, 100, 900, 2, offset_sec=30)   # ③ 轻度 → 好转仍异常
    send(client, m, "好转但仍异常（严重度3<创建时4）：保持 HANDLING")
    m = build_msg(z, 180, 700, 3, offset_sec=60)   # ④ 重度 → 恶化回退
    send(client, m, "指标恶化（严重度4>=创建时4）：HANDLING→OPEN")
    wait_enter("再次点击【执行干预】按钮（OPEN→HANDLING）")
    m = build_msg(z, 30, 600, 0, offset_sec=90)    # ⑥ 正常 → RECOVERED
    send(client, m, "HANDLING→RECOVERED，recoverTime 被记录")
    m = build_msg(z, 180, 700, 3, offset_sec=75)   # ⑦ 迟到旧消息（介于60s与90s之间）
    send(client, m, "迟到旧消息 → 状态保持 RECOVERED 不变，仅追加到 verifyDataList 历史（规则5）")
    m = build_msg(z, 30, 600, 3, offset_sec=120,   # ⑧ 低置信度模型消息
                  crowd_source="model", confidence=0.55)
    send(client, m, "D2 面板标记【需人工复核】，crowdLevel 不计分")


def walk(prev, spread, low, high):
    """随机游走并夹在 [low, high] 区间（模拟真实传感器缓慢波动）"""
    return max(low, min(high, prev + random.uniform(-spread, spread)))


def run_stream(client, args):
    """持续上报：模拟真实传感器周期性上报（Ctrl+C 停止）。
    每周期每个 zone 一条报文，指标在正常范围随机游走，约 12% 概率注入异常
    （重度/轻度污染/通风不足），下一周期自动回落到正常基线，
    页面 D1 状态与 D3 事件状态机实时联动。"""
    interval = max(1.0, args.interval)
    if interval != args.interval:
        print("[警告] interval 已从 %s 秒调整为 1 秒（秒级时间戳，间隔过小会被去重误杀）" % args.interval)
    zones = list(ZONE_NAMES.keys()) if args.all_zones else [args.zone]
    # 每个 zone 独立的传感器模拟状态（随机游走互不串扰，体现区域隔离）
    sensors = {z: {"pm25": 35.0, "co2": 650.0} for z in zones}
    banner("stream 持续上报",
           ["模拟真实传感器每 %s 秒周期性上报（Ctrl+C 停止）" % interval,
            "区域: %s" % ("全部三区域" if args.all_zones else "%s(%s)" % (args.zone, ZONE_NAMES[args.zone])),
            "指标随机游走 + 约 12%% 概率注入异常，下一周期自动回落正常基线",
            "期间可在页面点击【执行干预】，观察 OPEN→HANDLING→RECOVERED 实时联动"])
    print("[提示] 持续上报中，Ctrl+C 停止。页面 D1/D2/D3 应随每轮数据实时刷新。")
    tick = 0
    try:
        while True:
            tick += 1
            print("\n----- 第 %d 轮 -----" % tick)
            for z in zones:
                s = sensors[z]
                s["pm25"] = walk(s["pm25"], 4.0, 0, 300)
                s["co2"] = walk(s["co2"], 30.0, 300, 3000)
                crowd = random.randint(0, 3)
                pm25, co2 = s["pm25"], s["co2"]
                # 约 12% 概率注入异常，让状态计算与事件状态机有可见变化
                r = random.random()
                injected = False
                if r < 0.04:
                    pm25 = random.uniform(151, 260)      # 重度污染
                    injected = True
                elif r < 0.08:
                    pm25 = random.uniform(76, 150)       # 轻度污染
                    injected = True
                elif r < 0.12:
                    co2 = random.uniform(1500, 2600)     # 通风不足风险
                    injected = True
                msg = build_msg(z, round(pm25, 1), round(co2, 1), crowd)
                send(client, msg, gap=0.05)
                # 注入异常后回落到正常基线，形成"异常→正常"闭环（配合干预可演示恢复）
                if injected:
                    s["pm25"], s["co2"] = 35.0, 650.0
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止持续上报（Ctrl+C）")


# ---------------- D2 测试取证场景（一次性发送，发送完成即退出） ----------------
# 说明：使用真实三区域 zone-n/zone-s/zone-w（均为 ZONE_NAMES 合法值，
# 满足 build_msg 断言）；监测台可正常接收并实时展示 D2 打分结果。


def d2_case1(client, args):
    """D2 测试取证·用例1：三区域梯度打分（正常/正常/重度污染），一次性发送后退出"""
    banner("d2_case1 D2 测试取证·用例1",
           ["zone-n: pm25=35, co2=600, crowdLevel=0（正常）",
            "zone-s: pm25=35, co2=920, crowdLevel=1（正常）",
            "zone-w: pm25=160, co2=900, crowdLevel=2（重度污染）",
            "一次性发送全部 3 条报文后脚本结束"])
    send(client, build_msg("zone-n", 35, 600, 0))
    send(client, build_msg("zone-s", 35, 920, 1))
    send(client, build_msg("zone-w", 160, 900, 2))
    print("\nd2_case1 已发送全部 3 条报文（zone-n/zone-s/zone-w），脚本结束。")


def d2_case2(client, args):
    """D2 测试取证·用例2：三区域梯度打分（正常/重度污染/正常），一次性发送后退出"""
    banner("d2_case2 D2 测试取证·用例2",
           ["zone-n: pm25=35, co2=600, crowdLevel=0（正常）",
            "zone-s: pm25=170, co2=950, crowdLevel=3（重度污染）",
            "zone-w: pm25=38, co2=620, crowdLevel=1（正常）",
            "一次性发送全部 3 条报文后脚本结束"])
    send(client, build_msg("zone-n", 35, 600, 0))
    send(client, build_msg("zone-s", 170, 950, 3))
    send(client, build_msg("zone-w", 38, 620, 1))
    print("\nd2_case2 已发送全部 3 条报文（zone-n/zone-s/zone-w），脚本结束。")


def d2_case3(client, args):
    """D2 测试取证·用例3：人工复核（crowdSource/confidence），一次性发送后退出。
    约束：不调用 build_msg，手动构造完整统一 JSON 报文（额外带 crowdSource/confidence）。"""
    banner("d2_case3 D2 测试取证·用例3",
           ["zone-n: pm25=35, co2=600, crowdLevel=0, crowdSource=simulated, confidence=1.0",
            "zone-s: pm25=40, co2=650, crowdLevel=1, crowdSource=simulated, confidence=1.0",
            "zone-w: pm25=150, co2=880, crowdLevel=3, crowdSource=model, confidence=0.4（需人工复核）",
            "一次性发送全部 3 条报文后脚本结束"])
    # 手动构造完整统一 JSON：status 恒为空字符串（由监测台代码计算，禁止硬编码）
    msgs = [
        {"zoneId": "zone-n", "pm25": 35, "co2": 600, "crowdLevel": 0,
         "status": "", "time": now_iso(0),
         "crowdSource": "simulated", "confidence": 1.0},
        {"zoneId": "zone-s", "pm25": 40, "co2": 650, "crowdLevel": 1,
         "status": "", "time": now_iso(1),
         "crowdSource": "simulated", "confidence": 1.0},
        {"zoneId": "zone-w", "pm25": 150, "co2": 880, "crowdLevel": 3,
         "status": "", "time": now_iso(2),
         "crowdSource": "model", "confidence": 0.4},
    ]
    for m in msgs:
        send(client, m)
    print("\nd2_case3 已发送全部 3 条报文（zone-n/zone-s/zone-w），脚本结束。")


# 场景注册表：argparse choices 与分发
SCENARIOS = {
    "normal": run_normal,
    "abnormal": run_abnormal,
    "duplicate": run_duplicate,
    "outoforder": run_outoforder,
    "late": run_late,
    "worsen": run_worsen,
    "improve": run_improve,
    "recover": run_recover,
    "model": run_model,
    "stream": run_stream,
    "demo": run_demo,
    "d2_case1": d2_case1,
    "d2_case2": d2_case2,
    "d2_case3": d2_case3,
}



def main():
    parser = argparse.ArgumentParser(
        description="AirGuard MQTT 模拟发布脚本（MQTT over WebSocket，topic=%s）" % TOPIC,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: py mqtt_publish.py demo --zone zone-n\n"
               "      py mqtt_publish.py stream --all-zones\n"
               "提示: 每个场景演示前建议刷新监测台页面(F5)以获得确定性结果。")
    parser.add_argument("scenario", choices=sorted(SCENARIOS.keys()),
                        help="演示场景（normal/abnormal/duplicate/outoforder/late/"
                             "worsen/improve/recover/model/stream/demo/"
                             "d2_case1/d2_case2/d2_case3）")
    parser.add_argument("--zone", default="zone-n", choices=sorted(ZONE_NAMES.keys()),
                        help="目标区域（默认 zone-n 宿舍区）")
    parser.add_argument("--all-zones", action="store_true",
                        help="stream 场景：同时上报全部三个区域（默认仅 --zone 指定区域）")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="stream 场景：上报周期秒数（默认 5；必须 >= 1，过小会被去重误杀）")
    parser.add_argument("--host", default=",".join(BROKER_HOSTS),
                        help="broker 地址，逗号分隔依次尝试（默认 %s）" % ",".join(BROKER_HOSTS))
    parser.add_argument("--port", type=int, default=BROKER_PORT, help="WebSocket 端口（默认 %d）" % BROKER_PORT)
    args = parser.parse_args()

    if args.scenario == "stream" and args.all_zones:
        zone_desc = "全部三区域"
    else:
        zone_desc = "%s(%s)" % (args.zone, ZONE_NAMES[args.zone])
    print("\nAirGuard 模拟发布脚本 | zone=%s | topic=%s" % (zone_desc, TOPIC))
    print("提示: 每个场景演示前建议刷新监测台页面(F5)以获得确定性结果。")

    client = make_client("airguard-sim-" + str(random.randint(1000, 9999)))
    connect_and_start(client, args)

    try:
        SCENARIOS[args.scenario](client, args)
        time.sleep(0.5)   # 等待最后一条报文送达
    finally:
        client.loop_stop()
        client.disconnect()
        print("\n已断开 broker，场景结束。")


if __name__ == "__main__":
    main()

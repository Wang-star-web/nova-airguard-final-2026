#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 AirGuard D5 — 固定规则 vs ML(IsolationForest) 组合裁决
=====================================================================

【任务目标】
  用 sklearn IsolationForest（输入特征 pm25、co2）对空气质量数据做异常检测，
  与 D1 固定判级规则进行对比，并实现【代码内写死的规则优先组合裁决策略】，
  输出「固定规则结果 / ML判断结果 / 最终组合裁决结论」三项。

【依赖安装】
  py -m pip install scikit-learn
  （MQTT 推送需 paho-mqtt + websockets：py -m pip install paho-mqtt websockets；
    未安装或 broker 不可达时仅打印警告，控制台输出不受影响，脚本仍可独立运行）

【运行方式】
  py d5_ml_compare.py        （无需任何手动配置）
  运行后：
  1. 控制台依次打印 4 个构造测试样本的 输入 | 固定规则结果 | ML判断结果 | 最终组合裁决
  2. 尽力将结果推送到 MQTT 主题 airguard/zones/d5（index.html 的 D5 区域自动刷新）

【固定判级规则（与前端 computeStatus 完全一致，禁止修改）】
  ① pm25 > 150        → 重度污染
  ② 否则 pm25 > 75    → 轻度污染
  ③ 否则 co2 >= 1500  → 通风不足风险
  ④ 其余             → 正常

【组合裁决策略（写死在代码内：规则优先）】
  - 固定规则判为异常（非正常）→ 以固定规则结果为准，忽略 ML 判断
  - 固定规则正常 + ML 判为异常偏离历史分布 → 提示"建议人工关注"
  - 两者均正常 → 正常（一致）
  更换输入数据，最终裁决输出必须跟随变化（见 combine_decision）。

【训练样本构造（内置，确定性随机种子 42）】
  - 清洁簇（30 条）：pm25∈[20,80]、co2∈[400,1200] —— 校园历史常见空气质量
  - 污染簇（12 条）：pm25∈[120,200]、co2∈[400,900] —— 雾霾期历史数据
  两簇并存使模型"见过"污染分布，从而能构造出双向不一致的测试样本。
=====================================================================
"""

import sys
import time
from datetime import datetime

import numpy as np
from sklearn.ensemble import IsolationForest

# ---------------- 内置训练样本（确定性构造） ----------------
np.random.seed(42)
_clean = np.column_stack([
    np.round(np.random.uniform(20, 80, 30), 1),     # 清洁簇 pm25
    np.round(np.random.uniform(400, 1200, 30), 1),  # 清洁簇 co2
])
_polluted = np.column_stack([
    np.round(np.random.uniform(120, 200, 12), 1),   # 污染簇 pm25（雾霾期）
    np.round(np.random.uniform(400, 900, 12), 1),   # 污染簇 co2
])
X_TRAIN = np.vstack([_clean, _polluted])

# ---------------- 构造测试样本（刻意制造规则与ML不一致） ----------------
TEST_SAMPLES = [
    # 样本A：pm25=72<=75 且 co2=1490<1500 → 规则=正常；
    #        但双特征贴阈值、落在两簇之间稀疏区 → ML=异常。构造不一致①
    {"name": "A", "pm25": 72.0, "co2": 1490.0,
     "note": "构造不一致：固定规则=正常，ML=异常"},
    # 样本B：pm25=155>150 → 规则=重度；但落入训练污染簇内部 → ML=正常。构造不一致②
    {"name": "B", "pm25": 155.0, "co2": 550.0,
     "note": "构造不一致：固定规则=重度，ML=正常"},
    # 样本C：清洁簇核心，两者均正常 → 一致
    {"name": "C", "pm25": 40.0, "co2": 700.0,
     "note": "一致样本：固定规则=正常，ML=正常"},
    # 样本D：严重超标且远离一切历史分布 → 规则=重度、ML=异常 → 一致
    {"name": "D", "pm25": 260.0, "co2": 2200.0,
     "note": "一致样本：固定规则=重度，ML=异常"},
]

# ---------------- MQTT 推送配置（可选，失败不影响独立运行） ----------------
D5_TOPIC = "airguard/zones/d5"
BROKER_HOSTS = ["10.102.128.15", "127.0.0.1"]
BROKER_PORT = 8083
WS_PATH = "/mqtt"


def rule_judge(pm25, co2):
    """固定规则判级（顺序严格，与前端 computeStatus 一致，禁止修改）"""
    if pm25 > 150:
        return "重度污染"
    if pm25 > 75:
        return "轻度污染"
    if co2 >= 1500:
        return "通风不足风险"
    return "正常"


_model = None


def train_model():
    """用内置训练样本训练 IsolationForest（确定性 random_state=42）"""
    global _model
    _model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
    _model.fit(X_TRAIN)


def ml_judge(pm25, co2):
    """ML 判断：1=正常（符合历史分布）/ -1=异常（偏离历史分布）"""
    pred = _model.predict([[pm25, co2]])[0]
    return "正常（符合历史分布）" if pred == 1 else "异常（偏离历史分布）"


def combine_decision(rule_result, ml_result):
    """组合裁决策略（写死在代码内）：规则优先。
    - 固定规则判为异常（非正常）→ 以固定规则结果为准，无论 ML 说什么；
    - 固定规则正常 + ML 异常 → 正常（规则）但偏离历史分布，建议人工关注；
    - 两者均正常 → 正常（一致）。
    更换输入数据，最终裁决输出必须跟随变化。"""
    if rule_result != "正常":
        return rule_result + "（规则优先：以固定规则异常判定为准）"
    if "异常" in ml_result:
        return "正常（规则）但 ML 检测偏离历史分布，建议人工关注"
    return "正常（固定规则与 ML 判断一致）"


# ---------------- MQTT 推送（尽力而为，失败仅警告） ----------------
def try_connect_mqtt():
    """尝试连接本地 broker（WebSocket）；不可达返回 None，不影响脚本独立运行"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[MQTT] 未安装 paho-mqtt，跳过推送（控制台输出不受影响；安装：py -m pip install paho-mqtt websockets）")
        return None
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="airguard-d5-" + str(int(time.time()) % 100000),
                         transport="websockets")
    client.ws_set_options(path=WS_PATH)
    for host in BROKER_HOSTS:
        try:
            client.connect(host, BROKER_PORT, keepalive=60)
            client.loop_start()
            time.sleep(0.8)
            print("[MQTT] 已连接 broker，结果将推送到 %s" % D5_TOPIC)
            return client
        except Exception as e:
            print("[MQTT] 连接 %s 失败: %s" % (host, e))
    print("[MQTT] broker 不可达，跳过推送（控制台输出不受影响）")
    return None


def publish_result(client, sample, rule_result, ml_result, final_decision):
    """发布一条 D5 结果报文（index.html 的 D5 区域收到后自动刷新）"""
    if client is None:
        return
    payload = {
        "pm25": sample["pm25"],
        "co2": sample["co2"],
        "ruleResult": rule_result,
        "mlResult": ml_result,
        "finalDecision": final_decision,
        "sample": sample["name"],
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    import json
    client.publish(D5_TOPIC, json.dumps(payload, ensure_ascii=False), qos=0)


def main():
    print("=" * 72)
    print("AirGuard D5 — 固定规则 vs ML(IsolationForest) 组合裁决")
    print("=" * 72)

    # 1. 训练模型（内置样本）
    train_model()
    print("[模型] IsolationForest | 输入特征: pm25, co2 | n_estimators=100")
    print("       contamination=0.05 | random_state=42 | 内置训练样本: %d 条" % len(X_TRAIN))
    print("       （清洁簇 %d 条 + 污染簇 %d 条，模型可同时容纳两类历史分布）" %
          (len(_clean), len(_polluted)))
    print("[固定规则] 1.pm25>150→重度污染 2.pm25>75→轻度污染 3.co2>=1500→通风不足风险 4.其余→正常")
    print("[组合策略] 规则优先：规则异常→以规则为准；规则正常+ML异常→建议人工关注；双正常→一致")

    # 2. MQTT 连接（尽力而为）
    client = try_connect_mqtt()

    # 3. 逐样本执行：固定规则 → ML → 组合裁决 → 控制台打印 → MQTT 推送
    try:
        for sample in TEST_SAMPLES:
            pm25, co2 = sample["pm25"], sample["co2"]
            rule_result = rule_judge(pm25, co2)        # 固定规则
            ml_result = ml_judge(pm25, co2)            # ML 隔离森林
            final_decision = combine_decision(rule_result, ml_result)  # 组合裁决（规则优先）

            print("-" * 72)
            print("样本%s [%s] | 输入: pm25=%s, co2=%s" % (sample["name"], sample["note"], pm25, co2))
            print("  固定规则结果: %s" % rule_result)
            print("  ML判断结果:   %s" % ml_result)
            print("  最终组合裁决: %s" % final_decision)

            publish_result(client, sample, rule_result, ml_result, final_decision)
            if client is not None:
                time.sleep(1.2)   # 间隔推送，页面可看清每次自动刷新
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()
            print("[MQTT] 推送完成，已断开 broker")

    print("=" * 72)
    print("D5 运行结束。可打开 index.html 查看 D5 区域的三项结果自动刷新。")
    print("=" * 72)


if __name__ == "__main__":
    main()

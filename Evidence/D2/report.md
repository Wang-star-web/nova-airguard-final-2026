# D2 环境优先级分析测试报告
## 概述
本任务 D2 实现多宿舍环境指标采集，自动评估环境风险，动态计算风险优先级，对低置信度模型数据触发人工复核。
测试一共 3 组用例，每组发送 3 个区域 MQTT 报文，观察前端页面计算得到的风险优先级结果。

---

## 测试用例 1：d2_case1
### 输入报文
```json
{"zoneId": "zone-n", "pm25": 35, "co2": 600, "crowdLevel": 0}
{"zoneId": "zone-s", "pm25": 35, "co2": 920, "crowdLevel": 1}
{"zoneId": "zone-w", "pm25": 160, "co2": 900, "crowdLevel": 2}
页面输出结果

系统自动计算三个区域环境风险，判定 zone‑w 风险最高，排在优先级第一位。

判断理由

zone‑w 的 PM2.5 达到 160，属于严重污染，相比 zone‑n、zone‑s，环境指标最差，因此系统判定为最高优先级，需要优先处理。

如图 d:\AICording\nova-airguard-final-2026\Evidence\D2\case1.png


测试用例 2：d2_case2

输入报文
{"zoneId": "zone-n", "pm25": 35, "co2": 600, "crowdLevel": 0}
{"zoneId": "zone-s", "pm25": 170, "co2": 950, "crowdLevel": 3}
{"zoneId": "zone-w", "pm25": 38, "co2": 620, "crowdLevel": 1}
页面输出结果

系统重新评估全部区域风险，优先级发生切换，zone‑s 变为最高优先级。

判断理由

zone‑s 的 PM2.5 上升至 170，CO₂ 浓度 950，人员拥挤等级 crowdLevel = 3，多项指标超标，综合风险超过 zone‑w，实现动态优先级切换。

如图 d:\AICording\nova-airguard-final-2026\Evidence\D2\case2.png

测试用例 3：d2_case3

输入报文
{
  "zoneId": "zone-n",
  "pm25": 35,
  "co2": 600,
  "crowdLevel": 0,
  "crowdSource": "simulated",
  "confidence": 1.0
}
{
  "zoneId": "zone-s",
  "pm25": 40,
  "co2": 650,
  "crowdLevel": 1,
  "crowdSource": "simulated",
  "confidence": 1.0
}
{
  "zoneId": "zone-w",
  "pm25": 150,
  "co2": 880,
  "crowdLevel": 3,
  "crowdSource": "model",
  "confidence": 0.4
}
页面输出结果

zone‑w 成功触发页面人工复核提示。

判断理由

该条数据来自机器学习模型预测，confidence = 0.4，模型置信度低。系统不会直接采信该异常，弹出人工复核，等待人工确认数据是否可信。

如图 d:\AICording\nova-airguard-final-2026\Evidence\D2\case3.png

测试结论

三组用例全部验证通过。
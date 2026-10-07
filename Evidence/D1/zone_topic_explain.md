# D1 zoneId & Topic说明
MQTT Broker：127.0.0.1，WebSocket端口：8083
订阅Topic：airguard/zones/all
zone‑n：宿舍区
zone‑s：教学区
zone‑w：食堂区

测试说明：
两张截图取自 demo实时随机数据流，没有写死静态数值。
业务验证：各个zone数据严格隔离，单区域数据更新不会修改其余两个区域UI。
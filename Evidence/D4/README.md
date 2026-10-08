# D4 实时链路故障与修复
本次制造故障：停止MQTT Broker(Mosquitto)

## 故障现象
关闭Mosquitto运行终端之后，Web监测台、移动端、3D数字空间全部断开MQTT连接，不再接收新的环境报文；区域状态不再刷新，页面显示暂无数据。

## 问题定位
MQTT Broker服务停止，WebSocket 8083端口停止服务，所有前端客户端无法维持连接，整条实时数据流中断。

## 修复操作
1. 新开PowerShell终端，进入Mosquitto安装目录
2. 执行命令启动Broker：
```powershell
.\mosquitto.exe -c mosquitto.conf
3. Broker成功监听1883、8083端口。

修复验证

发送MQTT测试报文，zone‑n/zone‑s/zone‑w三个区域正常接收数据，Web、移动端、3D页面同步更新状态，实时链路恢复。

证据截图

• d4_broker_down.png：Broker停止，前端页面链路断开

• d4_broker_recovered.png：重启Broker之后系统业务恢复正常
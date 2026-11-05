# AW1000 参考系统记录

日期：2026-10-26（北京时间）。证据来自用户提供的 aw1000-info.txt，以及随后粘贴的只读电话服务和 AT 查询结果。公开源码不包含真实设备 SN、MAC、电话号码或通话记录。

## 系统边界

参考系统为 Linux 6.12.112、qualcommax/ipq807x，system board 报告 OpenWrt 24.10.5 / QWRT R26.10.25。本工程目标仍为上传的 QWRT 25.12.2、ipq807x/generic、Linux 5.4.213。不能复用参考系统的内核模块、设备名或 GPIO 驱动配置来替代目标适配。

## 分区与序列号

- 参考系统的 0:board_data 分区大小为 1 MiB，物理偏移 0x10B40000；当前设备编号 mtd23。
- 从该分区取得有效 ARC 加 10 位数字的机身 SN；SoC serial_number 是另一种标识，不用于生成密码。
- 当前 rootfs 为 701 MiB，偏移 0x10C40000，当前设备编号 mtd24。该布局不能视为目标 QWRT 运行时一定采用的布局。
- UBI PEB 262144、最小 IO 4096、LEB 253952，参考系统报告 6 个坏块、74 个保留坏块 PEB。目标刷写前仍须按当时布局核对容量。
- 项目仍按分区名查找 SN，不固定 mtd 编号、不扫描整片 NAND。目标 QWRT 若隐藏 board_data，仍回退 password；本版没有以已知偏移增加原始 NAND 读取。

## LED 与按钮

参考系统的 LED 使用 gpio_74x164，名称如 green:power、green:wifi、green:internet、green:phone 和三色 5g/signal。目标 QWRT 后续实测只有 ipq::led0～led12；驱动忽略 DT label，MAX_BLINK_IDX=13。led9 已确认对应机身制式绿灯，其他通道按 DT 索引映射，尚未全部实测。v1.0.9 保留 led1～led12 映射，并把空闲 led0 从物理通道 5 改映射至原电源物理通道 15，使专用服务可以常亮控制电源灯。

目标 QWRT DT 的 wlan / wps 键码分别为 238 / 529，hotplug.json 分发到 `/etc/rc.button/%BUTTON%`。1.1.1 删除重复的 hotplug.d 自定义入口后，真机确认 Wi-Fi 一次按键只执行一次关闭或开启，WPS 一次按键让 ath0/ath1 同时进入 PBC Active；WPS 客户端成功配对仍待反馈。

## AT 与电话

| 项目 | 采集结果 |
| --- | --- |
| QModem 模块 | RG500Q-EA，当前 AT 端口 /dev/ttyUSB3 |
| ubus 对象 | at-daemon 已注册 |
| ubus-at-daemon 服务 | running |
| AT 查询 | success，原始响应含 OK，耗时 6 ms |
| QDAI 查询 | success，+QDAI: x,1,0,4,0,1,2,1,2 |
| fxsd 服务 | running，通过 aw1000-fxsd-run auto 启动，-c 1 |
| 电话状态 | idle，offhook=0 |
| VBAT / VLINE / ILOOP | 56805 mV / -45811 mV / 640 µA |
| 参考电话包 | fxsd 0.2.0-4，kmod-aw1000-fxs 6.12.112+0.1.0-1 |

项目实际 C 解析器将这条 QDAI 识别为 PCM 从模式、16 kHz（qdai_is_slave=1、qdai_rate=1）。v1.0.1 将生成的电话配置 hd 设为 1，避免在该已存在配置上因默认 hd=0 主动调回 8 kHz。完整 JSON 样本保存在 tests/fixtures/at-daemon-qdai.json，回归检查运行实际解析器及 AT 包装器。

参考系统的 fxs.main.hd 未设置，因此采集命令把空 hd= 与下一项 led= 连在一行；该输出不能解释为服务故障。QDAI 的 16 kHz 表示模块 PCM 配置，不是端到端高清通话证明。

这些数据确认参考系统可以查询模块和读取 SLIC 待机线路状态。摘挂机、振铃、呼入/呼出、双向语音、业务开通情况及蜂窝数据并发仍待实际反馈。目标 5.4.213 电话驱动仍需匹配 QSDK 构建环境，deferred 基础版继续关闭 RJ11。


1.0.16：用户报告两个后置按钮均未生效。已使用原 QWRT BusyBox 重现：旧 helper 的 `set -u` 在载入 `/lib/functions.sh` 时触发 `IPKG_INSTROOT: parameter not set`；QCA wifi-utils.sh 也载入同一库。修复 Wi-Fi CLI 操作与 WPS 子 shell 隔离，并补齐实际高通脚本读取的 `wps_pbc`（旧配置只写了 `wps_pushbutton`）。GPIO 2/62 和键码 238/529 与同机型来源一致，保持不改；增加事件和执行状态记录，真实按压/配对仍待反馈。

1.0.17：1.0.16 真机发现 Wi-Fi 运行状态和 UCI disabled 暂时不一致，旧 helper 因而误判方向；关闭时使用 `wifi down` 还可能触发错误回滚和第二次 `wifi up`，表现为灯短灭、无线未关闭及较长 CPU 升高。热修补改读实际 UP 的 ath* AP，并对齐原 rfkill 脚本，只提交配置后执行一次 `wifi up`。真机日志确认 disable/enable 两个方向均为 reload_rc=0、result=matched；WPS 同次采集确认两个 AP 的 PBC Active。

1.1.1：QWRT hotplug.json 对按钮既调用 `/etc/rc.button/%BUTTON%`，也会继续遍历 hotplug.d/button；旧自定义 `50-aw1000-wps` 因而形成第二条入口。删除它后，真机日志确认一次 Wi-Fi 按键只有一条完成记录，关闭约 6 秒、开启约 8 秒；WPS helper 同秒结束并向两个 AP 提交 PBC。短暂的单次 QSDK 无线重建负载仍属预期，不再有第二次自定义重载。

1.1.2：根据现场“拔线能快速转蜂窝，插回时信号灯红闪且长时间仍用蜂窝”的反馈，确认旧配置恢复需 15 轮（约 30 秒），且面板把蜂窝 tracker 过渡状态误当作信号故障。新源码改为 4 轮恢复，将信号灯与线路探测解耦，Internet 灯跟随已发布的实际出口。该逻辑已通过目标运行库和成品回读测试，实际 2–4/6–8 秒时序与灯光表现仍待 AW1000 刷入验收。

1.1.3：按新的现场需求，不再让仅有线接入时的蜂窝故障指示无限红闪。SIM 硬故障立即开始 60 秒红闪；SIM 正常但数据路径持续不可用时，先等待 15 秒以排除正常切换，再红闪 60 秒。超时后信号灯熄灭，蜂窝恢复则立即恢复正常颜色。

物理网口采用 S18 早期改名，保留 dp1–dp4/dp6、PHY 与 MAC，不改 NSS 模块。名称映射和原 UCI 写入、冲突/失败回退经过隔离测试；实际高通数据路径、协商速率及加速仍待验收。

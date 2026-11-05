# Arcadyan AW1000 QWRT 改造工程

当前源码版本 **1.2.8**。基于 QWRT 25.12.2 r47986-d312c785fd、ipq807x/generic、aarch64_cortex-a53、Linux 5.4.213 的原始 factory 镜像重打包，保留原高通内核、无线校准和网络栈。LuCI 固件版本采用北京时间编译日期与工程版本，例如 **QWRT R26.11.5_V1.2.8**。

## 1.2.8 更新电话服务与界面

上游固定到 [f26f9f5a4bfbee391a3a15c9dab886c96d7ca539](https://github.com/Perz3us/openwrt-aw1000-phone/commit/f26f9f5a4bfbee391a3a15c9dab886c96d7ca539)，完整保存并校验 74 个原始文件。fxsd 和 LuCI 更新为 0.7.0。

- 增加定时或全天免打扰、始终允许响铃的号码列表，以及指定分钟内重复来电允许响铃。默认关闭免打扰。
- 修正 FSK 来电显示的编码帧和输出幅度，新增短铃后发送号码（RP-AS），支持按收到的格式或本地拨号格式显示号码。默认按收到的号码显示。
- 振铃时检查话机电流，状态页显示话机已连接、未检测到或尚未检查。没有进行振铃测试前显示尚未检查。
- 同步 SIP 独立模式和开关联动，状态页可关闭 RJ11 口后仅使用 SIP。SIP 默认关闭，基础固件中的 QModem/at-daemon 2.8.10 需要匹配支持 AW1000 的 QModem voice、qmodem-sipd 和 at-daemon >=3.4.0 后才能完整使用。
- 新选项、提示和状态全部汉化。模块日常 AT 查询经共用队列，Phone 灯由 fxsd 管理，默认正常服务绿色常亮。

驱动仍为 0.3.0。上游 9 个内核源文件与 1.2.5 编译基线完全相同，复用已集成的 Linux 5.4.213 模块。构建会核对全部内核源码 SHA256 和驱动包指纹，原驱动 manifest 记录实际编译提交与版本。构建及模拟验证范围见 VALIDATION.md。

## 1.2.7 移除 3proxy

移除原固件中的 `/usr/bin/3proxy` 及其启动项、默认配置、升级保留规则和 opkg 登记。factory.ubi 与 sysupgrade.bin 使用同一份经回读检查的 rootfs。

## 1.2.6 升级收尾优先执行 reboot -f

1.2.6 调整了升级收尾的重启顺序。原有镜像校验、UBI 写入、配置恢复、完成标记和同步结束后，先执行 `reboot -f`；只有命令返回，才等待 5 秒再尝试原有 SysRq，随后可退出 stage2 交给 upgraded 再次请求重启。不会在写入途中强制复位，也没有新增定时强制重启进程。

1.2.5 同版本升级的外部日志已记录到 kernel/rootfs 写入成功、配置恢复成功、最后同步 `rc=0` 和 `reset.sysrq.begin`。日志末尾不能区分“未复位”与“复位后早期启动失败”；1.2.6 修改重启路径后，用户已确认 LuCI 升级能自动重启。重启系统调用若卡住，后续 fallback 无法执行，不能保证看门狗一定恢复。

**从 1.2.5 升到 1.2.6 时，仍由当前运行的 1.2.5 执行升级收尾。启动 1.2.6 后再次通过 LuCI 升级，才会使用本版的 reboot -f 优先逻辑。** 若保留配置中另有旧脚本覆盖 `/lib/upgrade/zz-aw1000.sh`，先与 `/rom/lib/upgrade/zz-aw1000.sh` 的 SHA256 比对。独立 diag1 补丁不会加入本版源码。

## 1.2.5：更新 RJ11 电话

电话上游固定为 **5a5c87471d090f015a0ecaf1f407a8b0082d8e5c**，完整保留 69 个原始文件。同步驱动 **0.3.0**、fxsd **0.5.0**、LuCI **0.5.0**，驱动已用公开 QSDK 与 GCC 7.5.0 重新编译为 Linux 5.4.213 模块，替换旧内置驱动，主 Actions 不联网编译内核。

- 带入上游通话掉线处理、NO CARRIER 事件接收、约 30 秒主循环卡死保护及标准/短铃/双响/自定义振铃节奏。
- 新选项全部汉化；电话页显示实际内置源码版本，继续移除外部项目链接和改造状态页。
- 普通 AT 命令仍由现有 at-daemon 队列处理；新 ubus 事件连接兼容两种本地 socket 路径。模块重启期间仍沿用上游停止/恢复 AT 服务流程。
- 新增 libubus/libubox 依赖以完整固定源码归档并静态编入 fxsd，不替换系统 ubusd、共享库、QModem 或拨号脚本。
- Phone 灯仍由 fxsd 独占，正常服务绿色常亮；默认振铃、摘机也保持常亮，沿用 hd=1 / 16 kHz。
- 系统日志不再输出电话号码和拨号数字，独立通话记录与 CSV 保留号码。通话记录仍存于 /etc/fxs/calls.log，会写存储；本次不改变历史记录策略。
- SIP 桥接代码、音频设备接口及中文设置一并同步，默认关闭。当前基础固件是 QModem/at-daemon 2.8.10，尚不具备上游要求的完整 SIP 条件；启用仍需另外匹配支持 AW1000 的 QModem voice、qmodem-sipd 与 at-daemon >=3.4.0。本版不自动升级整个 QModem 栈，也不开放 SIP 服务端口。

驱动符号检查、构建和模拟回归通过不等于真机加载/通话验收；通话杂音是否改善尚无证据。保留配置升级可能保留已有 fxs 选项，新振铃设置缺省时使用标准节奏。

## 1.2.4：完整集成 modemdata

新增 **移动网络信息**，完整保留模块信息、模块定义、插件配置、诊断、更新与支持五个页面，以及上游图标、后端和中文翻译。前端固定 4IceG/luci-app-modemdata 1.0.32-20260709（862d7385346813bb0a862611f3ebd26659b09dfd），后端固定 obsy/modemdata 20261017-1（fbdc0b6a720647642e99c6fc8db933a767c9ead0）。

默认登记内置 RG500Q-EA，通信端口选择 **自动跟随 QModem**，关联接口 **wwan**。保留用户已定义的模块和手工端口；首次启动遇到未提交设置时延后，不提交用户编辑。插件不负责拨号，QModem、RJ11、DNS/IPv6、线路策略与灯光控制职责保持。

- 所有集成后端的串口读取经现有 at-daemon，不再直接用 sms_tool/comgt 抢串口；分解组合查询，逐条等待队列响应。
- 动态数据使用至少 10 秒共享缓存，身份信息 300 秒；锁、24 秒采集上限、5 秒失败退避，旧动态样本最多保留 60 秒并提示陈旧，过期后报告不可用。
- 主页面和信号弹窗在后台标签页暂停查询，弹窗消失后停止其定时器；无新增后台串口服务。关闭页面后不保留额外周期采集。
- 模块自动发现结果写 /tmp，查询缓存写 /var/run，页面读取不提交网络配置；只在用户保存或首次必要初始化时写配置。
- 修正有有效 QENG NSA 小区而 QCAINFO 未列出 NR 聚合信息时误报 LTE；无效 NR 信号占位值不会据此判成 NSA。JSON 输出转义，SA 大数小区 ID 保留。
- 更新与支持页完整保留。上游该页本身尚未实现在线包升级；内置兼容版随固件升级提供，未添加自动下载/更新。
- 保留上游 ECM、QMI/MBIM、ModemManager 的页面和后端；这些模式只有对应依赖存在时可选，未另外安装 ModemManager 或替换驱动。AW1000 默认和本次执行测试使用共享 AT 串口模式；其他模式、其他模块不标为已验收。
- 旧 modem-data 页面仍删除，新 luci-app-modemdata 与配置保留，首次旧配置清理不会再删除它。

Modemdata 的“已连接”表示模块数据会话，不代表当前流量一定走 SIM；实际出口仍看“上网设置”。本地 AT 查询与计数读取不产生公网测速/探测流量；用户手动点击外部基站查询、地图或支持链接会访问对应网站。

ARP 绑定和 modemdata 的完整原始来源合并到 vendor/luci-apps.tar.xz，内层 ARP 归档保持原字节与哈希，新增 133 个 Git blob 逐一验证；源码上传仍是 100 个文件。

## 继续保留的改进

- QCA 无线扫描改为手动读取已有缓存，不创建临时 STA、不执行强制主动扫描，不再自动循环刷新。缓存可能为空或过期，页面明确说明；这版不提供新的主动扫描结果。
- 采集驱动 VAP up/down 事件，按当前接口 ifindex 关联。驱动确认 AP down 时，不再被残留的接口 UP 标志误导。扫描请求期间若新发生驱动确认的掉线、配置未改变且无线未禁用，允许限频单频恢复一次；不反复重载。
- DNS 先更新，IPv6 由独立工作进程处理，分别限时与退避重试。只协调启用的自动 DNS 实例，并清理已不属于当前实例的工程临时文件。
- 检查 IPv6 完整规则、路由和过滤器，按所有权清理。备用线路地址变化只更新维护例外，不重置当前 RA/NAT；实际出口变化只清理旧 LAN 前缀的 IPv6 连接。
- 无线采样未知超过 120 秒后停止沿用旧信号颜色，设备节点实际消失单独记录。Mobile Signal 不因仅有线模式停止探测而误报失网，电话灯继续由 fxsd 独占。
- 缓存静态配置、按变更提交配置包；日常线路切换只更新内存状态。路由修复必须有 netifd 发布的默认路由，不能凭 DHCP 地址虚构网关。
- 上网设置简化为线路模式、状态、应用及 120 秒确认；DNS、IPv6、探测次数/间隔、蜂窝流量等放在展开详情中。WAN 协议、账号、VLAN、MTU 在“网络 → 接口 → wan”配置。

完整验证与尚未完成的硬件验收见 [VALIDATION.md](VALIDATION.md)。

## 使用与交付

1. 解压源码 ZIP，将 `QWRT-arcadyan-aw1000-1.2.8` 目录内全部内容上传到 GitHub 仓库根目录，包含 `.github`。
2. 更新旧仓库时删除 `build-phone-driver.yml`、`build-public-phone-driver.yml`，只保留 `build.yml`、`check.yml`。
3. Actions 运行“AW1000 QWRT 改造”，填写原始固件 URL 和定制参数。
4. 构建前执行源码检查，构建后用完整成品运行库执行全部自动测试；成功后仅上传 Releases，不保存 Artifacts。

源码 ZIP 与顶层目录均为 `QWRT-arcadyan-aw1000-1.2.8`，上传文件不超过 100 个。完整电话上游、实验驱动与供应 IPK 以归档保存，构建时解包、验证锁定哈希。不要遗漏 vendor 中的归档。

Release 只包含：

- `QWRT-arcadyan-aw1000-1.2.8-factory.ubi`
- `QWRT-arcadyan-aw1000-1.2.8-sysupgrade.bin`
- `QWRT-arcadyan-aw1000-1.2.8.sha256`

两种镜像使用相同 kernel/rootfs。sysupgrade 是原 nand_upgrade_tar 支持的 CONTROL/kernel/root TAR，由原 fwtool 追加并回读机型元数据，不能用于 U-Boot 工厂刷写。构建暂存文件在验证成功后才进入发布目录。

## 编译输入

| 输入 | 说明 |
| --- | --- |
| firmware_url | 原始 QWRT 25.12.2 固件 ZIP/UBI URL |
| router_name | 主机名，默认 AW1000 |
| lan_ip | LAN 私网 IPv4，固定 /24，默认 192.168.1.1 |
| ssid_24g、ssid_5g | 分别设置 SSID，1–32 字节 |
| wifi_password | 双频共用，8–63 位 ASCII；可留空用 WIFI_PASSWORD Secret |
| root_password | 可留空用 ROUTER_ADMIN_PASSWORD Secret；均为空时按硬件 SN 派生 |

编译页面已取消 SN、原文件 SHA256、电话模式输入，电话固定 `bundled`。内部下载仍计算 SHA256，供应归档仍按 sources.lock.json 校验。Secret 另支持 BOARD_STATE_KEY；日志和摘要不记录密码、SSID、SN 或下载 URL。

root 密码规则：手填/Secret 优先；否则从 board_data、设备树或正确配置的 fw_printenv 读取唯一的 ARC 加 10 位数字硬件 SN，沿用 v1.50 SN-v3 算法生成 8 位小写字母与数字密码。不使用 IMEI；无有效 SN 时回退 `password`。目标 libc/nixio 只使用已验证的 MD5-crypt，不使用不兼容的 SHA512-crypt。保留配置升级保留原 shadow。

计算器：`python3 scripts/password.py ARC1234567890`。路由器上 `aw1000-credentials status` 显示初始化来源，`serial` 查询 SN，`show` 只显示初始化时的派生/回退密码，无法还原手填或之后用 passwd 修改的密码。

## 网络与探测

| 接口 | 绑定与用途 |
| --- | --- |
| lan / br-lan | lan1–lan4，原 eth0–eth3；双频 AP 加入 LAN |
| wan | 物理 wan，原 eth4；默认 DHCP |
| wan_v6 | DHCPv6，跟随 @wan |
| wwan | QModem 管理的移动 IPv4，底层设备动态检测 |
| wwan_v6 | DHCPv6，跟随 @wwan |

物理端口由 S18aw1000-ports 在网络启动前改名；冲突/失败回退原名并同步配置，保留 NSS/PHY/校准。旧接口、VLAN、IP/MAC 绑定按目标 UCI 迁移，遇到未提交编辑或歧义停止迁移，不丢弃设置。

“网络 → 上网设置”四种模式：

1. 仅有线网络
2. 仅SIM卡上网（3G/4G/5G）
3. 有线网络优先，断网自动用SIM卡
4. SIM卡上网优先，断网自动用有线

只改变模式时保留 WAN 接入参数；未确认可回退，重启也回退未确认事务。仅单线模式下，另一线路停止健康探测，保留模块驻网、PDP、DHCPv6 和电话。备用蜂窝探测默认 60 秒，连续失败后 120/300 秒；蜂窝当前出口 10 秒，仅 SIM 模式 30 秒。每轮单目标成功后结束，失败才尝试第二目标；探测不含 DNS/HTTP 测速。计数是实际探测尝试，不代表运营商精确计费流量。

自动 DNS 跟随已发布的 IPv4 出口，使用学到的 IPv4 DNS 加 223.5.5.5、119.29.29.29。MosDNS/用户自定义解析接管时保留其设置。IPv6 跟随实际出口，支持 PD 和 ULA/NAT66；两路 DHCPv6 客户端保持运行。自动线路变化不提交 /etc/config/mwan3，也不整体重载 mwan3。详见 [docs/internet.md](docs/internet.md)。

QModem 沿用 2.8.10，共用 at-daemon 队列；修正旧 quectel-CM 不支持的 -D 和嵌套 procd 事务。辅助 udhcpc 保留地址与非默认 classless route，不再抢占/删除有线 default。管理期间暂停 ECM/通用流量卸载，吞吐量可能降低。

## 无线、按钮与灯光

默认 CN、WPA2-PSK/CCMP，保留硬件校准和信道宽度。按钮仅由 /etc/rc.button 分发，Wi-Fi 按释放事件依据真实 AP 状态切换双频，执行一次原 QSDK wifi up；WPS 通过双 AP hostapd PBC。按钮/扫描/WPS 共用互斥锁。

用户曾验证 Wi-Fi 关闭与恢复、双 AP PBC Active；客户端完成 WPS 配对仍待验收。单次 QSDK 无线重建仍可能短暂增加 CPU 占用，不能视为缺少依赖或宣称已消除。

| 灯 | 控制 |
| --- | --- |
| Power | ipq::led0，面板常亮 |
| Wi-Fi / Internet | led3 / led4，实际 AP / 当前健康出口 |
| 制式 | 红绿蓝 led10/led9/led7，SA 白、NSA 蓝、LTE 绿 |
| Mobile Signal | 红绿蓝 led6/led2/led1，真实驻网信号；硬 SIM 故障或明确持续失网红闪最多 60 秒 |
| Phone | led12，fxsd 独占；正常运行绿色常亮 |

数字节点 Power/Wi-Fi/Internet/Phone 为反向电平，RGB 保持原方向。QCA LED helper 只按锁定模块哈希改四字节入口，保留无线统计，未知模块拒绝构建。无 /dev/mem 写寄存器。详细边界见 [docs/led-controller.md](docs/led-controller.md)。

## 保留组件与清理

集成 Argon 并默认启用；中文 IP/MAC 绑定默认 br-lan，只管理自己添加的静态 ARP，不提供 IPv6 或交换机端口安全。电话保留状态、设置、通话记录、CSV 和汉化，删除“改造状态”入口及页面外部链接，保留上游许可署名。

LibreSpeed Go 1.1.5-r5 默认启用、端口 8989、database_type=none。访问 http://路由器LAN地址:8989/ 测量客户端与路由器的局域网速度，不等于公网速度；不放行 WAN 端口。首次升级应用默认值，之后不覆盖用户自行关闭或改端口。

移除旧 modem-data、SSR Plus、OpenClash/Mihomo、Design、CoreMark、IPTV/msd_lite/rtp2httpd/mcproxy/xupnpd、DDNS、FTP、ZeroTier 和独占依赖，原固件共 57 个包；按 opkg 文件归属保留共享依赖。普通 UPnP miniupnpd 保留。BusyBox ftpd 入口和 inetd 条目删除，共享二进制内部代码未重编译剔除。删除 /etc/banner。

## RJ11 与升级边界

固定内置 v1.2.5 构建的 0.3.0 公开 QSDK 实验模块、原内核/配置/模块哈希及 vermagic，构建时关闭占用 SLIC GPIO 的 SPI 控制器并重算 FIT DTB 哈希，Linux Image 保留。当前服务及界面源码固定 f26f9f5a4bfbee391a3a15c9dab886c96d7ca539，内核模块编译源为 5a5c874。两者内核源码完全相同，完整源码/GPL 许可在 vendor 归档。默认 hd=1 / 16 kHz，与已采集 QDAI 参数相符。

56 个导入符号和 48 个函数地址检查不能证明完整 ABI 相同；公开配置仍有 51 项差异。用户曾反馈可通话但杂音，当前工程没有目标机通话/噪声验收，不据此宣称制式问题已解决。

1.0.17 已验证 LuCI 写入后手动断电可启动新系统；1.2.5 的诊断记录已完成刷写、配置恢复和最后同步。1.2.6 将升级收尾改为优先 reboot -f，保留镜像校验、UBI 返回值检查、完成标记及命令返回后的 SysRq/升级进程路径；**1.2.6 的 LuCI 升级自动重启已通过用户真机验证**。保留已验证的 factory.ubi 作为 U-Boot Web 恢复路径，不刷写 MIBIB/U-Boot。

## 本地构建

```sh
sudo apt-get update
sudo apt-get install -y squashfs-tools mtd-utils gcc-aarch64-linux-gnu libc6-dev-arm64-cross gcc jq make python3-venv openssl lua5.1 qemu-user-static
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/settings.py
python -m unittest discover -s tests -v
python scripts/check_source.py
python scripts/build.py --input /absolute/path/aw1000-qwrt-25.12.2-factory.zip
AW1000_TEST_TARGET_ROOT="$PWD/work/build/root" python -m unittest discover -s tests -v
python scripts/package_source.py
```

使用全新工作目录，不上传 .private、work、out。后续交付先递增 VERSION 并更新 CHANGELOG；来源锁定见 sources.lock.json，源码指纹见 SOURCE_SHA256SUMS，许可见 LICENSE / THIRD_PARTY.md。

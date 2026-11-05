# 更新记录

## 1.2.8 — 2026-11-05

- 电话上游固定 f26f9f5，74 个原始文件，fxsd/LuCI 0.7.0，新增免打扰、允许名单、重复来电响铃、话机连接状态、号码本地格式和 RP-AS，修复 FSK 与 AT 回复截断。
- 同步 SIP 独立模式及开关联动，默认关闭，仍需配套 QModem voice/qmodem-sipd 与 at-daemon >=3.4.0。
- 新页面词条全部汉化。默认 Phone 灯由 fxsd 常亮，HD 沿用 16 kHz。
- 内核驱动源码 9 个文件与原构建基线一致，复用 0.3.0 Linux 5.4.213 模块并增加源码身份复核，保留实际构建来源。

## 1.2.7 — 2026-11-04

- 移除原固件内置的 3proxy 包、启动项、默认配置、升级保留规则和 opkg 登记。打包前显式清理 `/usr/bin/3proxy`，从成品回读检查该路径。
- 继承 1.2.6 已通过真机验证的 `reboot -f` 优先升级收尾。

## 1.2.6 — 2026-10-30

- 仅调整升级收尾：写入、配置恢复、完成标记及原有同步完成后，优先执行 `reboot -f`；若命令返回，等待 5 秒再尝试原有 SysRq，随后退出 stage2 交给 upgraded。
- 修正旧注释和发布说明中“30 秒看门狗保证兜底”的表述；重启调用阻塞时无法执行后续脚本，不承诺强制恢复。
- 记录 1.2.5 外部诊断已到达最后同步和 SysRq 请求阶段，不将日志停止等同于已经定位底层故障或新方案已修复。
- 明确旧版升级到本版仍执行旧版收尾；运行 1.2.6 后再次升级，才验证新顺序。
- 保留 1.2.5 的电话驱动/源码及全部网络、DNS、IPv6、LED、按键、modemdata 与 LibreSpeed 功能；源码/目录/成品统一使用 1.2.6，Actions 仍仅发布 Releases。

## 1.2.5 — 2026-10-30

- RJ11 固定上游 5a5c874，驱动 0.3.0、fxsd/LuCI 0.5.0；完整 69 文件源码、Linux 5.4.213 内置模块重新编译。
- 同步通话掉线处理、NO CARRIER 事件、约 30 秒主循环卡死保护、振铃节奏和系统日志号码隐私改进。
- libubus/libubox 171 个来源文件固定并静态编入电话服务，不替换系统共享库与 QModem；支持新旧 ubus socket 路径。
- 中文新增设置及内置版本显示，保持无外部项目链接、Phone 灯独占与常亮、共用 AT 队列及默认 HD 音频。
- SIP 桥接源码/驱动接口同步，默认关闭；当前基础 QModem/at-daemon 2.8.10 不满足完整 SIP 条件，需另行匹配配套服务。
- 延续 1.2.4 全部 modemdata、DNS/IPv6、双线、LED、按钮、ARP 与 LibreSpeed 改进；源码维持 100 文件，Actions 仅发布 Releases。

## 1.2.4 — 2026-10-29

- 完整集成 4IceG luci-app-modemdata 五个页面、中文、图标和 obsy/modemdata 后端，锁定两个上游提交并校验 133 个 Git blob。
- 默认 RG500Q-EA、自动跟随 QModem AT 端口、关联 wwan；首次初始化尊重已有模块和未提交编辑，兼容旧配置。
- 串口统一走 at-daemon，动态/身份缓存 10/300 秒，锁、采集超时、失败退避、陈旧状态提示；无新增串口常驻服务，隐藏页面暂停。
- 自动发现写入移至 /tmp，运行缓存位于 /var/run；保留配置升级的旧清理不再删除新 modemdata 配置。
- 修复 QCAINFO 缺 NR 信息时有效 NSA QENG 被误报 LTE，补齐 JSON 转义、SA 大数 ID、LuCI 依赖检查和通知兼容。
- 更新/支持页按上游实际功能完整保留；QModem 继续管理拨号，未自动安装 ModemManager 或替换 USB/内核驱动。
- ARP 和新插件来源合并归档，源码文件继续保持 100 个；发布仍只有两种镜像和校验文件，不保存 Artifacts。

## 1.2.3 — 2026-10-29

- QCA 扫描改为手动缓存读取，移除临时 STA、强制主动扫描及自动刷新；锁、超时、缓存与单次条件恢复，驱动 VAP 状态覆盖残留 UP 标记。
- DNS 先行、IPv6 独立工作进程，限时/退避/代次取消；按启用实例管理 DNS，清理旧工程文件。
- IPv6 完整性和所有权账本；备用地址更新不重置当前 RA/NAT，只清理旧 LAN 前缀连接并保留失败重试队列。
- 静态配置缓存、只提交修改包、DHCP 默认路由前置检查；无线信号样本 120 秒失效，Phone 仍由 fxsd 独占。
- 上网设置只保留模式、状态、应用/确认，详情折叠；切换模式保留 WAN 协议、账号、密码、VLAN/MTU。
- Actions 去掉 SN、原文件 SHA256、电话模式输入，固定内置 bundled；成品后完整测试、严格三个发布资产，硬件验证标记与历史证据分开。

## 1.2.2 — 2026-10-28

- 修正Mobile Signal信号源：实际SIM就绪且3G/4G/5G已驻网时，使用真实RSRP/RSCP显示常亮颜色；无PDP地址、tracker disabled/offline/connecting及线路切换不再构成信号失网依据。沿用原有颜色、强度门槛及GPIO电平，不关闭Mobile Signal。
- 共用QENG采样增加三态registered：有效小区样本为true，明确SEARCH/LIMSRV为false，AT错误/空响应为null。未知样本保留既有灯态；不新增AT命令、ping或公网请求。SIM硬故障立即告警、真实失网15秒容错及60秒红闪后熄灭保持。
- 菜单和标题统一为“上网设置”，线路选择项为“线路模式”，DHCP/PPPoE项为“有线接入方式”。四种名称完全采用用户确定的文本；wired_only/cellular_only/wired/cellular配置键与RPC参数不变。
- 状态改用“当前上网线路：有线网络／SIM卡网络”；单线路排除项显示“本模式不使用，已停止检测”，即使旧健康状态暂时仍是offline/connecting也不误报故障。
- 保留低流量探测、1.1.8恢复循环修复、IPv6内存配置、1.2.1 LibreSpeed 8989无数据库默认启动，以及Internet灯、Phone灯、按键和其他功能。
- 新增多分钟停止探测/无数据会话的信号灯执行回归、无线采样三态及原QWRT jq兼容测试、LuCI实际渲染的四模式名称/状态回归。
- 完整原系统验证另发现旧版UCI不支持-t；IPv6私有配置改用原版支持的-P读取/暂存，通过export导出到内存配置文件并清除对应私有delta，不执行持久配置commit。新增原QWRT UCI实际读写回归，确认IPv6前缀类别、RA寿命可保存到内存且原配置不变。

## 1.2.1 — 2026-10-28

- 按用户指定升至1.2.1，源码ZIP/目录、factory、sysupgrade、校验文件和固件显示统一使用新版本。
- 离线集成用户上传的 librespeed-go_1.1.5-r5_aarch64_cortex-a53.ipk，固定SHA256，校验包身份、架构、依赖与AArch64 ELF，补齐opkg元数据；不在构建主机执行maintainer脚本。Actions构建还用原QWRT运行库检查二进制可加载。
- 默认 enabled=1、listen_port=8989、database_type=none，并创建S99librespeed-go启动项。加入专用非root用户和组，使用不可登录账户；首次启动兼容保留配置升级时缺用户、enabled=0和旧端口的情况，仅应用一次默认值。
- 内置网页及测速后端，通过 http://路由器LAN地址:8989/ 访问；不增加WAN防火墙放行。此功能测速客户端与路由器的局域网链路，无数据库模式不保存测速历史。
- 保留1.1.8恢复循环修复和1.1.7低流量探测、IPv6内存配置等功能。后续版本更新入口合并到package_source.py --version，替代独立bump_version.py，使新增IPK后仍为100个上传文件。

## 1.1.8 — 2026-10-28

- 修复 1.1.7 动态探测中的恢复循环：WAN 完成四轮验证后，协调器稍后发布 selected=wan，会使它从显示上的 standby 变为 active。旧 aw_wait 对所有接口执行 disconnected，误清零正常 WAN 的恢复计数，并产生虚假 WAN 故障代次，连带蜂窝反复恢复。
- 重新验证只允许用于“有线优先模式下，WAN 实际不在线时，从备用状态被唤醒的蜂窝 tracker”。WAN 自身的角色变化、健康 WAN 下的角色变化、单线路或蜂窝优先模式的角色变化均不清零计数。
- 新增原生 tracker 完整主循环回归，模拟 selected 发布延迟：1.1.7 在40秒内反复 connected/disconnected；修复后只发布一次 connected、保持 online/score=6，不产生 AW_OUTAGE。原 QWRT BusyBox 下复现与修复均验证。
- 保留 1.1.7 的低流量探测、内存配置、接口命名、电话与灯光等功能；不改变探测目标、恢复门槛或优先级。
- 另提供独立 1.1.7 热修复，严格检查原 tracker SHA256，备份、原子替换，只经 procd 回收两个指定 tracker 等待自动重生，不整体重载 mwan3、不提交 UCI。热修后版本仍显示 1.1.7，以 tracker SHA256 区分；完整源码交付升为1.1.8。

## 1.1.7 — 2026-10-28

- 蜂窝使用 8 字节 ICMP 负载、单包、2 秒超时；健康有线下备用 60 秒，持续故障退避 120/300 秒，蜂窝承载上网 10 秒、仅蜂窝 30 秒，蜂窝优先但离线时 30 秒恢复探测。有线保持 2 秒。
- 同一个 mwan3track 内动态调度，单线路模式让另一 tracker 休眠且不发探测包；保留模组注册、PDP、DHCPv6 和 RJ11。停止探测不保证蜂窝总计费流量为零。
- 有线失效立即唤醒蜂窝，撤回陈旧备用健康状态，连续 4 轮成功后才发布可用出口；WAN 故障代次门槛防止旧备用状态提前进入策略。物理拔网线也在策略重建前失效旧验证。
- 自动调度只写 /var/run，不提交 mwan3、不整体重启 mwan3。IPv6 改用私有内存配置副本；仅 netifd/odhcpd 通过构建的 libuci 读取适配读取副本，提交也只落到内存目录。用户保存配置后旧副本立即失效；未提交的用户编辑不进入副本。
- 保留 DNS/IPv6 跟随实际出口及旧出口定向连接清理。LuCI 新增探测角色、周期、最近成功的启动时间和本次蜂窝接口收发字节，单线路排除项显示停止探测。
- 保持内核、电话、LED、按键、接口名称、主题和原组件清理；源码与固件命名统一升至 1.1.7。完整真机线路切换及计费流量仍需验收。

## 1.1.6 — 2026-10-27

- 根据 AW1000 真机现场结果修复有线恢复后仍使用流量的问题：WAN 已取得地址和网关，但 QConnectManager 附带的 udhcpc 脚本会建立无 metric 的移动默认路由，并删除其他默认路由，导致 mwan3 的有线路由表缺少 default。
- 对原固件 `/usr/share/udhcpc/default.script` 做精确锚点补丁：移动 `wwan*` 仍由 QConnectManager 配置地址和非默认 classless route，但不再增加、替换或删除默认路由；默认路由、metric 和策略表统一由 netifd/mwan3 管理。保留 1.0.13 已验证的旧 QConnectManager `-D` 兼容修复。
- 上网协调器增加保守的 WAN 路由自愈：只有 WAN 已 up、已取得 IPv4 且 netifd 已发布默认路由时才检查；主表缺失则按 netifd 参数恢复，mwan3 表缺失则调用一次 `mwan3 ifup wan` 重建。表号从现有规则解析，不假定固定为 1，并用 20 秒冷却避免重载风暴。
- 出口确认后 DNS 仍跟随实际选中的 `wan`/`wwan`。路由器本机与 LAN 地址自查询已经实测成功；Windows 客户端 TCP/UDP 53 不到达 br-lan 的问题仍单独保留为未解决验收项，不用路由修复掩盖。

## 1.1.5 — 2026-10-27

- 按用户指定直接使用 1.1.5；LuCI“固件版本”将北京时间编译日期与工程版本合并显示，例如本地构建为 `QWRT R26.10.27_V1.1.5`。
- 同一完整版本字符串写入 `/etc/openwrt_release`、`/usr/lib/os-release` 和首次启动默认设置，避免首次启动后回退成只有日期的版本号。
- 源码目录、ZIP、factory、sysupgrade、校验文件和 Release 命名同步为 `QWRT-arcadyan-aw1000-1.1.5`；1.1.3 的线路、LED 与其他功能保持不变。

## 1.1.3 — 2026-10-27

- SIM 缺失、锁定或明确故障时，信号灯立即红闪，最多持续 60 秒后自动熄灭，避免仅有线上网时长期闪烁。
- SIM 正常但蜂窝数据持续不可用时，先保留 15 秒容错窗口；超时后红闪 60 秒再熄灭。因此普通 2–4/6–8 秒线路探测不会触发故障灯。
- 短暂 AT 样本不可用不会重置已经持续的故障计时；蜂窝真实恢复后立即清零计时并恢复信号颜色。诊断 JSON 新增故障原因、持续时间、超时与阈值字段。
- Windows 客户端 DNS 53 超时、sysupgrade 自动复位、WPS 客户端配对和 RJ11 ABI 验收边界不变。

## 1.1.2 — 2026-10-27

- 双线故障阈值保持连续 2 轮（约 2–4 秒），恢复阈值由 15 轮调整为 4 轮（约 6–8 秒），避免网线插回后长时间停留在蜂窝出口。从 1.1.1 保留配置升级时，只迁移完全匹配的本工程 WAN/蜂窝旧配方，不改用户自定义 tracker。
- LuCI 在有线已起但尚未完成 4 轮确认时显示“启动检测中”，完成后再显示正常或不可用。
- 信号/制式灯与 mwan3 探测状态解耦：线路恢复检测不再触发红闪，仅 SIM 缺卡、锁定或确定故障使红灯闪烁；短暂 AT 样本缺失保留上一个稳定灯态。
- Internet 灯改为跟随 mwan3 已发布的实际出口，有线或蜂窝被选中且健康时点亮，不再固定只看蜂窝状态。
- 143 项测试已在最终 QWRT 运行库上零跳过通过，factory/sysupgrade 独立解包回读 192 项断言通过。线路时序和新 LED 语义仍待 AW1000 真机验收；Windows 客户端 DNS 53 超时未在本版解决。

## 1.1.1 — 2026-10-27

- 实机确认 1.0.17 的 LuCI sysupgrade 已正确写入 kernel/rootfs/rootfs_data，手动断电后启动的是完整 1.0.17；问题重新定位为写入完成后的自动复位阶段，而非 sysupgrade payload 无法启动。
- 新增 AW1000 专用升级覆盖：严格校验 CONTROL、成员列表、FIT 和 SquashFS，只使用 rootfs MTD 内的 kernel/rootfs/rootfs_data UBI 布局，并检查建卷及两个 `ubiupdatevol` 返回值。
- 配置恢复和同步完成后尽力写入持久完成标记，绕开可能卡住的通用 `umount -a`；优先用已确认可写的 SysRq 复位，若返回则退出 stage2，由 30 秒硬件看门狗兜底。新自动复位流程仍待 1.1.1 真机验收。
- 删除与 QWRT `hotplug.json` 重复的 `hotplug.d/button/50-aw1000-wps`，按钮只保留 `/etc/rc.button/*` 入口。真机复核一次 Wi-Fi 按键只执行一次关闭/开启，分别约 6/8 秒，结果均 matched；WPS 同秒向两个 AP 提交 PBC，均为 Active。
- 工程、源码目录和 Release 资产版本统一升级为 `QWRT-arcadyan-aw1000-1.1.1`；既有 DNS 客户端 53 超时、WPS 客户端配对和 RJ11 ABI 验收边界不变。

## 1.0.17 — 2026-10-27

- 合入已在 AW1000 1.0.16 真机验证的 Wi-Fi 按钮热修补：以实际运行中的 ath* AP 接口判断当前状态，避免 UCI 与运行状态暂时不一致时把“关闭”误判成“开启”。
- 对齐原 QWRT/QSDK rfkill 流程：无论目标为开启或关闭，提交 UCI 后只执行一次 `wifi up` 协调状态；不再执行 down→错误回滚→up 的双重重载，也不因 QSDK 非关键返回码反向重载。
- 状态文件记录请求动作、切换前后 AP、QSDK 返回码及最终匹配结果。真机已确认关闭后 ath0/ath1 消失、再次按键恢复，两个方向均为 `reload_rc=0`、`result=matched`。
- WPS 保持 1.0.16 逻辑；真机已确认按键 pressed/released 事件到达，并向 ath0/ath1 两个 AP 成功提交 PBC，hostapd 均显示 Active。客户端配对结果仍待验收。

## 1.0.16 — 2026-10-27

- 源码 ZIP、顶层目录、factory.ubi、sysupgrade.bin、SHA256 文件及 Release 标题统一采用 QWRT-arcadyan-aw1000-版本号前缀。
- 新增 S18 网口命名：eth0–eth3 → lan1–lan4，eth4 → wan；在 netifd 启动前完成，迁移桥接、VLAN、上网设置、IP/MAC 绑定和自定义 LED 设备引用。拒绝运行中改名，失败回退旧名/配置并记录原因；原内核、NSS/PHY 模块和 preinit 端口保持。
- 修复 Wi-Fi helper 在 set -u 下加载原 functions.sh 时因 IPKG_INSTROOT 未定义而退出；改为 CLI 操作 UCI，先提交后开关，保护未提交设置并提供失败回退/去重。
- 修复 WPS 读取高通函数库的同类问题；补齐 QSDK 的 wps_pbc 参数，保留用户显式关闭设置，增加事件和执行结果诊断。GPIO/键码保持原值。
- Actions 在成品上再次执行新按钮/网口兼容测试；继续仅发布 Releases，不保存 Artifacts。

## 1.0.15 — 2026-10-27

- 集成固定提交的 ImmortalWrt `luci-app-arpbind` 与中文界面，新增规则默认 br-lan，使用原系统 ip-full/Lua/UCI，无需新增内核模块。
- 绑定在启动、保存应用和接口重建时同步；服务仅清理自己管理的永久 IPv4 邻居，避免上游停止服务时全网卡 ARP 重置。校验非法/冲突规则，保留外部永久绑定及失败后的管理记录。
- 移除电话“改造状态”菜单和页面文件，保留电话服务、实际状态和内部诊断。
- 源码包及顶层目录统一为 `QWRT-arcadyan-aw1000-1.0.15`；源码保持 100 文件以内。固件镜像文件名沿用 aw1000-qwrt 前缀。
- 继承 1.0.14 的接口、组件清理、DNS 热修复 5 与 IPv6 热修复 6；客户端 DNS 与 sysupgrade 的已知验收边界不变。

## 1.0.14 — 2026-10-27

- 删除 Design、CoreMark、IPTV（msd_lite/rtp2httpd/mcproxy/xupnpd）、动态 DNS、vsftpd、ZeroTier，连同其 LuCI、翻译、启动项与独占依赖。普通 UPnP 端口映射保留；累计从原固件删除 57 个包。
- 网络统一为 `lan` 的 `br-lan` 桥接 eth0–eth3、`wan/wan_v6` 使用有线 eth4、`wwan/wwan_v6` 使用 QModem 动态移动设备。同步拨号重连、mwan3、IPv6、DNS、防火墙和诊断；旧自有接口备份迁移，保留 LAN IP 与 PPPoE/VLAN 设置。
- 合入 DNS 热修复 5：兼容不带正则库的原版 jq，修复有出口却残留空上游的 DNS 配置，也修复 IPv6 和上网设置提交的同类校验错误。
- 合入 IPv6 热修复 6：NAT66 使用 ip6tables 专用显式 SNAT，保留 nft 转发保护和原生 PD；规则缺失修复、失败撤回 RA、所有权保护和回滚清理一并保留。
- 源码继续控制在 100 文件以内，Actions 仅发布两个镜像与校验到 Releases，不上传 Artifacts。

用户已验证路由器自身解析及 NAT66 规则安装；Windows 客户端 53 端口连通仍待排查。改名版、双线切换、真实 IPv6 出网和 sysupgrade 启动不标为已验收；后者调查继续暂停。

## 1.0.13 — 2026-10-27

- 合入已实机确认的灯光修复：Wi-Fi 状态在 netifd 返回空时改用 iwinfo AP/接口 UP 检测，未知状态保留灯态；原 QCA 模块的专用 LED 输出 helper 按完整哈希、ELF、入口/调用/重定位进行四字节定向适配，保留无线统计。INI 和两份无线启动脚本统一关闭额外 LED GPIO 控制。Power、Wi-Fi、Internet 的修正已获设备反馈正常，电话灯仍由 fxsd 独占。
- 去掉原 QConnectManager 1.6.5 不支持的 `-D` 参数，保持合法的 shell 分支；修正 QModem start/reload 嵌套 procd JSON，保留单模块 redial 的 add 语义及已启用实例。
- 合入 DNS 热补：IPv6 helper 出错后仍更新 IPv4 DNS，错误和退出码保存在运行目录；签名相同也核查实际配置文件并自愈，dnsmasq 重启失败会继续重试。
- 将 IPv6 匿名前缀集合拆为三条独立规则，适配本机内核关闭 `CONFIG_NF_TABLES_SET` 的实际配置；没有可用出口时仍保留线路限制，不绕过仅有线/仅蜂窝策略。
- 统一防火墙事件的 mwan3 重载队列，修正管理服务停止后重新启动不恢复 mwan3，以及重复重载引起的竞争；mwan3 mask 缓存增加校验和原子发布。
- 增加 `aw1000-dns-check` 统一只读诊断，`aw1000-led-check` 不再依赖设备缺失的 od。
- 保持 GitHub Actions/Releases-only、内置 RJ11 源码/驱动和两类镜像命名；上传源码仍限制在 100 个文件以内。

本版是后续实机排查的修复基线。DNS/双线切换、IPv6/NAT66 与启动重载仍需从本版继续验收；不宣称当前 DNS 现象已全部消除。用户报告的 LuCI sysupgrade 启动失败按要求暂停处理，本版仅保留格式/回读验证，未解决或通过实机升级验收。

## 1.0.12 — 2026-10-27

- 对照原始内核 LEDC 驱动和 AW1000 接线，修正电源、Wi-Fi、Internet 数字节点的逻辑极性；电源继续由 aw1000-leds 管理，led0 仍映射物理通道 15。
- Internet 灯使用 mwan3 实际登记的蜂窝接口，避免 QModem 旧 alias 导致误判；已证明在线的数据通路不再被暂缺的 AT 样本熄灯，SIM 故障及线路离线仍会熄灯。
- 电话灯继续由 fxsd 独占，补齐 ipq::led12 的极性、最大亮度读取、亮度修复和启动/停止熄灯处理。灯亮表示初始化完成、服务就绪，不代表已验证通话健康。
- LuCI 固件版本按实际编译日期（Asia/Shanghai）显示为 QWRT R年.月.日，同时修正原厂首次启动脚本，避免恢复成 R26.5.5。基础系统、目标和 ABI 身份另行保留。
- 增加 aw1000-led-check 只读诊断；灯光 JSON 记录实际蜂窝接口、健康状态、逻辑灯态和原始亮度。源码继续少于 100 个文件，Actions 仍只发布 Releases。

限制：物理灯色、极性适配后的亮灭及 RJ11 加载/通话仍需在目标 QWRT 5.4.213 上验收；具体证据与适配边界见 docs/led-controller.md。

## 1.0.11 — 2026-10-27

- 修复 QModem 2.8.10 新建 RG500Q-EA 配置时重新启用前缀扩展和直接写 DNS 的问题：固定小写 `ipv4v6`，关闭 `extend_prefix`/`ra_master`，启用 `do_not_add_dns`。
- 路由器继续向 LAN 提供统一 DNS；自动模式只使用当前出口学到的 IPv4 DNS，并始终追加 223.5.5.5、119.29.29.29 作为上游容错，仍正常返回 AAAA，不再因蜂窝 IPv6 路由异常拖慢全部解析。
- IPv6 表 61000 只接受明确网关；蜂窝 raw-IP 的裸 `default dev` 不再被当作可用 Internet 路由，PPPoE 等点到点接口仍支持设备路由。
- 增加定向升级迁移，仅清理问题报告中的固定 DNS/RA relay 配方和本项目管理的 QModem 字段，不覆盖其他自定义 DNS、MosDNS 或用户网络配置；LuCI 同时区分自动、自定义接管和无上游状态。
- 59 项自动测试通过，源码仍为 76 个上传文件；factory/sysupgrade 完整构建与回读结果记录在 `docs/build-validation.json`。

限制：双线路切换、运营商 DNS 故障、IPv6 PD/NAT66 和蜂窝 raw-IP 网关判定仍需在目标 QWRT 5.4.213 上实机验收；RJ11、按钮与其余 LED 的既有验收边界不变。

## 1.0.10 — 2026-10-27

- 电话灯 `led12` 重新交由 RJ11 的 fxsd 独占，`aw1000-leds` 不再读取或写入该节点。
- fxsd 启动前先熄灭电话灯；SLIC、模块和电话服务正常进入运行状态后点亮，待机、振铃、摘机均保持绿色常亮；服务停用、初始化失败或退出时熄灭。
- 新装及保留配置升级统一写入 `led=ipq::led12` 和 `on,on,on`，避免上一版空 LED 配置残留。
- 固件打包增加异常隐藏可执行文件排除与成品拒绝检查。57 项自动测试及 factory/sysupgrade 完整构建、UBI/SquashFS 回读通过。

限制：电话灯实际亮灭仍需在目标 QWRT 5.4.213 上随 fxsd 启停验证；RJ11 实验模块的 ABI、呼入呼出和双向语音验收边界不变。

## 1.0.9 — 2026-10-27

- 由 `aw1000-leds` 独占前面板灯光：电源灯与电话灯常亮，其余制式、信号、Wi-Fi、蜂窝 Internet 灯按原厂图示逻辑刷新。
- 根据目标 5.4 LEDC 驱动的 13 节点上限，将空闲 `led0` 严格重映射到原电源物理通道 15；保留 led1～led12 的既有映射并重算 FIT 哈希。
- 禁止 QModem 和 fxsd 写灯，首次启动清理旧 qmodem/system/fxs 灯光配置；Internet 灯不再被有线当前出口误点亮。
- 补齐固件实际使用的 `/etc/rc.button/wps` 入口，支持 hostapd socket/ubus 回退与重复事件抑制；Wi-Fi 双频切换增加失败回退。
- 新装和保留配置升级均删除 `/etc/banner`，并清理指向该文件的 Dropbear BannerFile 配置。
- 修正 SquashFS 解包器对重叠文件/符号链接类型判断的顺序。57 项自动测试、76 个上传文件；完整 factory/sysupgrade 构建和回读通过。

限制：按钮键码、脚本分发和 hostapd 调用已验证，但后部 Wi-Fi/WPS 实体按键仍需真机按压确认；除已确认的 led9 外，其余灯色、组合白色和电源通道重映射仍需实机验收。RJ11 实验模块与 sysupgrade 的原验收边界不变。

## 1.0.8 — 2026-10-26

- 移除 OpenClash/Mihomo 与独占 Ruby/相关库，连同前版项目合计删除 34 个包；保留共享网络依赖并清理保留升级配置中的旧代理钩子。
- 移植 v1.50 中文上网页面：四种线路模式、DHCP/PPPoE、VLAN/MTU、120 秒确认回退及重启回退。
- 固定归档集成 fw3 mwan3 2.11.7-2，适配原 QWRT musl、QModem 2.8.10、实际蜂窝接口与 v6 后缀，独立线路探测及 SIM 恢复门控。
- IPv6/DNS 跟随已发布的策略；兼容 MosDNS 接管和仅 PD 的原生 IPv6，无 PD 时支持 NAT66 回退。
- 共享 AT 采样供 LED/线路管理使用；按旧 mark 清理 IPv4 连接，IPv6 改变时清理 IPv6 连接；修正 Linux 5.4 的 netlink family 限定。
- 管理期间暂停 ECM/流量卸载，服务停止恢复；mwan3 清理范围限制为所属表和规则。
- 保留双镜像及 Releases-only Actions。54 项测试，74 个上传文件。

限制：双线切换、IPv6/NAT66、NSS 状态恢复尚需目标 QWRT 实机验收；暂停加速可能降低吞吐量。RJ11 实验模块与 sysupgrade 仍保持原验收边界。

## 1.0.7 — 2026-10-26

- 移除 modemdata、SSR Plus、SSR 后端和无其他包依赖的专用组件，共 21 个包；按 opkg 归属清理文件、服务链接、元数据及空目录，保留共享依赖。
- 去除电话首页外部项目链接，保留中文功能、CSV 导出、原作者许可和源码署名。
- 修复实际 QWRT led0～led12 编号映射；led9 制式绿灯已获用户确认；电源通道未暴露时保留硬件状态。
- QModem 普通/快速 AT 默认共享 at-daemon，灯光和电话支持 override_at_port。
- 同时生成带工程前缀的 factory.ubi 和 sysupgrade.bin；后者为 kernel/root TAR + 机型元数据，使用原 fwtool/BusyBox tar 回读检查。
- 42 项测试；55 个源码文件；Releases 仅发布两种镜像与 SHA256，不保存 Artifacts。

限制：sysupgrade 尚未实机升级；RJ11 仍需目标内核加载与通话验收。

## 1.0.6 — 2026-10-26

- 删除 build-phone-driver.yml 和 build-public-phone-driver.yml；取消 required 和外部驱动 URL，只保留源码包内置 bundled 与关闭 deferred。
- 修复 root 登录失败：原 QWRT libc/nixio 实测不支持旧版写入的 SHA512-crypt，固定密码与 SN 派生密码均改用原固件支持的 MD5-crypt。
- 每次构建通过 QEMU 运行原 QWRT Lua/nixio，实际校验所选密码存储格式。
- 增加可选机身 SN 输入，在没有有效硬件 SN 时使用；保留 SN-v3 算法和密钥。
- 保存首次密码来源，新增 credentials status，show 不再因以后发现 SN 而误报原来的回退密码。
- 37 项测试覆盖 SN、回退、手填、首次写入和重复执行保留；源码包 53 个文件。

限制：旧配置保留的 shadow 不会自动重置；RJ11 仍需实机加载和通话验收。

## 1.0.5 — 2026-10-26

- 修复主 Actions 使用 `--no-install-recommends` 时未安装 ARM64 libc 开发头文件，导致编译 fxsd 报 `bits/wordsize.h` 缺失的问题。
- 主构建和检查工作流显式安装 `libc6-dev-arm64-cross`。
- 新增 ARM64 libc 头文件、静态链接及 ELF 架构预检；主构建在下载和解包固件前失败并给出明确依赖提示。
- 更新本地构建依赖说明，并增加缺失工具链及错误架构测试。

限制：内置驱动仍有 51 项公开配置差异，尚未在 QWRT 加载或完成通话验证。

## 1.0.4 — 2026-10-26

- 将已实编的 v1.0.3 公开 QSDK 驱动包内置到 vendor，固定归档及模块 SHA256，免去额外填写驱动 URL。
- 主 Actions 默认 bundled，自动集成模块、处理 DTB 的 SPI/GPIO 冲突并将电话配置启用；deferred 可关闭，required 保持严格身份校验。
- 内置模式同时核对原 Linux Image、原配置身份、ELF 架构和 vermagic；修改的内置驱动包会被拒绝。
- Release 标题、说明及固件状态明确标记 RJ11 实验适配，保留 ABI 和硬件未验证状态。
- 30 项检查通过；上传源码文件数为 55，继续不使用 Artifacts，主流程直接发布 factory.ubi 与 SHA256。

限制：内置驱动仍有 51 项公开配置差异，尚未在 QWRT 加载或完成通话验证。

## 1.0.3 — 2026-10-26

- 使用固定公开 QSDK 12.2.5.751.897 提交与校验过的 Linaro GCC 7.5.0，实际编译出 AArch64 / Linux 5.4.213 RJ11 实验模块。
- 解析原 QWRT Image 的 PREL32 导出记录，检查驱动 56 个导入符号；其中 48 个函数地址与原 kallsyms 交叉一致。
- 原内核未启用 MODVERSIONS；明确标记重建的 GPL-only modpost 索引，不把它称为原始 Module.symvers，也不伪造 CRC。
- 最终模块链接没有缺失符号警告；struct module section 与原模块同为 768 字节，vermagic 一致。
- 记录 51 项配置差异，新增公开实验构建 Actions，驱动包和 SHA256 直接发布 Releases；主固件严格流程拒绝误集成实验包。
- 27 项检查通过，源码继续控制在 100 个上传文件以内。

限制：使用 Linaro 工具链和公开 QSDK，尚未证明与原 QWRT 完整 ABI 相同；未实机加载或验证电话，不直接宣称 RJ11 可用。

## 1.0.2 — 2026-10-26

- 将完整电话上游源码压缩内置，构建时自动解包；增加归档及各原始文件 SHA256/Git blob 检查，拒绝路径穿越、链接和额外文件。
- 增加源码打包工具和不超过 100 个上传文件的检查，保留全部源码、许可、主题和 DSP patch。
- 从原 FIT 提取并核对未压缩 Linux Image 指纹，记录原工具链构建标识 r47815-ddcc976f7b。
- 电话驱动构建增加原 Image 身份、工具链标识、导入符号检查；主固件拒绝缺少原 Image 指纹的驱动包。
- 整理向原 QWRT 构建者获取匹配环境的具体材料清单。

限制：公开检索尚未取得该原 QWRT 的匹配 SDK，本版未生成 aw1000-fxs.ko；Linux 5.4 源码适配尚未通过目标内核编译或实机通话验收。

## 1.0.1 — 2026-10-26

- 根据本机参考 OpenWrt 的真实 AT 返回，将电话默认 PCM 采样率设为 16 kHz，避免沿用上游 8 kHz 默认值导致新服务改写已有模块配置。
- 增加真实 at-daemon JSON 样本，验证共享 AT 包装器提取原始响应并拒绝错误返回。
- 编译实际电话 C 解析器，验证该 QDAI 响应、8 kHz、主模式和错误响应；检查工作流显式安装 gcc/jq。
- 补充参考系统的 AT、电话待机、分区和 LED 记录，区分 6.12 系统证据与目标 QWRT 5.4 实机验收。

限制：匹配 QWRT 5.4.213 的电话内核驱动仍未构建；参考系统尚未反馈呼入、呼出、双向语音和物理按钮测试结果。

## 1.0.0 — 2026-10-26

- 新建 QWRT 25.12.2 / Linux 5.4.213 固件改造工程，保留原高通驱动。
- Actions 参数化构建及 Releases 直传，不使用 Artifacts；统一版本号。
- 上传 Argon 2.4.3 默认启用；使用现代网络 device/ports 结构。
- LAN eth0–eth3、有线 WAN eth4、QModem 蜂窝接口默认初始化。
- 双频独立 SSID、共用无线密码、固定或 SN-v3 root 密码。
- Wi-Fi wlan 按钮入口及 WPS 无恢复出厂副作用处理。
- QWRT LEDC 状态服务及独立蜂窝连通性指示。
- 上游电话源码固定版本、Linux 5.4 源码适配、完整页面汉化与 DSP patch。
- 电话驱动匹配构建及集成检查；无匹配驱动时基础版明确禁用 RJ11。
- 解包、路径安全、内核/DTB 哈希与 UBI 回读检查。

限制：未实机验收；完整 RJ11 仍需匹配 QSDK 构建环境；未输出未验证的 sysupgrade.bin。

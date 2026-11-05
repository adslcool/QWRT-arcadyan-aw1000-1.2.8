# QWRT 5.4 LEDC：节点映射与电平适配

## 1.2.3 状态新鲜度与 QCA VAP 事件

面板读取当前接口 ifindex 对应的驱动 VAP down/up 事件，驱动确认 down 优先于残留的 IFF_UP/iwinfo 标记；另一频段仍在线时 Wi-Fi 灯仍可亮。事件由阻塞式 logread 管道采集，不周期执行扫描或重载。

Mobile Signal 的未知 AT 样本最多保留原状态 120 秒，超过后熄灭并报告 expired，恢复有效样本后立即更新。已实际观察到的模块节点消失单独作为硬故障；不能因为单线模式停止 tracker 就报告无线失网。Phone 继续完全由 fxsd 控制。上述新逻辑已做自动回归，物理灯光仍需本版刷机确认。

## 1.2.2 Mobile Signal与数据健康解耦

1.2.1把“SIM就绪、移动数据不在线”当作持续失网，导致仅有线模式停止tracker后仍走红闪/熄灭流程。1.2.2只按实际SIM和无线小区状态决定Mobile Signal，不按IPv4地址、探测状态或出口角色判断无线信号。

共用QENG采样输出registered三态：有效SA/NSA/LTE/WCDMA样本为true；明确SEARCH/LIMSRV为false；空响应或AT错误为null。驻网时按已有门槛显示绿色、蓝色或红色常亮；临时未知保留既有信号灯态。不额外访问串口或发送网络探测，既有5G/制式色、GPIO极性和无线统计补丁不变。

只有明确SIM缺失/锁定/故障或明确无线持续失网触发既定告警：SIM硬故障立即红闪；无线失网容错15秒；故障红闪最多60秒后熄灭。恢复有效驻网样本后清零告警并显示信号强度。仅有线模式本身从不关闭Mobile Signal。Internet随实际出口，Phone继续由fxsd独占。

自动回归模拟100到500秒的多次状态：tracker disabled、无移动数据会话、不同强度、一次未知AT样本、恢复检测和切换优先模式；正常信号始终常亮，且Internet在WAN健康时仍亮、Phone不被写入。实际灯光颜色及通话仍需刷机验收。

本记录针对用户提供的 QWRT 25.12.2 / Linux 5.4.213 原始镜像，记录 v1.0.12 电平修正和 v1.0.13 合并的实机热修补。用户已反馈 Power、Wi-Fi、Internet 经修补后正常；完整 RGB、电话和按钮验收仍保留。

## 原驱动的实际行为

从原始 factory UBI 的 FIT 解压 Linux Image，SHA256 为
`2cf34abbdeff69411f4fa70ed000de28ea83165586441f8315eb75ea25344d9c`。
用 `scripts/kernel_exports.py` 的原内核 kallsyms 布局定位函数，并使用 AArch64 objdump 反汇编，得到：

| 函数 | Image 文件偏移 | 本轮核对结果 |
| --- | --- | --- |
| `ipq_set_led_brightness_set` | `0x5985c8` | 写 LEDC 基址加 `0x20058`，非零 brightness 置位、零清位；位号取自 `led_blink_array[index]`，没有每灯极性分支 |
| `ipq_led_source_select` | `0x598698` | 源选择寄存器序号为 `physical / 6 + 1`，每输出占 5 位 |
| `ipq_led_probe` | `0xaffb1c` 至 `0xafffcc` | 节点数上限 13，注册 `ipq::led0` 至 `ipq::led12`；max_brightness 为 1；读取默认触发器，没有读取子节点 `active` 属性 |

因此，把子节点的 `active` 改成 0 或 1 不能改变这个目标驱动的行为。`brightness` 的 sysfs 读回值是软件请求值，也不能单独证明物理灯珠已经亮起。

相同控制流程可与公开 QSDK 驱动交叉核对：

- [leds-ipq.c，固定提交 979e58f5036ed916b6f745d83106bdfefe58341b](https://nest-open-source.googlesource.com/manifest_repos/kernel/+/979e58f5036ed916b6f745d83106bdfefe58341b/drivers/leds/leds-ipq.c)
- [对应 leds-ipq.h](https://nest-open-source.googlesource.com/manifest_repos/kernel/+/979e58f5036ed916b6f745d83106bdfefe58341b/drivers/leds/leds-ipq.h)

公开驱动是交叉证据，本项目没有用它替换原 QWRT 内核驱动。

## 板级接线与极性推断

[OpenWrt 最初加入 AW1000 支持的提交 fbcda3616e2c18b533a552fad9b724a636bcae5e](https://github.com/openwrt/openwrt/commit/fbcda3616e2c18b533a552fad9b724a636bcae5e)
中的 `target/linux/qualcommax/files/arch/arm64/boot/dts/qcom/ipq8072-aw1000.dts` 使用两个 74HC595 串行寄存器。Power、Wi-Fi、Internet、Phone 为 GPIO_ACTIVE_HIGH，RGB 通道为 GPIO_ACTIVE_LOW。

把该接线与原 QWRT 的 LEDC 数组对照，得到：

| 功能 | 串行 GPIO 位 | LEDC 物理通道 | 本项目数字节点 | v1.0.12 逻辑 ON / OFF |
| --- | --- | --- | --- | --- |
| Power | 0 | 15 | `ipq::led0`，由 DT 从空闲通道重映射 | 0 / max |
| Wi-Fi | 1 | 14 | `ipq::led3` | 0 / max |
| Internet | 2 | 13 | `ipq::led4` | 0 / max |
| Phone | 11 | 4 | `ipq::led12`，由 fxsd 控制 | 0 / max |
| RGB | 3、4、5、6、8、9 | 对应的反向串行通道 | 保留当前已使用的 RGB 节点 | max / 0 |

这是依据板级接线、原驱动行为以及用户反馈推断出的 QWRT 数字节点电平适配。现代 OpenWrt 的命名节点（例如 `green:power`）已经由 GPIO LED 驱动处理极性，不能对这些节点再反相。

[OpenWrt AW1000 设备页](https://openwrt.org/toh/arcadyan/astoria/aw1000)
收录的原厂启动日志提供了额外交叉证据：索引 3、4、12、14 的 active 为 0，其他 RGB 索引为 1。原日志时间点分别为 4.647543、4.652221、4.689720、4.699281 秒。该页本轮直接读取触发反机器人页面，以上日志片段来自搜索索引，不能把它视为本机 QWRT 运行日志。

## 修正范围与验收

Power 的 `led0 → 物理15` 映射保持不变，面板服务负责持续常亮；Internet 跟随 mwan3 已发布的有线/蜂窝实际出口；Phone 继续交给 fxsd，面板程序不写 `led12`。数字节点的逻辑状态与写入 sysfs 的原始 brightness 分开记录。

调查初期只确认电平，后续实机对照把两灯闪烁定位到 QCA 活动路径：停止面板后，sysfs 保持 raw0/none 而实灯仍闪；将两个 radio 的 AP 统计临时置 0 并清 blink 后两灯常亮，恢复统计为 1 则又闪。`led_gpio_enable_8074=0` 与统计 blink 是不同开关，不能单凭 INI 判定所有活动灯效已停。

## v1.0.13：保留统计，隔离专用 LED 输出

原 `qca_ol.ko` 的专用 LED helper 在 `.text+0x99180`，调用 `ipq_led_set_blink(3,...)` 与 `(4,...)`，可直接改变 LEDC 而不更新 sysfs 亮度缓存。调用者还负责 AP 统计，因此不永久关闭统计，也不通过高频写亮度争抢。

构建时由 `scripts/led_hardware.py` 进行严格离线修补：

- 原模块 SHA256：`e09f70633bc21f932078484e6e6457800e7b7e901b2fab5835e73ad5a864bbd9`。
- 文件入口 `0x991e8` 的首条栈操作 `fd7bbaa9` 改为 `RET c0035fd6`，只改变四字节；调用者不使用返回值，返回发生在栈帧建立之前。
- 修补后 SHA256：`9b5bb825cf1c520dc5b7393fd0134f06bcdda7fc3feead52dfe82d6dfd7b3de4`。
- 检查 ELF64/AArch64、模块名称/vermagic、入口上下文、两条调用和重定位，入口不得有重定位；未知文件拒绝构建，已修补文件可幂等验证。
- 保留原 Linux Image、DT 灯位和无线统计代码；INI 及两份无线启动脚本均固定 LED GPIO 开关为 0。

用户应用同一模块热修补并重启后反馈 LED 正常。这不等于对所有无线统计、组合 RGB 与电话功能的完整验收。没有修改未确认语义的 CG7，不使用 `/dev/mem` 写寄存器。

Wi-Fi 状态优先使用有效 netifd 状态；旧 QCA 栈返回空时改读 iwinfo AP 模式、有效信道与接口 UP。查询失败保留灯态，真实 AP 在线不受陈旧关闭标记影响。`aw1000-led-check` 用 Lua 解码 DT，不依赖设备缺少的 od。

自动测试验证逻辑与字节变化，不能观测灯光。既有热修补已获 Power/Wi-Fi/Internet 正常反馈。1.1.3 使 SIM 缺失/锁定/故障立即红闪，并对蜂窝持续失网保留 15 秒容错；故障红闪达 60 秒后信号 RGB 熄灭，恢复时立即清零。新语义仍需真机确认，同时继续验收组合 RGB、停止 fxsd 时 Phone 灭及就绪后 Phone 常亮。若不符，应提供版本、`aw1000-led-check` 和实际灯光状态。

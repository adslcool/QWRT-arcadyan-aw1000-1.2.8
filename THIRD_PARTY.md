# 第三方来源

| 内容 | 来源 | 状态 |
| --- | --- | --- |
| vendor/librespeed-go_1.1.5-r5_aarch64_cortex-a53.ipk | 用户上传的确切文件；包内上游地址 https://github.com/librespeed/speedtest-go ，OpenWrt/ImmortalWrt feeds/packages/net/librespeed-go | 包控制声明 LGPL-3.0-only，版本1.1.5-r5；保留原程序、配置、启动脚本及opkg maintainer脚本署名；仅修改默认开启配置，并补齐服务用户和启动项；SHA256固定在sources.lock.json |
| vendor/luci-apps.tar.xz 内的 luci-app-arpbind.tar.xz | https://github.com/immortalwrt/luci ，提交 d72e293e6fd698af75510b9018fb7e154ae219cc 的 applications/luci-app-arpbind | Apache-2.0；完整应用 10 个文件及仓库 LICENSE 共 11 文件归档；保留署名、逐文件 SHA256 和 Git blob 校验，构建时中文化并改用仅管理自有邻居的后端 |
| vendor/openwrt-aw1000-phone.tar.xz | https://github.com/Perz3us/openwrt-aw1000-phone ，提交 f26f9f5a4bfbee391a3a15c9dab886c96d7ca539 | 内含全部 74 个原始文件，保留 GPL-2.0 LICENSE 和注释；适配脚本校验后生成工作副本；sources.lock 中 phone/upstream/ 为归档内逻辑路径 |
| vendor/aw1000-phone-driver.tar.gz | 本工程 v1.2.5 实编产物（驱动源码 0.3.0），公开 QSDK 12.2.5.751.897 + Linaro GCC 7.5.0 | GPL 电话模块及构建清单；对应源码、适配脚本、固定上游来源和构建流程均保留；属于未实机验证的实验驱动 |
| vendor/phone-libs.tar.xz | openwrt/libubox 75a3b870cace1171faf57bd55e5a9a2f1564f757；openwrt/ubus f787c97b34894a38b15599886cacbca01271684f | 共 171 个完整来源文件，原文件版权/许可全部保留；libubox 以 ISC 为主，libubus LGPL-2.1；仅静态链接客户端库，保留完整可修改、重编译与重新链接的电话/库源码和脚本，不替换目标系统共享库。 |
| phone/fxs.zh-cn.po | 用户上传 aw1000-lede-v1.50-main.zip 的 files/fxs.zh-cn.po | 沿用中文词条并补齐新版本页面；来源项目许可保存在 phone/v150-LICENSE |
| vendor/luci-theme-argon_2.4.3-r20250722_all.ipk | 用户上传的确切文件 | 包内 Argon 署名及许可保留 |
| vendor/si3226x_patch.bin | 用户上传的 Si3226x DSP patch | 908 字节，Skyworks/原厂来源专有补丁；不声明 MIT/GPL 再许可 |
| phone/qwrt-kernel.config | 从用户上传 QWRT 的 Linux 5.4.213 内核 IKCONFIG 提取 | 仅作匹配检查，不代替内核源码或构建树 |
| vendor/mwan3-2.11.7-2.tar.xz | https://github.com/openwrt/packages/tree/openwrt-22.03/net/mwan3 | GPL-2.0；Makefile、完整运行脚本、sockopt_wrap.c、LICENSE 均在归档中，逐文件及归档 SHA256 固定；构建中应用适配补丁并编译 wrapper，未使用新版 nft mwan3 二进制 |
| 上网方式页面与 IPv6 跟随逻辑 | 用户上传 v1.50 scripts/internet.py、files/aw1000-ipv6-follow.sh | MIT，许可保存在 phone/v150-LICENSE；已适配 fw3/QModem 2.8.10、确认回退、DNS 目录、PD-only IPv6 和目标连接清理 |
| SN-v3 算法兼容性 | 用户上传 v1.50 scripts/board_state.py | 重写为 Shell/Python，保持默认密钥、HMAC 域和字符映射一致 |

vendor/luci-apps.tar.xz 另含完整 4IceG/luci-app-modemdata（MIT，提交 862d7385346813bb0a862611f3ebd26659b09dfd）和 obsy/modemdata（提交 fbdc0b6a720647642e99c6fc8db933a767c9ead0）来源，共 133 个文件。保留原许可、版权、README、Makefile、翻译和原始符号链接目标。obsy 后端仓库未提供单独 LICENSE，不将前端 MIT 许可套用于后端；其原版权说明保留。构建在工作副本应用队列、缓存、中文和兼容补丁，完整逐文件 SHA256/Git blob 固定在 sources.lock.json。内层 ARP 归档及原校验不变。

原 QWRT 固件由工作流下载，不包含在源码 ZIP 中。该工程不替代原固件组件各自的源码提供义务和许可条款。

# QWRT 5.4.213 电话驱动构建材料

v1.2.5 已针对更新后的电话驱动 0.3.0 利用公开 QSDK 12.2.5.751.897 和 Linaro GCC 7.5.0 实际编出实验 aw1000-fxs.ko，见 [公开构建记录](public-driver.md)。本页保留原作者环境严格匹配流程的材料要求；目前仍未取得该原环境。实验模块未标记为 ABI 匹配或电话可用。参考系统的 6.12.112 驱动不能作为目标 5.4.213 模块使用。

v1.0.6 主固件只集成源码包内置驱动，已取消独立驱动 Actions 和外部驱动 URL。下面保留取得原作者环境后手动重现编译的材料要求。

## 已从上传固件确认的身份

| 项目 | 原固件值 |
| --- | --- |
| 系统 | QWRT 25.12.2 r47986-d312c785fd |
| 目标 / 架构 | ipq807x/generic / aarch64_cortex-a53 |
| 内核 | 5.4.213，SMP PREEMPT，2026-05-17 05:10:09 UTC |
| 原编译器 | OpenWrt GCC 7.5.0 r47815-ddcc976f7b |
| kernel 包 | 5.4.213-1-606f983e9fed006b50a91d394d80f588 |
| vermagic | 5.4.213 SMP preempt mod_unload aarch64 |
| 配置 SHA256 | 6f3db954984db931afa3b1fe2716fe902414d3799758697a3e322b6971bc79dc |
| 未压缩 Image SHA256 | 2cf34abbdeff69411f4fa70ed000de28ea83165586441f8315eb75ea25344d9c |

Image 指纹来自原 FIT 中 kernel@1 的 gzip 数据解压，字节数 12644360，与保存的未压缩数据逐字节一致。系统 revision 与工具链 revision 是两个不同标识，不能混用。

## 需要向原固件构建者取得的内容

可以提供一个可重新定位的 tar 归档，包含：

1. 本次 QWRT 的完整、已准备好的内核构建树，含其 QSDK 补丁；包括 .config、Module.symvers、生成头文件、构建脚本和 arch/arm64/boot/Image。
2. 对应的 AArch64 GCC 7.5.0 工具链，标识应包含 OpenWrt GCC 7.5.0 r47815-ddcc976f7b；保留配套 binutils、宿主运行库等所需文件。
3. 解压后的内核目录和 CROSS_COMPILE 前缀，供手动编译脚本使用。

若原构建者不便提供归档，也可在原环境中运行本工程 phone/build_driver.py，生成带身份清单的 aw1000-phone-driver.tar.gz，然后提供这个驱动包。该脚本会自动解开内置电话源码并应用 5.4 适配。

```sh
python3 phone/build_driver.py \
  --kernel /absolute/path/to/original-prepared-kernel \
  --cross /absolute/path/to/original-toolchain/bin/aarch64-openwrt-linux-musl-
```

构建前检查配置、原 Image 和工具链身份，构建后检查 vermagic 与导入符号；不允许把 modpost 缺失符号降为警告。检查通过后仍需目标设备加载、GPIO/PCM、振铃、双向通话和数据并发验收。

## 本次检索结论

已检索原系统 revision d312c785fd、原工具链 revision ddcc976f7b、内核包标识，以及公开 QSDK 和 AW1000 电话来源；截至本次交付未取得能够确认属于此原构建的 SDK。

- [GL.iNet ipq807x SDK](https://github.com/gl-inet-builder/openwrt-sdk-ipq807x-2102/blob/master/target/linux/ipq807x/Makefile) 所读目标 Makefile 为 ARM / Cortex-A7、4.4 内核，不能用于本目标。
- [公开 QSDK 12.5 内核](https://github.com/liqinhuicn/qsdk-12.5/blob/master/qca/src/linux-5.4/Makefile) 的 Makefile 确实声明 5.4.213；没有证据证明其补丁、构建产物与该 QWRT 一致，未将其作为匹配 SDK。
- 当前官方 OpenWrt 的 qualcommax SDK、上传的 v1.50 LEDE 工程都未提供原 QWRT 的准备好内核树、原导出符号表和工具链。

普通运行中的 OpenWrt 能提供硬件状态，不能恢复出另一版 QWRT 原始的完整编译环境。取得原构建环境有助于严格匹配；公开 QSDK 则已经提供了可运行的试编译路径，后续需在目标 QWRT 上验收。

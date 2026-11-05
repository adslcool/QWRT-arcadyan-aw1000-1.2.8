# 公开 QSDK RJ11 实验驱动 v1.2.5

本版针对上游提交 5a5c874 的驱动源码 0.3.0，已经实际编译出 aw1000-fxs.ko，包名为 aw1000-phone-driver-public-v1.2.5.tar.gz。包中包含模块和 manifest.json；清单记录模块 SHA256、来源、配置差异及符号检查证据。它属于实验构建，未标记为与原 QWRT 完整 ABI 匹配或已经通话可用。

## 已运行的流程

- QSDK：[everything411/qsdk](https://github.com/everything411/qsdk/tree/12.2.5.751.897)，固定提交 12fde49c2e2f5ed85e27be973a8c2ee9130a586a；内核 5.4.213。
- 编译器：实际 Linaro GCC 7.5.0，2019.12 x86_64 / aarch64-linux-gnu；使用 [sherpa-ncnn-toolchains 镜像](https://huggingface.co/csukuangfj/sherpa-ncnn-toolchains/tree/main)，归档 SHA256 为 3b6465fb91564b54bbdf9578b4cc3aa198dd363f7a43820eab06ea2932c8e0bf。原 Linaro 下载 URL 本次返回 HTML，因此采用固定 SHA256 的镜像。
- 将从原 QWRT 提取的配置作为输入，在独立输出目录运行 olddefconfig 和 modules_prepare。关键 ARM64、SMP、PREEMPT、模块及页面配置检查通过，完整差异为 51 项。
- 原内核没有启用 MODVERSIONS。脚本从确切原 Image 解析出 8879 个结构化 PREL32 导出记录，生成只供本 GPL 模块 modpost 使用的索引；该索引不是原始 Module.symvers。零 CRC 是未启用版本 CRC 的占位值，没有修改模块 vermagic 或绕过模块加载器。
- 实际编译 fxs_main、fxs_clk、fxs_pcm、fxs_spi，完成模块链接和 modpost。最终构建没有缺失符号警告。
- 60/60 模块导入存在于原内核导出记录；52 个函数地址还与原 kallsyms 对照一致。另 8 个为未列入该 kallsyms 表的数据符号，已核对其导出记录。
- 模块 vermagic 为 5.4.213 SMP preempt mod_unload aarch64；.gnu.linkonce.this_module 大小与原 gpio-button-hotplug.ko 相同，均为 768 字节。

kallsyms 位置及解码结果曾独立使用 [vmlinux-to-elf](https://github.com/marin-m/vmlinux-to-elf) 提交 29eac31a38db48b51c0dc6aae3be0162237601eb 检查；本工程自己的读取器仅针对已校验 SHA256 的确切原 Image，不依赖该外部工具。

## 手动复现

v1.0.6 已取消两个独立电话驱动 Actions，主固件只集成源码包内置驱动。下面保留实验模块的手动编译复现方法。

本地 Ubuntu 24.04 安装 build-essential、git、bc、bison、flex、libelf-dev、libssl-dev、Python venv，再安装 requirements.txt，并把 venv/bin 加到 PATH：

```sh
python3 phone/build_public_driver.py --input /absolute/path/aw1000-qwrt-25.12.2-factory.zip
```

已有固定 QSDK 克隆和 GCC 7.5.0 时，可传 --qsdk 和 --cross。每次运行使用全新 --work 目录。脚本不会将实验模块自动装入主机或路由器，也不会刷写设备。

## 当前边界

公开配置与原配置的 51 项差异包括 LRU_GEN、PGB_QUICK_PATH、WireGuard/密码库回移及若干驱动选项。编译器版本一致但构建来源不同。符号存在、vermagic 和 struct module 大小相同，不能证明所有函数签名与 C 类型布局均一致。

尚未在目标 QWRT 5.4.213 上加载，也没有测试 GPIO、PCM、摘挂机、振铃、呼入呼出和双向音频。该 5.4 模块不能用于当前参考系统的 6.12.112 内核。

主工作流默认 bundled，使用 vendor/aw1000-phone-driver.tar.gz 中固定的实编包。构建会禁用原 DTB 的 /soc/spi@78b8000，释放 SLIC GPIO，集成模块并将电话配置启用，Release 标记为 RJ11 实验版。选择 deferred 可关闭电话；不再接受外部驱动 URL。原作者环境取证材料另见 phone-build-environment.md。

## 静态电话服务复现

主 Actions 安装 gcc-aarch64-linux-gnu、libc6-dev-arm64-cross 与 make 后，由 scripts/phone.py 的 install() 从 vendor/phone-libs.tar.xz 解包全部依赖源码，build_libraries() 编译 libubus.a/libubox.a，再链接新版 fxsd。源归档与每个文件均校验 SHA256；libs 的全部 Git blob 已与固定上游树核对。修改电话或库后可运行主 scripts/build.py 重新编译和链接，不使用不可替换的预编译电话服务或系统共享库。完整编译命令保留在脚本与构建日志中。

1.2.8 的电话服务/界面源码固定 f26f9f5，内核驱动 9 个源文件与上述 5a5c874 构建来源完全相同。当前复用此二进制，sources.lock.json 保存原构建提交与内核源码指纹，构建时逐一核对。manifest 继续表示实际 1.2.5 编译结果。

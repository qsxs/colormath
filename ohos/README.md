# Colormath CPF OHOS 入口

此独立入口直接编译 Fork 中 `colormath/src/commonMain/kotlin` 的全部 45 个上游原始文件，
不修改或复制生产源码。上游版本为 3.6.1（commit `5e776b66ea6ffc5dc5dc360ab88ca5aefbd2a310`），
使用上游 MIT 许可证。来源清单记录 source JAR、POM、Gradle module metadata 与 LICENSE 原件及哈希。

公共坐标为 `io.github.qsxs.cpf.colormath:colormath:<version>`，版本由生产提交、Actions run 和
attempt 共同组成。构建使用 Kotlin/Native 2.4.20-0.1.0-08、Gradle 9.5.0、JDK 17；common metadata
沿用同版本公共编译入口，`ohos_x64` 和 `ohos_arm64` 使用标准 KGP 编译器。生产脚本按实际发现任务、
依赖解析、三次编译、发布到独立 staging、验证来源与完整发布目录的顺序执行。任何已有版本目录都会拒绝写入。

## 已验版本与公开消费

已验根坐标：`io.github.qsxs.cpf.colormath:colormath:3.6.1-cpf11-preview-86f87c1c333f-40461897858-1`。
临时 Maven 根为 `https://qsxs.github.io/cpf-maven/`，可查看[实际根 POM](https://qsxs.github.io/cpf-maven/io/github/qsxs/cpf/colormath/colormath/3.6.1-cpf11-preview-86f87c1c333f-40461897858-1/colormath-3.6.1-cpf11-preview-86f87c1c333f-40461897858-1.pom)或 [GitHub 制品仓库](https://github.com/qsxs/cpf-maven)。

[Fork source `86f87c1c333f`](https://github.com/qsxs/colormath/commit/86f87c1c333f0eb68af52afc88b73d82a536d6cf) 经 [producer 40461897858/1](https://github.com/qsxs/cpf-port-tooling/actions/runs/40461897858/attempts/1) 完成生产与 staging 验证；生产工作流提交为 `49d54d239482838dc187ba6b1ce8c9f8cd8231c0`，共享工具内容锁为 `d0cff88fd1ea2e4f08bb4409f1c802cca0c7f285`。工作流上传 staging 和证据，Pages 由维护者核对后手动发布。[Pages 制品提交 `8b5e6932bb24`](https://github.com/qsxs/cpf-maven/commit/8b5e6932bb2490bca64f40e29e1f113090628274) 的 125 个文件、1,106,922 字节与 staging 经匿名 HTTP 逐字节对齐。

[consumer 40668960640/1](https://github.com/qsxs/cpf-port-tooling/actions/runs/40668960640/attempts/1) 使用工作流提交 `b606affac816befd3b33269e57d9b79b89c31b0f`、Gradle 9.5.0，从公开根 GAV 经 GMM 完成 common metadata、OHOS x64 和 arm64 真实编译；探针为 `Color.toHSL()`，9 个公开对象与 HTTP 校验对齐。该运行不使用 producer 源码或 staging，未单独验证 Gradle 8.11.1。

此结果只证明独立编译消费，链接、运行时、设备和 Android/iOS 回归均未执行；App 尚未切换，MaterialKolor 整体尚不能由此视为完成。

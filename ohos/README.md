# Colormath CPF OHOS 入口

此独立入口直接编译 Fork 中 `colormath/src/commonMain/kotlin` 的全部 45 个上游原始文件，
不修改或复制生产源码。上游版本为 3.6.1（commit `5e776b66ea6ffc5dc5dc360ab88ca5aefbd2a310`），
使用上游 MIT 许可证。来源清单记录 source JAR、POM、Gradle module metadata 与 LICENSE 原件及哈希。

公共坐标为 `io.github.qsxs.cpf.colormath:colormath:<version>`，版本由生产提交、Actions run 和
attempt 共同组成。构建使用 Kotlin/Native 2.4.20-0.1.0-08、Gradle 9.5.0、JDK 17；common metadata
沿用同版本公共编译入口，`ohos_x64` 和 `ohos_arm64` 使用标准 KGP 编译器。生产脚本按实际发现任务、
依赖解析、三次编译、发布到独立 staging、验证来源与完整发布目录的顺序执行。任何已有版本目录都会拒绝写入。

工作流仅上传验证通过的 staging，Pages 发布须单独受控完成。独立消费者仅从公开 Pages 解析该 GAV，
编译 `Color.toHSL()` 的 metadata 和双 ABI 探针并核验实际依赖与制品字节；不包含链接或运行时验收。
未执行远程工作流不能说明编译、发布或消费成功。

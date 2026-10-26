# 最小发布与归档清单

目标：让使用者能准确找到、复现并引用同一个软件版本，同时保留贡献归属和许可声明。本文件是发布操作说明，勾选项是完成标准，不表示仓库已经发版或取得 DOI。

## 1. 固定一个可引用的版本

- [ ] 选择实际要发布的提交，确认版本号。当前项目元数据中的 `0.1.0` 可作为首发候选；只有创建相应标签和 Release 后，才称其为已发布版本。
- [ ] 发布提交包含 `LICENSE`、`NOTICE`、`AUTHORS.md`、`CITATION.cff`、[引用说明](../CITATION.md)、[方法报告](METHODS.md) 和 [复现说明](REPRODUCING.md)，不夹带未完成的改动。
- [ ] 检查 `CITATION.cff` 的作者、名称、版本和许可与发布内容一致；填写实际发布日期。没有 ORCID、机构或对应论文就不填写；未取得 DOI 就暂时省略 `doi`。
- [ ] 在拟发布提交的干净检出中，按复现说明完成离线示例、失败示例及项目要求的检查。记录提交号、环境、命令和实际结果；没有完成的检查直接写明，不以其他提交的测试结果代替。
- [ ] 确认发布源码及随附材料的公开范围：不含凭证、真实用户私密资料或无权再分发的材料；第三方来源与许可声明随相应材料保留。

工程执行者可用以下命令验证引用文件，运行成功后再记录为“已验证”：

```text
uvx --from cffconvert==2.0.0 cffconvert --validate -i CITATION.cff
```

这只是引用格式检查，不验证作者身份、学术原创性或模拟结论。[Zenodo 推荐的 CFF 校验方式](https://help.zenodo.org/docs/github/describe-software/citation-file/)

## 2. 连接 Zenodo，再创建 GitHub Release

1. 由仓库所有者登录 [Zenodo](https://zenodo.org/)，连接 GitHub，并在 [GitHub 集成页面](https://zenodo.org/account/settings/github/) 为 `lcy432/homo-spatialis` 开启归档。该集成要求仓库公开。
2. 确认归档已启用，再将选定提交发布为带版本标签的 GitHub Release，例如 `v0.1.0`。发布说明包含范围、复现命令、核验记录和限制。
3. 检查 Zenodo 实际生成的记录：软件类型、作者、版本、许可、源码文件和 DOI 均应对应这次 Release。仅创建 Git 标签不能代替这一步。

这是 GitHub 官方说明的自动归档顺序；启用后，每次创建 Release 会触发一次新的归档与 DOI。[GitHub 软件归档说明](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content)

最小方案复用现有 `CITATION.cff`，无需再创建 `.zenodo.json`。如果以后添加后者，Zenodo 会优先读取它而忽略 CFF，必须另外保持元数据一致。[Zenodo 元数据优先级](https://help.zenodo.org/docs/github/describe-software/)

## 3. 把 DOI 与对应版本关联起来

- [ ] 打开 DOI 链接，确认落到本次版本记录且文件可以下载；复现实验优先引用具体版本 DOI，项目总体介绍可使用代表所有版本的 DOI。见 [Zenodo 版本引用说明](https://zenodo.org/help/versioning)。
- [ ] 在默认分支的 `CITATION.cff` 中回填该版本 DOI，并保留匹配的 `version`、作者与发布日期；在 GitHub Release 说明中加入同一个 DOI 链接。
- [ ] 回填引用元数据不移动已经发布的标签，不替换已归档的源码。首次自动归档时，归档内的 CFF 没有自身 DOI 是可以说明的正常情况；不必为回填 DOI 再发一个内容相同的版本。
- [ ] 以后发布新版本时，更新版本及作者元数据，并移除旧版本的 DOI 后再触发归档；新记录生成后回填新 DOI，避免把新源码标成旧版本。

## 4. 最少保留这些发布信息

将下面字段填写到 GitHub Release 说明中即可，不另复制一套方法文档。未完成的条目保持“未完成”，不要填推测值。

| 字段 | 内容 |
|---|---|
| 软件 | Homo Spatialis · LLMs Becoming Us |
| 版本 / 标签 | 实际发布版本与标签 |
| 源码提交 | 完整 Git 提交号 |
| 作者 | 对应版本 `CITATION.cff` 中的软件作者 |
| 发布范围 | 本版本实际支持的入口与功能 |
| 复现 | 指向发布标签下的复现说明、锁定依赖与示例；不只链接浮动的 `main` |
| 核验 | 平台、Python 版本、执行命令、实际结果及未执行的检查 |
| 方法与限制 | 指向发布标签下的方法说明，区分工程通过与现实有效性 |
| 引用 | 初次发布写“归档处理中”；成功后替换为该版本 DOI |

完成后，读者应能从引用找到固定源码，从源码找到运行说明，从报告找到实际贡献与限制。归档提供持久引用和公开记录，不能取代同行评审或替作者保证原创性。

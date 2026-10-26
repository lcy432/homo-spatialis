# 引用、署名与使用说明 / Citation and attribution

[English](CITATION.en.md) · 简体中文

If you use Homo Spatialis in research, please cite the software version you used, acknowledge the original authors, and cite the underlying data sources separately. Citation metadata is maintained in [CITATION.cff](CITATION.cff).

## 如何引用

软件名称：**Homo Spatialis · LLMs Becoming Us**。当前软件作者：**Chengyu Liu**。作者与具体贡献见 [作者与贡献者](AUTHORS.md)，模型方法见 [方法报告](docs/METHODS.md)，研究依据见 [参考文献](docs/REFERENCES.md)。作者名单、名称与版本以所用版本的 `CITATION.cff` 为准。

- **使用正式归档版本**：从对应 Zenodo 记录复制引用，使用该版本的 DOI，并注明软件版本。方法或实验中同时记录完整 Git 提交号、资料包、配置、规划器和随机种子（如适用）。
- **使用尚未发布的代码**：引用软件仓库，并补充实际使用的完整 Git 提交号及访问日期。`main` 会变化，不能单独标识实验所用代码；包含未提交改动时，应先保存可追溯的版本或补丁。
- **使用自己的修改版**：引用原项目，同时注明你的分支或归档地址、版本与主要修改，帮助读者区分原始实现与后续工作。

下面是尚无 DOI 时的仓库级 BibLaTeX 条目。它没有虚构发布日期、正式 Release 或对应论文；用于具体实验时，请补充实际提交号和访问日期。传统 BibTeX 样式若不支持 `@software`，可改用 `@misc`。

```bibtex
@software{liu_homo_spatialis,
  author = {Liu, Chengyu},
  title = {Homo Spatialis · LLMs Becoming Us},
  url = {https://github.com/lcy432/homo-spatialis}
}
```

不要把 `pyproject.toml` 或 `CITATION.cff` 中的版本字段单独视为正式发版证明。引用已发布软件时，应核对对应 GitHub Release、标签和归档记录；尚未取得 DOI 时不填写占位 DOI。

`CITATION.cff` 放在 GitHub 默认分支后，可让仓库显示 **Cite this repository** 并提供格式化引用；本文件解释怎样选择正确的引用对象，不另维护一份版本和 DOI 元数据。[GitHub 引用文件说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files)

## 什么应当获得署名

原项目的发起与核心设计归属记录在 [作者与贡献者](AUTHORS.md)，实现与演进记录在源码、Git 历史及 [设计决定](DECISIONS/README.zh-CN.md)。引用具体机制时，可同时指向相应版本的方法报告或设计文档，便于核查实际贡献。

使用城市资料时，应分别说明原始资料发布者、整理或映射贡献者，以及实际使用的数据版本。引用本软件不能替代对底层数据和既有研究的引用。资料出处与映射见 [数据说明](docs/DATA.md)；贡献接收与署名流程由正在维护的 [贡献指南](CONTRIBUTING.zh-CN.md) 规定，本文件不另设一套规则。

## 许可义务与学术引用

本项目的许可文本见 [LICENSE](LICENSE)，相关声明见 [NOTICE](NOTICE)。以下是便于理解的说明，不修改许可证条款：

| 使用情形 | 应区分的义务与请求 |
|---|---|
| 复制、修改和再分发代码 | 遵守 Apache-2.0 的适用条件，包括提供许可证副本、对修改过的文件作醒目标记、保留适用的版权等声明，以及按第 4 节处理相关 NOTICE 内容 |
| 在论文、报告或研究中使用软件或方法 | 我们请求引用实际使用的软件版本，并说明所用方法；这不是 Apache-2.0 之外新增的许可条件 |
| 复用第三方数据、依赖或素材 | 按各自许可和出处要求处理；项目的 Apache-2.0 不会替这些材料重新授权 |
| 商业使用或发布修改版 | Apache-2.0 允许商业使用，也不要求所有修改必须公开；合规使用不因其商业性质而成为侵权 |

Apache-2.0 对再分发和声明保留有明确规定，但不保证使用者在学术论文中引用项目，也不要求每次运行都展示作者。以 [Apache-2.0 原文第 4 节](https://www.apache.org/licenses/LICENSE-2.0) 为准。

版权通常保护代码、文档等具体表达，不保护思想、方法或数学概念本身。因此，这些文件用于明确归属、许可与可核查记录，不能阻止别人独立实现相同研究思路。[WIPO 版权说明](https://www.wipo.int/en/web/copyright/faq-copyright)

## 保存可核查的贡献记录

维护者发布时，将源码、作者声明、方法说明、复现入口和已知限制一起固定到版本，并通过归档平台保存。步骤见 [最小发布与归档清单](docs/RELEASE.md)。公开记录可帮助别人追溯贡献，但 DOI 不等于同行评审、原创性认证或预测有效性证明。

若发现遗漏署名或不符合许可证的复制，先保存相关页面、版本、日期和代码对应关系，再按具体问题请求更正。学术引用遗漏与许可违规应分别判断；存在争议时，以实际许可条件、证据及适用法律为准。

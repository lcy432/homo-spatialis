# 文献与设计来源

[English](REFERENCES.en.md) · 简体中文

[返回文档索引](INDEX.md) · [研究方法](RESEARCH.md) · [生活模拟的方法](METHODS.md)

这里整理项目动机和设计用到的文献，说明每篇工作与项目的具体关系。文献提供问题、方法和对照；项目当前实现见[项目索引](INDEX.md)，具体设计取舍见[决策记录](../DECISIONS/README.zh-CN.md)。

<a id="spatial-economics"></a>
## 从具体的人比较城市

空间均衡中的无差异条件约束边际迁移者，并不要求每个人从各地获得同样的生活。消费结构、技能回报、税收、城市条件和迁移摩擦共同影响个人的选择。项目希望把这些作用放到同一个人的条件下比较，而不是把平均收入或代表性消费篮子当成所有人的尺度。

| 文献 | 与本项目的关系 |
|---|---|
| Roback（1982），*Wages, Rents, and the Quality of Life*；Rosen（1979），*Wage-based Indexes of Urban Quality of Life* | 工资、住房成本与城市条件共同形成区位价值，是空间均衡的基础背景。 |
| [Gibbons、Overman、Resende（2011），*Real Earnings Disparities in Britain*](https://cep.lse.ac.uk/pubs/download/sercdp0065.pdf) | 用实际收入与地区差异讨论补偿性差异；帮助区分边际均衡条件和具体个人的处境。 |
| [Moretti（2013），*Real Wage Inequality*](https://eml.berkeley.edu/~moretti/inequality.pdf) | 美国城市生活成本解释了 1980—2000 年大学工资溢价增长的至少 22%。启发是工资差距需要与居住地成本一起看。 |
| [Handbury（2021），*Are Poor Cities Cheap for Everyone? Non-Homotheticity and the Cost of Living Across U.S. Cities*](https://realestate.wharton.upenn.edu/wp-content/uploads/2022/04/w836.pdf)（[作者稿入口](https://www.jessiehandbury.com/papers/Handbury_NHP_currentpublic.pdf)） | 食品消费中的品种可得性与不同收入群体的需求改变城市成本比较。项目的个人篮子与品种扩展受此启发；三项地区价格指数尚未表达完整的商品组合。 |
| [Albouy（2009），*The Unequal Geographic Burden of Federal Taxation*](https://www.journals.uchicago.edu/doi/10.1086/605309) | 正式发表版本估计，在工资高于平均的城市，相同劳动者支付的联邦税额比工资低于平均的城市高约 27%。税额差异会改变名义高薪的实际价值。 |
| [Diamond（2016），*The Determinants and Welfare Implications of US Workers’ Diverging Location Choices by Skill: 1980–2000*](https://www.aeaweb.org/articles?id=10.1257/aer.20131706) | 工资、租金、城市条件与技能分选共同影响福利差距，支持按人的技能和偏好分析城市。 |
| [Diamond、Moretti（2024 年 8 月稿），*Where is Standard of Living the Highest? Local Prices and the Geography of Consumption*](https://web.stanford.edu/~diamondr/DiamondMoretti.pdf) | 比较当地价格和不同收入、教育群体的消费，延续了“同一城市对不同的人意味着什么”的问题。消费结果与包含非市场城市条件的个人效用分别处理。 |
| Kaplan、Schulhofer-Wohl（2017），*Inflation at the Household Level* | 家庭面对的价格变化具有异质性，为保留个人消费结构提供背景。 |

这些作用不必指向同一个方向。高工资可能伴随高房租和高税额，也可能对应更适合某种技能或爱好的机会。项目要研究的是它们对一个给定的人怎样共同起作用。

<a id="ai-preferences"></a>
## LLM 能否理解和保留个人差异

| 文献 | 与本项目的关系 |
|---|---|
| [*Homo Silicus*](https://arxiv.org/html/2301.07543v2) | 给模型指定信息与偏好，再观察其经济选择。项目据此把代理行为放到可以控制条件的实验中。 |
| [Kim 等，*Can an LLM Learn Preferences from Choice Data?*（v3）](https://arxiv.org/html/2401.07345v3) | 在已知偏好下生成选择、让模型学习并独立评价，观察不同偏好成分和人群的学习差异。它是本项目研究分组损失的重要出发点。 |
| [*Synthetic Personalities*](https://arxiv.org/html/2606.04592v1) | 输入材料和信息深度影响个人模拟，提示需要比较画像的获取方式与内容。 |
| [*Distribution-First Population Simulation*](https://arxiv.org/html/2607.18310v1) | 群体分布与个体准确性是不同目标；只看平均输出容易漏掉差异。 |
| [Zhao 等（2025），*Do LLMs Recognize Your Preferences?*（PrefEval）](https://arxiv.org/abs/2502.09597) | 将偏好提取、记忆和实际遵循分开检查，对应项目的画像、事实记忆和行动评价。 |

这些工作的任务并非三年城市生活。项目采用其可控实验与分组评价思路，城市选择和长期行动仍由本项目的任务检验。

<a id="agent-simulation"></a>
## 让代理在环境中生活

| 文献 | 与本项目的关系 |
|---|---|
| [Park 等（2023），*Generative Agents: Interactive Simulacra of Human Behavior*](https://arxiv.org/abs/2304.03442) | 记忆和持续行动为生活代理提供设计参照。 |
| [*AgentSociety*，§3.6、§4.1、§4.4](https://arxiv.org/html/2502.08691v2)（[代码](https://github.com/tsinghua-fib-lab/AgentSociety)） | 行动、反馈与状态更新分开，环境处理账目、成本和约束。项目沿用这种分工，由语言模型计划、经济模型执行。 |
| [*Agentopia: Long-Term Life Simulation and Learning in Agent Societies*，§4.1](https://arxiv.org/html/2606.07513v1) | 持续生活、分周期结算与多维结果启发了项目流程。项目另行定义自己的评分和跨分身汇总，不采用论文中的 life reward。 |
| [Feng 等，*CityBench*（2025 版本）](https://arxiv.org/abs/2406.13945v3) | 真实城市资料先成为环境对象，再通过行动接口交给模型。 |
| [Xia 等（2026），*Empirical Grounding Improves the Realism of LLM Agents Simulating Human Behavior During Disruptions*](https://arxiv.org/abs/2607.17437) | 经验资料进入结构化属性和约束，资料拟合与行为检验分别进行。 |
| [Santos 等（2026），*When Plausible Is Not Realistic*](https://arxiv.org/abs/2606.13835) | 分别检查空间、时间与行为结果，区分叙述可信和结果符合现实。 |
| [Koaik 等（2026），*LLM Powered Social Digital Twins*](https://arxiv.org/abs/2601.06111v2) | 环境资料、代理行为和结果评价分别验证；校准资料与检验资料分开。 |
| [Xu 等（2026），*Towards Fully Automated City Operations: Integrating Agentic AI with Urban Digital Twins*](https://discovery.ucl.ac.uk/id/eprint/10225208/1/Batty_ssrn-5596992.pdf) | 流程组织、数据处理与专业计算工具保持分工，作为架构参照。 |
| [美国国家科学院等（2024），*Foundational Research Gaps and Future Directions for Digital Twins*](https://www.nationalacademies.org/read/26894/chapter/2) | 资料更新、模型验证与决策反馈的关系。项目目前定位于真实资料支持的生活模拟。 |
| [Zhang 等，*Agentic Economic Modeling*（2026 修订版）](https://arxiv.org/abs/2510.25743v4) | 后续真人行为校正与独立检验的研究方向。 |

AgentSociety 和 Agentopia 的环境也包含各自的模型假设与生成式成分；引用它们不意味着本项目的数学核是现实真值。环境规则是否主导结果，本身就是本项目的[研究问题](RESEARCH.md#research-questions)。

<a id="interviews"></a>
## 用户输入与访谈

访谈式输入计划在 V3 引入。先保留用户原始表达，再区分明确陈述、模型推断与用户确认的画像，是这些文献对当前接入设计最直接的影响。

| 文献 | 与本项目的关系 |
|---|---|
| [Radlinski 等（2019），*Coached Conversational Preference Elicitation: A Case Study in Understanding Movie Preferences*](https://aclanthology.org/W19-5941/) | 具体经历以及喜欢或不喜欢的原因，可用于澄清偏好。 |
| [Handa 等（2024），*Bayesian Preference Elicitation with Language Models*（OPEN）](https://arxiv.org/abs/2403.05534v1) | 提问可以减少取舍的不确定性；推断值与明确回答需要区分。主动提问与贝叶斯估计尚属后续方向。 |
| [Park 等，*LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals*（2026 修订版）](https://arxiv.org/abs/2411.10109v3) | 原题为 *Generative Agent Simulations of 1,000 People*。结构问卷和访谈是可比较、可组合的信息来源；画像的含义与采集方式分开设计。 |
| [Jain 等（2026），*Decisive*](https://aclanthology.org/2026.acl-long.1465/) | 选项事实与用户取舍分开；两两比较提供偏好证据，而非自动确定唯一权重。 |
| [Yang 等（2022），*Eliciting risk preferences that predict risky health behavior*](https://pmc.ncbi.nlm.nih.gov/articles/PMC9305924/) | 提问方式和措辞影响风险回答。当前健康输入用于规划信息，不直接反推疾病概率或风险厌恶参数。 |

访谈能否改善本项目的城市比较，需要在相同任务和评价规则下与现有输入方法比较。

<a id="data-mapping"></a>
## 从统计资料到生活机会

机会生成有两类思路：先覆盖合理组合再用本地资料校准，或按资料支持的条件关系生成。人口合成方法为它们提供工具，但居民已经作出的选择与新用户能够获得的机会仍需区分。当前方案见[真实资料设计](design/intake-02-real-data.md)和[生活资料映射](design/e3b-mapping-and-daily-support.md)。

| 文献 | 与本项目的关系 |
|---|---|
| [Xu 等（2025），*Developing an Automated Microscopic Traffic Simulation Scenario Generation Tool*（RealTwin）](https://doi.org/10.1177/03611981251349433) | 原始资料、统一事实、场景映射与模拟器适配分层，并保留对象之间的对应关系。 |
| [Sun、Erath（2015），*A Bayesian network approach for population synthesis*](https://doi.org/10.1016/j.trc.2015.10.010) | 用条件分布表达属性相关性，作为机会生成方法的参考。 |
| [Ye 等（2017），*Population Synthesis Based on Joint Distribution Inference Without Disaggregate Samples*](https://www.jasss.org/20/4/16.html) | 用交叉表和统计约束重建、校准分布；边缘统计不足以唯一确定联合关系。 |
| [Jutras-Dubé 等（2024 版本），Copula 迁移生成](https://arxiv.org/abs/2302.09193v3) | 面向资料稀疏城市与跨国扩展的候选方法，需独立检验迁移来的相关关系。 |
| [Kukic、Bierlaire（2025），*Adaptive synthetic generation using one-step Gibbs Sampler*](https://transp-or.epfl.ch/documents/technicalReports/KukicBier2025.pdf) | 后续资料更新与合成生成的参考。 |
| [Rosen（1974），*Hedonic Prices and Implicit Markets: Product Differentiation in Pure Competition*](https://doi.org/10.1086/260169) | 住房价格共同反映多种属性；住房本身、地区条件和交通分别表达，租金只是品质推断的证据之一。 |
| [Albouy（2016），*What Are Cities Worth? Land Rents, Local Productivity, and the Total Value of Amenities*](https://www.nber.org/papers/w14981) | 工资和住房成本还反映生产率、区位与税。住房品质推断要控制这些差异；链接为对应工作论文页。 |
| [Sen 等，*MoveOD: Synthesizing Origin-Destination Commute Distribution from U.S. Census Data*（v2）](https://arxiv.org/html/2510.18858v2) | 用 LODES 居住—工作联系、ACS 通勤分布与空间网络约束通勤映射；当前先采用片区关系表。 |
| [Fay、Herriot（1979），*Estimates of Income for Small Places: An Application of James-Stein Procedures to Census Data*](https://math.umd.edu/~plahiri/SAE/Papers/FayHerriot1979.pdf) | 小地区资料不足时借用更广样本的信息，同时保留估计误差与缺失标记。 |
| [del Rio-Chanona 等（2021），*Occupational mobility and automation: a data-driven network model*](https://pmc.ncbi.nlm.nih.gov/articles/PMC7879770/) | 职业之间有不同的转移路径与阻力；项目用既有能力、培训成果和岗位要求表达这些边界。 |
| [Waddell（2002），*UrbanSim: Modeling Urban Development for Land Use, Transportation, and Environmental Planning*](https://doi.org/10.1080/01944360208976274) | 住房、就业和交通通过明确的空间对象衔接。 |

这些方法帮助组织资料，并不能直接把工资估计当录用概率、把租金当住房质量，或把历史职业流动当培训的因果效果。具体映射假设及参数集中在[参数与假设目录](design/parameter-assumption-register.md)。

<a id="measurement"></a>
## 选择模型与测量

[`RESEARCH.md`](RESEARCH.md)中的混合 logit、偏好回收和模拟积分以 [Train，*Discrete Choice Methods with Simulation*](https://eml.berkeley.edu/books/train1201.pdf) 为主要方法参考。以下文献与量表保留为选择、迁移、异质性和生活评价的领域背景：

- McFadden（1978），*Modelling the Choice of Residential Location*。
- Bayer、Ferreira、McMillan（2007）；Kennan、Walker（2011）。
- Lenk、DeSarbo、Green、Young（1996），*Hierarchical Bayes conjoint analysis: recovery of partworth heterogeneity from reduced experimental designs*。
- Louviere、Flynn、Marley，*Best-Worst Scaling*。
- Schwartz 的 Portrait Values Questionnaire；WHOQOL-BREF；OECD Better Life Index 与 Regional Well-being。

项目的输入权重与生活评分由本项目定义，并未直接采用以上量表的完整测量模型。

<a id="data-sources"></a>
## 数据与方法入口

| 来源 | 用途 |
|---|---|
| [O*NET 数据库](https://www.onetcenter.org/database.html) | 职业与技能要求。 |
| [BEA 地区价格平价](https://www.bea.gov/data/prices-inflation/regional-price-parities-state-and-metro-area/) | 跨地区价格水平与有基准报价的成本换算；指数不直接作商品单价。 |
| [EPA Smart Location](https://www.epa.gov/smartgrowth/smart-location-mapping) | 交通与可达性资料筛选。 |
| [FBI 2024 资料说明](https://www.fbi.gov/news/press-releases/fbi-releases-2024-reported-crimes-in-the-nation-statistics) | 犯罪统计的覆盖和口径，缺报与零事件分开。 |
| [NIST 可靠性手册](https://www.itl.nist.gov/div898/handbook/apr/section1/apr161.htm) | 区分使用寿命和随机故障；常数风险可作近似，具体商品参数需要另有资料。 |

已获取的数据、字段来源、转换和使用范围以数据构建记录为准；这份阅读清单不代替数据清单或发布方许可。

## 待核对的阅读线索

旧文档还记录了 *Polypersona: Persona-Grounded LLM for Synthetic Survey Responses* 与 *Do large language models shop like people? Comparing GPT and human food choices using discrete choice experiments*。这两项保留供后续核对，不用于支撑当前设计结论。

历史讨论见[设计索引](design/README.zh-CN.md)、[决策记录](../DECISIONS/README.zh-CN.md)与[文档存档](history/)。

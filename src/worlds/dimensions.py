"""七个维度的规范定义。整个项目对"维度"的唯一真源。

README §6.2 从 WHOQOL-BREF 的域与 facet 结构出发，滤掉不随地点变化的
facet，剩九项，再合并到七项进入定和量表。这里把那七项定死：

    material 一项  + 舒适度六项 = 七维

``material`` 就是 §7.2 里的 w₁——"物质相对于环境的总权重"。它在效用函数
里进一步被 CEX 份额劈成 α 与 β，但用户只看到一个滑块，量表上也只占一格。
其余六项是 θ，直接进 Π A_k^θ_k。

--------------------------------------------------------------------------
为什么少了"社会关系"
--------------------------------------------------------------------------

§6.2 的映射表里第九项是社会关系，重解释为"与用户指定锚点的邻近性"。它
在 V1 里**不实现**，原因是结构性的而不是工作量：其余六项都是纯粹的城市
属性，一座城市的治安对谁都一样；而邻近性是 (人, 城市) 的函数——同一座
城市对父母在本地的人和对父母在两千公里外的人，值完全不同。

把它放进 CityRecord.amenities_scaled 就等于承认城市属性依赖于人，那会让
世界轴与人设轴重新黏在一起，而 §11.9 明确要求两轴正交。正确的做法是给
它一套单独的锚点机制（persona 带一个锚点城市，邻近性在打分时现算），
那是一块独立的设计，留给 V2。

代价要说清楚：迁移决策里"离家多远"往往是最重的一维，V1 不覆盖它。这是
已知缺口，不是疏忽。
"""

from __future__ import annotations

# 物质维。它不在 theta 里，走 §7.2 的两通道映射变成 alpha 与 beta。
MATERIAL = "material"

# 六个舒适度维，全部是纯城市属性。顺序固定，因为特征列的顺序依赖它。
AMENITY_DIMENSIONS: tuple[str, ...] = (
    "safety",  # 人身安全：犯罪率
    "healthcare",  # 医疗与社会照护可及性：医疗资源密度
    "opportunity",  # 获取信息与技能的机会：教育资源、职业发展
    "leisure",  # 休闲参与：文体设施密度
    "environment",  # 物理环境：空气质量、日照、气温分布
    "commute",  # 交通：通勤时长、公共交通覆盖
)

# 定和量表上的七格。
ALL_DIMENSIONS: tuple[str, ...] = (MATERIAL, *AMENITY_DIMENSIONS)

N_DIMENSIONS = len(ALL_DIMENSIONS)
N_AMENITIES = len(AMENITY_DIMENSIONS)

# 语义方向：全部是"越大越好"。标度函数在 ledger 层负责把原始量翻成这个
# 方向（犯罪率越低 -> safety 越高，通勤越短 -> commute 越高）。M2 与 M3
# 都假定这一点，反向的量混进来不会报错，只会静默地把排序搞反。
POLARITY_NOTE = "all amenity scale values are oriented so that larger is better"

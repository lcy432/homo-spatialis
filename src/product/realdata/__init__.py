"""接入层真实资料（intake-02）：获取、标准化、A / B 映射、机会注册表、菜单、查询与报告。

只在接入层：不改生活引擎（``econ``）、运行器、行动语义、规划器、用户画像或界面。LangGraph 只在 ``workflows`` 里用；
这里的业务函数可以脱离流程图直接调用与测试。

模块：

* ``fetch``：带缓存的获取（原始文件按请求内容寻址存在 ``data/raw/intake/``，不进仓库）；离线模式只读缓存。
* ``config``：资料构建配置（验证都会区、来源、方法参数、菜单政策与种子）。
* ``geo``：CBSA 划定与 PUMA 归属（2023 年都会区、2020 年 PUMA）。
* ``prices``：时期换算（CPI-U），只在有明确依据时生成 ``USD_2024_LOCAL`` 估计值。
* ``housing``、``jobs``、``training``、``food``、``goods``、``activities``、``background``：各板块的标准化。
* ``mapping_b``：由真实资料估计的条件关系（住房租金与通勤、岗位工时与在家工作比例）。
* ``mapping_a``：有覆盖的候选组合，再按城市资料校准（住房分层骨架、食品方案）。
* ``registry``：稳定的机会注册表与四种能力、编译状态。
* ``menus``：菜单政策与抽样（不含分身编号的抽样键）。
* ``store``、``query``：冻结的 SQLite 库与分页筛选查询。
* ``reports``：覆盖矩阵、来源统计、映射诊断、菜单大小诊断。
* ``e1_export``：把当前能表达的机会导出成 E1 机会并做合法性检查（不宣称产品链已接真实资料）。
* ``build``：按阶段组织的构建函数（配置 → 获取或读缓存 → 标准化 → 映射 → 目录与菜单 → 检查 → 冻结）。
* ``publish``：把冻结库的清单、覆盖矩阵、映射诊断与菜单索引写成可以提交的小文件。
"""

import contextlib
import warnings
from collections.abc import Iterator


@contextlib.contextmanager
def quiet_xlsx() -> Iterator[None]:
    """BLS 的 xlsx 没有默认样式，openpyxl 每读一次就警告一次；与内容无关，读这些文件时只滤掉这一条。"""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        yield

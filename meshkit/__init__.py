# -*- coding: utf-8 -*-
"""meshkit -- ANSYS Mechanical 2022 R1 自动网格 + 质量分析 + 自动纠正工具包。

目标运行环境
------------
ANSYS Mechanical 2022 R1（安装目录 ``AWP_ROOT221``）内置 IronPython 2.7.4。
在 Mechanical 的 Automation -> Scripting 控制台里::

    execfile(r"D:\\ai\\meshkit\\run_mesh.py")

模块划分
--------
compat    : Python 2/3 兼容层、日志、数值格式化
config    : 需求配置（JSON）加载/合并/校验、预设
session   : ExtAPI 访问层（特征探测、安全 setter、几何/网格数据）
metrics   : 质量指标采集、力学限值读取、可选单元级抽样
controls  : 网格控制项的读/建/改、状态快照与回滚
mesher    : 应用全局设置 + 控制项，并带验证的重划网格
diagnose  : 规则引擎：现象 -> 根因 -> 可执行动作
repair    : 自动纠正阶梯（**只动网格设置**），可回滚
advisor   : 自动纠正用尽后的人工决策清单 + 可执行修复脚本生成
report    : Markdown / TXT / CSV 报告
engine    : 编排闭环（建网格 -> 评质量 -> 诊断 -> 纠正 -> 再评 -> 定论）
"""

__version__ = "1.0.0"
__all__ = [
    "compat", "config", "session", "metrics", "controls",
    "mesher", "diagnose", "repair", "advisor", "report", "engine",
]

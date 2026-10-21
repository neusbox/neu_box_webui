"""NeuOps (master) package.

NeuOps 是 Neu Box 的中心节点：节点池管理、任务转发、实验记录、
AI 辅助运维工单与知识库。2026-08-25 从 ``neu_box`` 单体仓库拆分，
现以独立包 ``neuops`` 维护。

共享件（config / logging_config / database 迁移引擎）为本仓库自有副本，
与 neu_box 仓库的副本同源但各自独立维护。
"""

__version__ = "0.2.0"

# 与 worker 的 HTTP 契约版本；重命名包名不改变该契约。
API_VERSION = 2

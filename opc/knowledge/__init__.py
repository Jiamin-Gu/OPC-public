"""公司知识库：独立组件，收录公司文件区的文档和代码、任务的汇报和审查记录，支持关键词、向量和混合检索。

目前不接入任何角色 agent，只通过命令行（python -m opc.knowledge）和网页上的只读搜索使用。
以后 OPC 调用编码 agent 做大项目时，可以从这里检索公司积累的需求、方案、代码和决策。
"""
from .base import KnowledgeBase
from .company import open_kb, sync_company, task_documents

__all__ = ["KnowledgeBase", "open_kb", "sync_company", "task_documents"]

"""配置：默认模型从 .env 读取；每个角色可以在创建公司时单独覆盖模型、接口地址和密钥。"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from . import providers

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 依次读取：当前目录、项目根目录、~/.opc/ 下的 .env（排在前面的优先）
load_dotenv(Path.cwd() / ".env")
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(os.getenv("OPC_HOME") or Path.home() / ".opc") / ".env")


def opc_home() -> Path:
    """公司数据（配置、文件、LangGraph 检查点）保存的位置。"""
    return Path(os.getenv("OPC_HOME") or Path.home() / ".opc")


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    provider: str = "deepseek"  # 服务商预设（见 providers.py），决定要不要加额外的请求头


def default_llm_config() -> LLMConfig:
    base_url = os.getenv("OPC_BASE_URL", "")
    # 没写服务商时按接口地址判断，兼容旧配置
    provider = os.getenv("OPC_PROVIDER") or (providers.detect(base_url) if base_url else "deepseek")
    return LLMConfig(
        api_key=os.getenv("OPC_API_KEY", ""),
        base_url=base_url or providers.get(provider).base_url or "https://api.deepseek.com",
        model=os.getenv("OPC_MODEL", "deepseek-flash"),
        provider=provider,
    )


MAX_ROLE_STEPS = int(os.getenv("OPC_MAX_ROLE_STEPS", "12"))  # 每个角色完成一个任务最多调用模型的次数
MAX_REVIEW_ROUNDS = int(os.getenv("OPC_MAX_REVIEW_ROUNDS", "2"))  # 审查最多来回几轮，还不通过就上报 CEO 裁决

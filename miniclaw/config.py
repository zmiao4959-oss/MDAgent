import os
import yaml
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from dataclasses import dataclass, field
from typing import Mapping, Optional, Dict, Any
from dotenv import load_dotenv
load_dotenv()  


# 工作区路径
"""
从系统环境变量中读取 MINICLAW_WORKSPACE 的值。
如果这个环境变量存在，就使用它的值作为工作空间路径。
如果不存在，则默认使用用户主目录下的 .miniclaw/workspace
（例如 Linux 上是 /home/用户名/.miniclaw/workspace，Windows 上是 C:/Users/用户名/.miniclaw/workspace）。
最终把得到的路径字符串包装成 Path 对象（pathlib.Path），赋值给变量 WORKSPACE_DIR，方便后续以面向对象的方式操作路径。
"""
WORKSPACE_DIR = Path(os.environ.get("MINICLAW_WORKSPACE", Path.home() / ".miniclaw" / "workspace"))
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
_ACTIVE_WORKSPACE: ContextVar[Optional[Path]] = ContextVar("miniclaw_active_workspace", default=None)


def active_workspace_dir() -> Path:
    """Return the workspace bound to the current Agent request."""
    return (_ACTIVE_WORKSPACE.get() or WORKSPACE_DIR).resolve()


@contextmanager
def workspace_scope(workspace: Optional[Path | str]):
    """Temporarily bind tool and visualisation code to one project workspace."""
    if not workspace:
        yield WORKSPACE_DIR.resolve()
        return
    path = Path(workspace).resolve()
    path.mkdir(parents=True, exist_ok=True)
    token = _ACTIVE_WORKSPACE.set(path)
    try:
        yield path
    finally:
        _ACTIVE_WORKSPACE.reset(token)

# 集中定义工作区文件路径
MEMORY_FILE = "MEMORY.md"


def resolve_config_path(
    workspace_dir: Path = WORKSPACE_DIR,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """Return the configuration file to load without creating one implicitly.

    A user-specific config next to the workspace wins when it exists.  On a
    fresh install, fall back to the example shipped with MiniClaw so that
    ``python main.py`` uses the repository's documented defaults.  An explicit
    ``MINICLAW_CONFIG`` always wins, which is useful for deployments and tests.
    """
    env = os.environ if environ is None else environ
    explicit = env.get("MINICLAW_CONFIG")
    if explicit:
        return Path(explicit).expanduser()

    user_config = workspace_dir.parent / "config.yaml"
    if user_config.exists():
        return user_config

    return Path(__file__).resolve().with_name("config.yaml")

#  YAML 是一种人类可读的数据序列化语言，常用来写项目配置（缩进表示层级，类似 JSON 但更简洁）
def _load_yaml(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


@dataclass
class LLMConfig:
    """LLM 提供者配置"""
    provider: str = "deepseek"          # 默认提供者
    model: str = "deepseek-v4-pro"
    #model: str = "deepseek-chat"
    api_key: str = ""                   # 从环境变量 MINICLAW_API_KEY 读取
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.7
    max_tokens: int = 8192
    # 多提供者列表（用于降级）
    fallback_providers: list = field(default_factory=list)
    # 调用 obj.resolved_api_key 时，不需要加括号 ()，看起来就像在访问一个普通属性
    @property
    def resolved_api_key(self) -> str:
        """Resolve an explicit key before provider-specific and shared env vars."""
        provider_var = f"{self.provider.upper().replace('-', '_')}_API_KEY"
        return (
            self.api_key
            or os.environ.get(provider_var, "")
            or os.environ.get("MINICLAW_API_KEY", "")
        )


@dataclass
class GatewayConfig:
    """Gateway 网关配置"""
    host: str = "127.0.0.1"
    port: int = 18789
    auth_token: str = ""                # 共享密钥


@dataclass
class ChannelConfig:
    """渠道配置"""
    enabled: Dict[str, bool] = field(default_factory=dict)
    settings: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Agent 行为配置"""
    max_tool_rounds: int = 15           # 单次对话最多执行工具轮数
    max_context_tokens: int = 80000     # 上下文超出后触发压缩
    compaction_keep_messages: int = 20  # 压缩时保留最近 N 条消息
    heartbeat_interval_min: int = 30    # 心跳间隔（分钟）
    thinking: str = "adaptive"          # on | off | adaptive
    task_timeout_sec: int = 600         # 单次 Agent 任务整体超时（秒）
    sse_keepalive_sec: int = 15         # SSE 流无事件超时断连检测（秒）
    planning: "PlanningAgentConfig" = None  # type: ignore

    def __post_init__(self):
        if self.planning is None:
            self.planning = PlanningAgentConfig()


@dataclass
class ExecutionConfig:
    """子进程执行配置（execute 工具）"""
    default_timeout_sec: int = 60       # execute 工具默认超时
    max_timeout_sec: int = 900          # execute 工具最大超时


@dataclass
class PlanningAgentConfig:
    """Planning Agent 配置"""
    max_tool_rounds: int = 5            # 规划阶段最多工具轮数
    temperature: float = 0.3            # 规划阶段温度（更低=更确定）


class Config:
    """全局单例配置"""
    _instance = None
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or resolve_config_path()
        raw = _load_yaml(self.config_path)
        
        # 解析各子配置
        llm_raw = raw.get("llm", {})
        # llm_raw.get("provider", "deepseek") 表示如果 llm_raw 中没有 provider 键，则使用 "deepseek" 作为默认值
        # 至于为什么上面的class里面也加入默认值，就是说如果LLMConfig()，即没有输入任何参数，则使用上面class里配置的"deepseek"作为默认值
        # 相当于代码里可以只在LLMConfig里配置一两个他想配置的，其他都用默认值
        fallback_providers = []
        for fb in llm_raw.get("fallback_providers", []):
            fallback_providers.append(LLMConfig(
                provider=fb.get("provider", "openai"),
                model=fb.get("model", "gpt-4o-mini"),
                api_key=fb.get("api_key", ""),
                base_url=fb.get("base_url", "https://api.openai.com/v1"),
            ))
        self.llm = LLMConfig(
            provider=llm_raw.get("provider", "deepseek"),
            model=llm_raw.get("model", "deepseek-chat"),
            api_key=llm_raw.get("api_key", ""),
            base_url=llm_raw.get("base_url", "https://api.deepseek.com/v1"),
            temperature=llm_raw.get("temperature", 0.7),
            max_tokens=llm_raw.get("max_tokens", 4096),
            fallback_providers=fallback_providers,
        )
        
        gateway_raw = raw.get("gateway", {})
        self.gateway = GatewayConfig(
            host=gateway_raw.get("host", "127.0.0.1"),
            port=gateway_raw.get("port", 18789),
            auth_token=gateway_raw.get("auth_token", os.environ.get("MINICLAW_AUTH_TOKEN", "")),
        )
        
        self.channels = ChannelConfig(
            enabled=raw.get("channels", {}).get("enabled", {}),
            settings=raw.get("channels", {}).get("settings", {}),
        )
        
        agent_raw = raw.get("agent", {})
        planning_raw = agent_raw.get("planning", {})
        execution_raw = raw.get("execution", {})
        self.agent = AgentConfig(
            max_tool_rounds=agent_raw.get("max_tool_rounds", 15),
            max_context_tokens=agent_raw.get("max_context_tokens", 80000),
            compaction_keep_messages=agent_raw.get("compaction_keep_messages", 20),
            heartbeat_interval_min=agent_raw.get("heartbeat_interval_min", 30),
            thinking=agent_raw.get("thinking", "adaptive"),
            task_timeout_sec=agent_raw.get("task_timeout_sec", 600),
            sse_keepalive_sec=agent_raw.get("sse_keepalive_sec", 15),
            planning=PlanningAgentConfig(
                max_tool_rounds=planning_raw.get("max_tool_rounds", 5),
                temperature=planning_raw.get("temperature", 0.3),
            ),
        )
        self.execution = ExecutionConfig(
            default_timeout_sec=execution_raw.get("default_timeout_sec", 60),
            max_timeout_sec=execution_raw.get("max_timeout_sec", 900),
        )
    """
    @classmethod 也是一个装饰器，它把方法变成类方法，与普通实例方法的区别在于：
    普通方法第一个参数是 self（实例本身）
    类方法第一个参数是 cls（类本身）

    从功能上讲，con = Config(path) 当然可以运行
    但需要注意：这意味着你放弃了单例模式。原来的 Config.get_instance(path) 设计的初衷就是为了让整个程序里只有一个 Config 对象
    """
    @classmethod
    def get_instance(cls, config_path: Optional[Path] = None) -> "Config":
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

# 便捷导入
config = Config.get_instance()

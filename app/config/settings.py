"""应用配置模块

提供 AppConfig 数据类和 load_config 工厂函数，
支持环境变量、.env 文件和默认值三级配置优先级。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_ENV_PREFIX = "OA_"

_ENV_MAP: dict[str, str] = {
    "OA_OLLAMA_URL": "ollama_base_url",
    "OA_OLLAMA_MODEL": "ollama_model",
    "OA_API_KEY": "api_key",
    "OA_DATA_DIR": "data_dir",
    "OA_SESSIONS_DIR": "sessions_dir",
    "OA_CHROME_PROFILE_DIR": "chrome_profile_dir",
    "OA_CHROME_DEBUG_PORT": "chrome_debug_port",
    "OA_MAX_HISTORY": "max_history",
}

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """应用全局配置。

    所有路径字段均使用 pathlib.Path 表示绝对路径。
    """

    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5"
    api_key: str = "ollama"               # Ollama 不需要真实 key，DeepSeek 等需要
    data_dir: Path = field(default_factory=lambda: Path("data"))
    sessions_dir: Path = field(default_factory=lambda: Path("data/sessions"))
    chrome_profile_dir: Path = field(default_factory=lambda: Path("data/chrome_profile"))
    chrome_debug_port: int = 9222
    max_history: int = 20

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{f}={getattr(self, f)!r}" for f in self.__dataclass_fields__
        )
        return f"AppConfig({fields})"


# ---------------------------------------------------------------------------
# .env 解析（零依赖）
# ---------------------------------------------------------------------------


def _parse_dotenv(path: Path) -> dict[str, str]:
    """手动解析 .env 文件，返回键值对字典。

    仅支持 KEY=VALUE 格式，忽略空行和 # 开头的注释行。
    """
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def load_config(env_prefix: str | None = None) -> AppConfig:
    """加载应用配置。

    优先级（由低到高）：
    1. AppConfig 类默认值
    2. 项目根目录下的 .env 文件（若存在）
    3. 环境变量（以 OA_ 为前缀）

    加载后自动创建必要的目录（sessions_dir、chrome_profile_dir）。

    Parameters
    ----------
    env_prefix : str | None
        环境变量前缀，默认为 ``"OA_"``。

    Returns
    -------
    AppConfig
        构建完成的配置对象。
    """
    prefix = env_prefix if env_prefix is not None else _ENV_PREFIX

    overrides: dict[str, str | int] = {}

    # ---- .env 文件 ----
    project_root = Path(__file__).resolve().parent.parent.parent
    dotenv_path = project_root / ".env"
    for key, value in _parse_dotenv(dotenv_path).items():
        if key.startswith(prefix):
            mapped = _ENV_MAP.get(key)
            if mapped:
                overrides[mapped] = value

    # ---- 环境变量（覆盖 .env） ----
    for env_key, attr in _ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is not None:
            overrides[attr] = env_val

    # ---- 构建 AppConfig ----
    config = AppConfig()

    # int 类型字段列表
    int_fields = {"chrome_debug_port", "max_history"}

    for attr, raw in overrides.items():
        if attr in int_fields:
            setattr(config, attr, int(raw))
        elif attr.endswith("_dir"):
            setattr(config, attr, Path(raw))
        else:
            setattr(config, attr, str(raw))

    # ---- 路径转换为绝对路径 ----
    for attr in ("data_dir", "sessions_dir", "chrome_profile_dir"):
        p = Path(getattr(config, attr))
        if not p.is_absolute():
            p = project_root / p
        setattr(config, attr, p)

    # ---- 自动创建目录 ----
    for attr in ("sessions_dir", "chrome_profile_dir"):
        p: Path = getattr(config, attr)
        p.mkdir(parents=True, exist_ok=True)

    return config

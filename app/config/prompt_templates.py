"""
Prompt 模板模块
---------------
为千问模型（via Ollama）构建 system/user prompt，
将用户自然语言转换为结构化 JSON 任务参数。
"""

# ============================================================
# JSON Schema —— LLM 输出的结构化定义
# ============================================================
TASK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_type": {
            "type": "string",
            "enum": [
                "form_filling",
                "data_extraction",
                "file_organize",
                "batch_rename",
                "excel_report",
                "web_monitor",
                "unknown",
                "general_chat",
            ],
            "description": "任务类型标识",
        },
        "system_name": {
            "type": "string",
            "description": "目标系统名称，如'OA报销'，无则为空字符串",
        },
        "mode": {
            "type": "string",
            "enum": ["A", "B", ""],
            "description": "浏览器模式：A=独立浏览器，B=复用用户当前Chrome页面，空=不适用",
        },
        "params": {
            "type": "object",
            "properties": {
                "data_source": {
                    "type": "string",
                    "description": "数据源文件路径",
                },
                "target_file": {
                    "type": "string",
                    "description": "目标文件路径",
                },
                "field_mapping": {
                    "type": "object",
                    "description": "字段名到列号的映射",
                    "additionalProperties": {"type": "string"},
                },
                "organize_rule": {
                    "type": "string",
                    "description": "整理规则描述",
                },
                "url_hint": {
                    "type": "string",
                    "description": "目标网页URL提示",
                },
                "additional_notes": {
                    "type": "string",
                    "description": "补充说明",
                },
            },
            "description": "任务参数，按需填充",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "置信度",
        },
        "needs_clarification": {
            "type": "boolean",
            "description": "是否需要向用户追问澄清",
        },
        "clarification_question": {
            "type": "string",
            "description": "追问内容，不需要时为空字符串",
        },
    },
    "required": [
        "task_type",
        "system_name",
        "mode",
        "params",
        "confidence",
        "needs_clarification",
        "clarification_question",
    ],
}

# ============================================================
# 工具描述 —— 每种任务类型的中文说明
# ============================================================
TOOL_DESCRIPTIONS: dict[str, str] = {
    "form_filling": "表单填写 - 从Excel读取数据，填入网页系统的表单中",
    "data_extraction": "数据提取 - 从网页系统提取数据，保存到Excel",
    "file_organize": "文件整理 - 按规则批量整理、分类、重命名文件",
    "batch_rename": "批量重命名 - 按规则批量重命名文件",
    "excel_report": "Excel报表 - 从数据生成Excel报表",
    "web_monitor": "网页监控 - 监控网页变化并通知",
    "unknown": "未知任务 - 无法识别的任务",
    "general_chat": "普通对话 - 非任务的闲聊",
}


def build_system_prompt(schema: dict, tools: dict, context: dict) -> str:
    """
    构建中文 system prompt。

    参数
    ----
    schema : dict
        TASK_OUTPUT_SCHEMA，定义 LLM 输出 JSON 结构（保留以备未来使用）。
    tools : dict
        可用工具描述字典，key 为任务类型，value 为中文描述。
    context : dict
        上下文信息，可包含:
        - chrome_connected: bool  用户 Chrome 是否已连接
        - shortcut_match: str | None  快捷方式匹配到的任务
        - recent_tasks: list[dict] | None  最近任务列表
    """
    lines: list[str] = []

    # ---- 角色 ----
    lines.append("你是Windows桌面AI办公助手，帮助用户通过自然语言完成办公自动化。")

    # ---- 职责 ----
    lines.append(
        "你的职责是理解用户意图，将自然语言转换为结构化的任务参数JSON。"
        "你不需要执行任务，只需要输出参数——代码会负责执行。"
    )

    # ---- 输出规则 ----
    lines.append(
        "【输出规则】优先判断用户意图："
    )
    lines.append(
        "• 如果用户想执行办公任务（填表单、处理Excel、整理文件等），必须输出JSON。"
    )
    lines.append(
        "• 如果是普通对话、问候、闲聊，直接自然回复即可，不需要JSON。"
    )
    lines.append(
        "输出JSON时，整个回复就是一段合法的JSON，不要有额外文字。"
    )

    # ---- 可用任务类型 ----
    tool_items = list(tools.items())
    if tool_items:
        lines.append("【可用任务类型】")
        for task_type, desc in tool_items:
            lines.append(f"  - {task_type}: {desc}")
    else:
        # fallback to built-in
        lines.append("【可用任务类型】")
        for task_type, desc in TOOL_DESCRIPTIONS.items():
            lines.append(f"  - {task_type}: {desc}")

    # ---- Mode 选择规则 ----
    chrome_connected = context.get("chrome_connected", False)
    lines.append(
        "【Mode选择规则】"
        f"当前用户Chrome{'已连接' if chrome_connected else '未连接'}。"
        "如果任务需要浏览器操作：用户Chrome已连接时使用mode='B'（可操作用户当前页面），"
        "未连接时使用mode='A'（启动独立浏览器）。"
        "非浏览器任务mode留空字符串。"
    )

    # ---- 安全规则 ----
    lines.append("【安全规则】不要询问用户密码，密码由浏览器自动管理。")

    # ---- JSON Schema（精简） ----
    lines.append("【输出格式】严格按照下面的JSON结构输出：")
    lines.append(
        '{"task_type":"...","system_name":"...","mode":"...",'
        '"params":{"data_source":"...","target_file":"...",'
        '"field_mapping":{...},"organize_rule":"...","url_hint":"...",'
        '"additional_notes":"..."},'
        '"confidence":0.9,"needs_clarification":false,"clarification_question":""}'
    )
    lines.append(
        "params 中仅填充有意义的字段，其余使用空字符串或空对象。"
    )
    lines.append(
        "confidence 取值 0-1，反映你对任务分类的把握程度。"
        "当 confidence < 0.5 或模糊不清时，设置 needs_clarification=true 并给出追问。"
    )

    # ---- 记忆上下文注入 ----
    shortcut = context.get("shortcut_match")
    recent = context.get("recent_tasks")
    if shortcut or recent:
        lines.append("【历史参考】以下是用户的过往信息，可作为判断依据：")
        if shortcut:
            lines.append(
                f"  匹配到快捷指令: 系统={shortcut.get('system_name','')}, "
                f"任务={shortcut.get('task_type','')}"
            )
        if recent:
            lines.append("  最近任务:")
            for i, t in enumerate(recent[:5], 1):
                lines.append(f"    {i}. {t.get('task_summary','')}: {t.get('user_input','')}")

    prompt = "\n".join(lines)

    # 控制总长度：按字符估算（中文字符约占 1 token），目标 < 1200 字
    if len(prompt) > 1400:
        # 裁剪：优先保留角色+规则+Schema，压缩工具描述
        prompt = prompt[:1400]

    return prompt


def build_user_prompt(user_input: str, context: dict, user_chrome_connected: bool = False) -> str:
    """
    构建用户输入 prompt。

    参数
    ----
    user_input : str
        用户原始输入文本。
    context : dict
        上下文信息，可包含:
        - recent_tasks: list[dict] | None  最近任务列表
    user_chrome_connected : bool
        用户 Chrome 是否已连接。
    """
    parts: list[str] = []

    recent = context.get("recent_tasks")
    if recent:
        parts.append("（以下是用户最近的任务记录，帮助你理解上下文：")
        for t in recent[:3]:
            parts.append(
                f"  - {t.get('task_summary', '?')}: {t.get('user_input', '')}"
            )
        parts.append("）")

    parts.append(f"用户输入：{user_input}")
    if user_chrome_connected:
        parts.append("（提示：用户Chrome浏览器已连接，可直接操作当前标签页）")

    return "\n".join(parts)

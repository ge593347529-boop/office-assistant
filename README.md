# AI 办公助手 (Office Assistant)

一个 Windows 桌面端 AI 办公助手，通过自然语言对话驱动，自动完成浏览器业务系统操作和本地 WPS 文档处理。运行于内网环境，接入本地部署的大模型。

## 设计理念

> **AI 只当翻译官（调一次），代码干苦力活（确定性执行）。**

- 用户自然语言输入 → AI 推理一次 → 结构化参数 → 确定性脚本执行
- 不做 Agent Loop，不做多步推理
- 安全性优先：密码永不进入 App，委托 Chrome 密码管理器

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 桌面框架 | **PySide6** | 与 Python COM/WPS 自动化生态无缝集成，单进程零跨语言开销 |
| 浏览器自动化 | **Playwright (Python)** | Mode A(独立Chrome) + Mode B(连接用户Chrome) 统一 API |
| 本地 LLM | **Ollama + 千问模型** | OpenAI 兼容 API，`openai` Python SDK 直接调用 |
| WPS 操作 | **pywin32 + COM + openpyxl + python-docx + xlwings** | Windows 桌面自动化事实标准 |
| 数据存储 | **SQLite（本地）** | 记忆系统、系统配置、session 持久化，完全离线 |
| 打包分发 | **PyInstaller** | 单一 exe，内网免安装分发 |

## 架构概览

```
┌──────────────────────────────────────────┐
│            Chat UI (PySide6)              │
│   自然语言输入 + 确认卡片 + 结果展示      │
└──────────────┬───────────────────────────┘
               │
┌──────────────▼───────────────────────────┐
│          App Core                          │
│                                            │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ 对话管理  │  │ 记忆系统  │  │ 配置管理 │ │
│  │(历史+上下文)│  │(SQLite)  │  │(settings)│ │
│  └──────────┘  └──────────┘  └─────────┘ │
│                                            │
│  ┌──────────────────────────────────────┐ │
│  │         AI 推理 (仅一次)              │ │
│  │  输入: 用户原话 + 记忆上下文          │ │
│  │  输出: {task_type, mode, params}     │ │
│  └──────────┬───────────────────────────┘ │
│             │                              │
│  ┌──────────▼───────────────────────────┐ │
│  │       执行引擎 (确定性脚本)            │ │
│  └──────────┬───────────────────────────┘ │
└──────────────┼───────────────────────────┘
               │
┌──────────────▼───────────────────────────┐
│           工具层                           │
│  ┌────────┐ ┌────────┐ ┌────────────────┐│
│  │Browser │ │  WPS   │ │  File System   ││
│  │自动登录 │ │Excel读写│ │  文件整理/搜索  ││
│  │填表/取数│ │Word处理 │ │  批量操作       ││
│  └────────┘ └────────┘ └────────────────┘│
└──────────────────────────────────────────┘
```

## 项目目录结构

```
ai办公助手/
├── main.py                    # 应用入口
├── requirements.txt           # Python 依赖
├── app/
│   ├── ui/
│   │   ├── main_window.py     # 主窗口
│   │   ├── chat_panel.py      # 聊天面板
│   │   ├── confirm_card.py    # 确认卡片组件
│   │   └── system_tray.py     # 系统托盘
│   ├── core/
│   │   ├── conversation.py    # 对话管理（上下文+历史）
│   │   ├── inference.py       # AI 推理（单次调用 LLM）
│   │   ├── executor.py        # 执行引擎（路由到工具脚本）
│   │   └── memory.py          # 本地记忆系统（SQLite CRUD）
│   ├── tools/
│   │   ├── browser.py         # Playwright 封装 (Mode A/B + 自动登录)
│   │   ├── excel.py           # Excel 读写
│   │   ├── word.py            # Word 处理
│   │   ├── filesystem.py      # 文件系统操作
│   │   └── system.py          # 系统工具（剪贴板等）
│   └── config/
│       ├── settings.py        # 配置管理
│       └── prompt_templates.py# LLM Prompt 模板
├── data/
│   ├── sessions/              # 各系统 storage_state 持久化
│   └── chrome_profile/        # Chrome persistent profile
└── resources/
    └── styles/
```

## 核心功能

### 1. 浏览器操作 (Mode A + B)
- **Mode A**：独立启动 Chrome（Playwright Launch），持久化 profile，自动保存密码和 session
- **Mode B**：连接用户已打开的 Chrome（CDP），直接操作当前页面，复用已有登录态
- 自动判断：用户 Chrome 已连接 ? Mode B : Mode A
- 自动登录：session 有效直接进，过期自动填密码登录，验证码/异常截图问用户

### 2. 文档处理
- Excel 读写、数据提取、报表生成（openpyxl + xlwings）
- Word 文档读写替换（python-docx）
- 文件批量重命名、整理分类

### 3. 记忆系统
- **三层记忆**：系统 session → 行为模式 → 对话上下文
- 使用越频繁，输入越简短（"报销" 两个字即可触发完整流程）
- 全部存储在本地 SQLite，完全离线

### 4. 确认卡片
- 执行前一次性展示所有参数
- 支持修改、确认、取消
- 执行中支持暂停/继续/停止

## 交互流程

```
用户说一句话
  → AI 推理（仅一次）→ 生成结构化参数
  → 展示确认卡片
  → 用户确认 → 确定性脚本执行
  → 结果反馈
```

## 安全设计

- **密码永不进入 App**：委托 Chrome 密码管理器
- **Session 持久化**：cookies + localStorage，不存密码
- **本地存储**：所有数据在用户电脑上
- **操作确认**：不可逆操作（提交/删除）默认让用户手动确认

## 实施阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 项目骨架 + 基础 UI | 🔲 待开始 |
| Phase 2 | AI 推理接入 (Ollama) | 🔲 待开始 |
| Phase 3 | 浏览器自动化 (Playwright) | 🔲 待开始 |
| Phase 4 | WPS/文档工具 | 🔲 待开始 |
| Phase 5 | 执行引擎 + 确认卡片 | 🔲 待开始 |
| Phase 6 | 记忆系统完善 | 🔲 待开始 |
| Phase 7 | 打包 + 分发 | 🔲 待开始 |

## 开发环境

- Windows 11
- Python 3.10+
- Ollama (本地)
- Chrome / Edge 浏览器

## License

MIT

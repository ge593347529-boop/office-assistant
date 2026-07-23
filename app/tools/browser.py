"""Playwright 浏览器封装层。

支持两种模式:
- Mode A: 独立启动 Chrome（persistent context，密码管理器生效）
- Mode B: 通过 CDP 连接用户已打开的 Chrome

自动登录 + Session 持久化。密码委托 Chrome 密码管理器，不进入代码。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)

from app.config.settings import AppConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LoginResult:
    """登录流程结果。"""

    success: bool
    needs_manual: bool  # True = 验证码 / 二次验证 / 密码错误，需要用户介入
    message: str
    screenshot: bytes | None  # 截图供用户查看


# ---------------------------------------------------------------------------
# BrowserTool
# ---------------------------------------------------------------------------


class BrowserTool:
    """Playwright 浏览器封装。

    通过 persistent context 保持 Chrome 用户数据目录，使密码管理器可用。
    支持独立启动（launch）和远程连接（connect）两种模式。
    """

    # 常见登录页面 URL 关键词
    _LOGIN_URL_PATTERNS = frozenset(
        {
            "login",
            "signin",
            "sign_in",
            "sign-in",
            "auth",
            "oauth",
            "sso",
            "authenticate",
        }
    )

    # 常见"已登录"指示器选择器（任一命中即认为已登录）
    _LOGGED_IN_SELECTORS = (
        '[aria-label*="退出"]',
        '[aria-label*="登出"]',
        '[aria-label*="logout"]',
        '[aria-label*="sign out"]',
        "a[href*='logout']",
        "a[href*='signout']",
        "a[href*='sign-out']",
        "[data-testid*='logout']",
        "[data-testid*='signout']",
        ".user-avatar",
        ".avatar",
        ".user-name",
        ".username",
        "[class*='user-menu']",
        "[class*='userMenu']",
        "#user-menu",
        "#userMenu",
    )

    # 常见登录按钮选择器
    _LOGIN_BUTTON_SELECTORS = (
        "button[type='submit']",
        "input[type='submit']",
        "button[class*='login']",
        "button[class*='Login']",
        "button[class*='signin']",
        "button[class*='sign-in']",
        "button[class*='submit']",
        "[data-testid*='login']",
        "[data-testid*='submit']",
        "#login",
        "#submit",
        "#signin",
    )

    def __init__(self, config: AppConfig) -> None:
        """初始化 Playwright。

        Args:
            config: 应用配置，包含 chrome_profile_dir、sessions_dir 等。
        """
        self._config = config
        self._playwright = sync_playwright().start()
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._mode: str | None = None  # "launch" or "connect"
        logger.info("BrowserTool 初始化完成")

    # ------------------------------------------------------------------
    # Mode A: 独立浏览器
    # ------------------------------------------------------------------

    def launch(self, headless: bool = False) -> Page:
        """启动独立 Chrome（persistent context）。

        使用 persistent context 确保 Chrome 用户数据目录被复用，
        密码管理器才能正常读取已保存的凭据。

        Args:
            headless: 是否无头模式。默认 False（用户可见）。

        Returns:
            新创建的 Page 对象。
        """
        if self._context is not None:
            logger.warning("已有活跃的浏览器上下文，先关闭再重新启动")
            self.close()

        user_data_dir = self._config.chrome_profile_dir
        user_data_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "启动独立 Chrome（headless=%s, user_data_dir=%s）",
            headless,
            user_data_dir,
        )

        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                # 不设置 viewport 以使用窗口默认大小
                no_viewport=True,
            )
            # persistent context 只创建一个 page
            if self._context.pages:
                self._page = self._context.pages[0]
            else:
                self._page = self._context.new_page()

            self._browser = None  # persistent context 不暴露 browser 对象
            self._mode = "launch"
            logger.info("独立 Chrome 启动成功")
            return self._page

        except PlaywrightError as exc:
            logger.error("启动独立 Chrome 失败: %s", exc)
            raise RuntimeError(f"无法启动 Chrome: {exc}") from exc

    # ------------------------------------------------------------------
    # Mode B: 连接用户 Chrome
    # ------------------------------------------------------------------

    def connect(self, port: int | None = None) -> Page:
        """通过 CDP 连接用户已打开的 Chrome。

        用户需要先以调试模式启动 Chrome：
            chrome.exe --remote-debugging-port=9222

        Args:
            port: CDP 调试端口。默认使用 config.chrome_debug_port。

        Returns:
            连接到的 Page 对象（或新建的 Page）。
        """
        if port is None:
            port = self._config.chrome_debug_port

        if self._browser is not None or self._context is not None:
            logger.warning("已有活跃连接，先关闭再重新连接")
            self.close()

        cdp_url = f"http://localhost:{port}"
        logger.info("通过 CDP 连接 Chrome: %s", cdp_url)

        try:
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            self._mode = "connect"

            # 尝试获取已有 context 和 page
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = self._context.new_page()
            else:
                self._context = self._browser.new_context()
                self._page = self._context.new_page()

            logger.info("CDP 连接成功，当前页面: %s", self._page.url if self._page else "无")
            return self._page

        except PlaywrightError as exc:
            logger.error("CDP 连接失败 (port=%d): %s", port, exc)
            raise ConnectionError(
                f"无法连接到 Chrome (端口 {port})。请确保 Chrome 已以调试模式启动:\n"
                f"    chrome.exe --remote-debugging-port={port}"
            ) from exc

    # ------------------------------------------------------------------
    # 页面操作
    # ------------------------------------------------------------------

    def _ensure_page(self) -> Page:
        """确保有可用的 Page 对象。"""
        if self._page is None or self._page.is_closed():
            raise RuntimeError("没有可用的页面。请先调用 launch() 或 connect()。")
        return self._page

    def navigate(self, url: str) -> None:
        """导航到指定 URL。

        Args:
            url: 目标 URL。
        """
        page = self._ensure_page()
        try:
            logger.info("导航到: %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError as exc:
            logger.warning("导航超时: %s", url)
            raise TimeoutError(f"导航到 {url} 超时") from exc
        except PlaywrightError as exc:
            logger.error("导航失败: %s - %s", url, exc)
            raise RuntimeError(f"导航到 {url} 失败: {exc}") from exc

    def click(self, selector: str) -> None:
        """点击匹配选择器的元素。

        Args:
            selector: CSS 选择器。
        """
        page = self._ensure_page()
        try:
            logger.debug("点击元素: %s", selector)
            page.click(selector, timeout=10_000)
        except PlaywrightTimeoutError as exc:
            raise TimeoutError(f"等待元素超时: {selector}") from exc
        except PlaywrightError as exc:
            raise RuntimeError(f"点击元素失败 ({selector}): {exc}") from exc

    def fill(self, selector: str, value: str) -> None:
        """填充输入框。

        注意：不会读取密码字段的值。如果 selector 指向 password 类型输入框，
        仅执行填充操作，不记录 value 内容。

        Args:
            selector: CSS 选择器。
            value: 要填入的文本。
        """
        page = self._ensure_page()
        is_password = False
        try:
            # 判断是否为密码字段
            element = page.locator(selector).first
            input_type = element.get_attribute("type")
            is_password = input_type == "password" if input_type else False

            if is_password:
                logger.debug("填充密码字段: %s (值不记录)", selector)
            else:
                logger.debug("填充字段: %s", selector)

            element.fill(value, timeout=10_000)
        except PlaywrightTimeoutError as exc:
            raise TimeoutError(f"等待输入框超时: {selector}") from exc
        except PlaywrightError as exc:
            raise RuntimeError(f"填充输入框失败 ({selector}): {exc}") from exc

    def select_option(self, selector: str, value: str) -> None:
        """在下拉框中选中指定选项。

        Args:
            selector: CSS 选择器（指向 select 元素）。
            value: 选项的 value 属性值。
        """
        page = self._ensure_page()
        try:
            logger.debug("选择选项: %s -> %s", selector, value)
            page.select_option(selector, value, timeout=10_000)
        except PlaywrightTimeoutError as exc:
            raise TimeoutError(f"等待下拉框超时: {selector}") from exc
        except PlaywrightError as exc:
            raise RuntimeError(f"选择选项失败 ({selector}): {exc}") from exc

    def get_text(self, selector: str = "body") -> str:
        """获取元素文本内容。

        Args:
            selector: CSS 选择器。默认 "body" 获取整个页面文本。

        Returns:
            元素的 inner_text。
        """
        page = self._ensure_page()
        try:
            return page.inner_text(selector, timeout=5_000)
        except PlaywrightTimeoutError:
            logger.warning("获取文本超时: %s", selector)
            return ""
        except PlaywrightError as exc:
            logger.error("获取文本失败 (%s): %s", selector, exc)
            return ""

    def get_table_data(self, selector: str = "table") -> list[dict[str, str]]:
        """提取 HTML 表格数据。

        Args:
            selector: 指向 table 元素的 CSS 选择器。

        Returns:
            列表，每行为一个 dict，key 为表头文本，value 为单元格文本。
        """
        page = self._ensure_page()
        try:
            table = page.locator(selector).first
            if not table.count():
                logger.warning("未找到表格: %s", selector)
                return []

            # 提取表头
            headers: list[str] = []
            header_cells = table.locator("thead th, thead td")
            if header_cells.count() == 0:
                header_cells = table.locator("tr:first-child th, tr:first-child td")
            for i in range(header_cells.count()):
                headers.append(header_cells.nth(i).inner_text().strip())

            if not headers:
                logger.warning("表格无表头: %s", selector)
                return []

            # 提取数据行
            rows: list[dict[str, str]] = []
            data_rows = table.locator("tbody tr")
            if data_rows.count() == 0:
                data_rows = table.locator("tr:not(:first-child)")

            for i in range(data_rows.count()):
                cells = data_rows.nth(i).locator("td, th")
                if cells.count() != len(headers):
                    continue  # 跳过头尾不一致的行
                row_data: dict[str, str] = {}
                for j in range(cells.count()):
                    row_data[headers[j]] = cells.nth(j).inner_text().strip()
                rows.append(row_data)

            logger.debug("提取表格 '%s': %d 行", selector, len(rows))
            return rows

        except PlaywrightError as exc:
            logger.error("提取表格失败 (%s): %s", selector, exc)
            return []

    def screenshot(self) -> bytes:
        """截取当前页面截图。

        Returns:
            PNG 格式截图字节数据。
        """
        page = self._ensure_page()
        try:
            logger.debug("截取页面截图: %s", page.url)
            return page.screenshot(type="png", full_page=False)
        except PlaywrightError as exc:
            logger.error("截图失败: %s", exc)
            raise RuntimeError(f"截图失败: {exc}") from exc

    def wait_for_selector(self, selector: str, timeout: int = 10) -> bool:
        """等待元素出现。

        Args:
            selector: CSS 选择器。
            timeout: 超时秒数。默认 10 秒。

        Returns:
            True 如果元素在超时前出现，否则 False。
        """
        page = self._ensure_page()
        try:
            page.wait_for_selector(selector, timeout=timeout * 1000)
            return True
        except PlaywrightTimeoutError:
            logger.debug("等待元素超时 (%ds): %s", timeout, selector)
            return False
        except PlaywrightError as exc:
            logger.error("等待元素出错 (%s): %s", selector, exc)
            return False

    # ------------------------------------------------------------------
    # 登录检测 & Session 管理
    # ------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        """检测当前页面是否已登录。

        综合策略：
        1. URL 不包含登录相关关键词
        2. 页面存在退出/登出按钮
        3. 页面存在用户头像/用户名元素

        策略 2 和 3 中任一命中即认为已登录（同时 URL 不含 login 关键词）。
        如果找不到任何指示器，检查 URL 不含 login 关键词也算弱证据。

        Returns:
            True 表示已登录。
        """
        page = self._ensure_page()
        try:
            current_url = page.url.lower()
        except PlaywrightError:
            return False

        # 检查 URL 是否明显在登录页面
        url_has_login = any(
            keyword in current_url for keyword in self._LOGIN_URL_PATTERNS
        )

        # 检查页面元素
        for selector in self._LOGGED_IN_SELECTORS:
            try:
                if page.locator(selector).count() > 0:
                    logger.debug("is_logged_in: 命中已登录选择器 '%s'", selector)
                    return True
            except PlaywrightError:
                continue

        # 如果 URL 不含登录关键词，且页面内容不像是登录页（没有 password 字段）
        if not url_has_login:
            try:
                password_fields = page.locator("input[type='password']").count()
                username_fields = page.locator("input[type='text'], input[type='email']").count()
                if password_fields == 0 and username_fields == 0:
                    logger.debug("is_logged_in: URL 不含 login 且页面无登录表单")
                    return True
            except PlaywrightError:
                pass

            # 弱证据：URL 不含 login 即认为已登录（可能误判，但避免重复登录）
            logger.debug("is_logged_in: URL 不含 login 关键词（弱证据）")
            return True

        logger.debug("is_logged_in: 当前页面疑似为登录页面")
        return False

    def handle_login(self, system_name: str) -> LoginResult:
        """自动登录流程。

        流程：
        1. 检查是否已在登录页
        2. 尝试恢复 session
        3. 检查表单是否已自动填充（Chrome 密码管理器）
        4. 已填充 → 自动点击登录按钮
        5. 未填充 → 返回 needs_manual=True + 截图
        6. 登录成功 → save_session → 返回 success=True
        7. 遇到验证码/异常 → 返回 needs_manual=True + 截图

        Args:
            system_name: 系统名称，用于 session 文件命名。

        Returns:
            LoginResult 描述登录结果。
        """
        page = self._ensure_page()

        # Step 0: 如果已经登录，直接返回
        if self.is_logged_in():
            logger.info("handle_login: 已处于登录状态")
            self.save_session(system_name)
            return LoginResult(
                success=True,
                needs_manual=False,
                message="已处于登录状态",
                screenshot=None,
            )

        # Step 1: 尝试恢复 session
        logger.info("handle_login: 尝试恢复 session '%s'", system_name)
        if self.restore_session(system_name):
            # 恢复后重新加载当前页面
            try:
                page.reload(wait_until="domcontentloaded", timeout=15_000)
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightError:
                pass

            if self.is_logged_in():
                logger.info("handle_login: session 恢复成功，已登录")
                self.save_session(system_name)
                return LoginResult(
                    success=True,
                    needs_manual=False,
                    message="通过 session 恢复登录成功",
                    screenshot=None,
                )
            logger.info("handle_login: session 恢复后仍未登录，继续自动填充流程")

        # Step 2: 检查 Chrome 密码管理器是否已填充表单
        screenshot_bytes: bytes | None = None
        try:
            screenshot_bytes = self.screenshot()
        except RuntimeError:
            pass

        # 查找密码输入框和用户名输入框
        try:
            password_inputs = page.locator("input[type='password']")
            username_inputs = page.locator("input[type='text'], input[type='email']")

            has_password_field = password_inputs.count() > 0
            has_username_field = username_inputs.count() > 0

            if not has_password_field:
                logger.info("handle_login: 页面无密码输入框，可能不是登录页")
                # 试着找登录按钮直接点击
                if self._try_click_login_button(page):
                    if self.is_logged_in():
                        self.save_session(system_name)
                        return LoginResult(
                            success=True,
                            needs_manual=False,
                            message="自动登录成功（无表单直接点击登录按钮）",
                            screenshot=None,
                        )
                return LoginResult(
                    success=False,
                    needs_manual=True,
                    message="未检测到登录表单，需要手动操作",
                    screenshot=screenshot_bytes,
                )

            # Step 3: 检查是否已自动填充（不能读取密码值，只检查 value 属性是否非空）
            # 注意：对于密码字段，仅通过 get_attribute("value") 查询长度，
            # 不将值记录到日志或变量中
            username_filled = False
            password_filled = False

            if has_username_field:
                first_username = username_inputs.first
                try:
                    username_val = first_username.input_value()
                    if username_val and len(username_val.strip()) > 0:
                        username_filled = True
                        logger.debug("handle_login: 用户名已自动填充")
                except PlaywrightError:
                    pass

            if has_password_field:
                first_password = password_inputs.first
                try:
                    # 仅判断是否非空，不记录密码内容
                    pwd_val = first_password.input_value()
                    if pwd_val and len(pwd_val) > 0:
                        password_filled = True
                        logger.debug("handle_login: 密码已自动填充（内容不记录）")
                except PlaywrightError:
                    pass

            if username_filled and password_filled:
                logger.info("handle_login: 表单已自动填充，点击登录按钮")
                if self._try_click_login_button(page):
                    # 等待页面跳转
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PlaywrightError:
                        pass

                    # 检查是否登录成功
                    if self.is_logged_in():
                        self.save_session(system_name)
                        return LoginResult(
                            success=True,
                            needs_manual=False,
                            message="自动登录成功（Chrome 密码管理器自动填充）",
                            screenshot=None,
                        )

                    # 登录失败：检查是否遇到验证码
                    return self._check_for_captcha_or_error(page, system_name)

                # 没找到登录按钮
                return LoginResult(
                    success=False,
                    needs_manual=True,
                    message="表单已填充但未找到登录按钮，需要手动操作",
                    screenshot=screenshot_bytes,
                )

            # 未自动填充
            logger.info("handle_login: 表单未自动填充，需要用户手动输入凭据")
            return LoginResult(
                success=False,
                needs_manual=True,
                message="登录表单未自动填充，请手动输入用户名和密码",
                screenshot=screenshot_bytes,
            )

        except PlaywrightError as exc:
            logger.error("handle_login: 执行过程出错: %s", exc)
            return LoginResult(
                success=False,
                needs_manual=True,
                message=f"自动登录过程出错: {exc}",
                screenshot=screenshot_bytes,
            )

    def _try_click_login_button(self, page: Page) -> bool:
        """尝试点击登录按钮。

        Args:
            page: 当前页面。

        Returns:
            True 如果成功找到并点击了登录按钮。
        """
        for selector in self._LOGIN_BUTTON_SELECTORS:
            try:
                button = page.locator(selector).first
                if button.count() > 0 and button.is_visible():
                    logger.info("_try_click_login_button: 找到并点击登录按钮 '%s'", selector)
                    button.click(timeout=5_000)
                    return True
            except PlaywrightError:
                continue

        # 最后尝试通过文本查找
        text_selectors = (
            "button:has-text('登录')",
            "button:has-text('Login')",
            "button:has-text('Sign in')",
            "button:has-text('登 录')",
            "input[type='submit'][value*='登录']",
            "input[type='submit'][value*='Login']",
            "input[type='submit'][value*='Sign']",
        )
        for selector in text_selectors:
            try:
                element = page.locator(selector).first
                if element.count() > 0 and element.is_visible():
                    logger.info("_try_click_login_button: 通过文本找到登录按钮 '%s'", selector)
                    element.click(timeout=5_000)
                    return True
            except PlaywrightError:
                continue

        logger.warning("_try_click_login_button: 未找到登录按钮")
        return False

    def _check_for_captcha_or_error(self, page: Page, system_name: str) -> LoginResult:
        """检查登录后是否遇到验证码或错误消息。

        Args:
            page: 当前页面。
            system_name: 系统名称。

        Returns:
            LoginResult。
        """
        screenshot_bytes: bytes | None = None
        try:
            screenshot_bytes = page.screenshot(type="png", full_page=False)
        except PlaywrightError:
            pass

        # 检测验证码常见关键词
        captcha_selectors = (
            "img[src*='captcha']",
            "img[src*='Captcha']",
            "img[src*='verify']",
            "input[name*='captcha']",
            "input[name*='Captcha']",
            "input[id*='captcha']",
            "input[id*='Captcha']",
            "[class*='captcha']",
            "[class*='Captcha']",
            "[class*='recaptcha']",
            ".g-recaptcha",
            "iframe[src*='recaptcha']",
            "iframe[src*='captcha']",
        )

        for selector in captcha_selectors:
            try:
                if page.locator(selector).count() > 0:
                    logger.info("_check_for_captcha_or_error: 检测到验证码 '%s'", selector)
                    return LoginResult(
                        success=False,
                        needs_manual=True,
                        message="需要输入验证码",
                        screenshot=screenshot_bytes,
                    )
            except PlaywrightError:
                continue

        # 检测错误消息
        error_selectors = (
            "[class*='error']",
            "[class*='danger']",
            "[class*='alert']",
            "[role='alert']",
            ".message-error",
            ".login-error",
        )

        found_errors: list[str] = []
        for selector in error_selectors:
            try:
                elements = page.locator(selector)
                for i in range(min(elements.count(), 3)):
                    text = elements.nth(i).inner_text().strip()
                    if text and len(text) < 200:
                        found_errors.append(text)
            except PlaywrightError:
                continue

        if found_errors:
            error_msg = "; ".join(found_errors)
            logger.info("_check_for_captcha_or_error: 检测到错误消息: %s", error_msg)
            return LoginResult(
                success=False,
                needs_manual=True,
                message=f"登录失败: {error_msg}",
                screenshot=screenshot_bytes,
            )

        # 没有明确的验证码或错误，但也没登录成功
        logger.info("_check_for_captcha_or_error: 登录结果不明，需要用户确认")
        return LoginResult(
            success=False,
            needs_manual=True,
            message="登录结果不确定，请手动确认",
            screenshot=screenshot_bytes,
        )

    def save_session(self, name: str) -> None:
        """保存 storage_state 到文件。

        保存的内容是 cookies + localStorage，不包含密码。

        Args:
            name: session 名称，决定文件名。
        """
        if self._context is None:
            logger.warning("save_session: 没有活跃的浏览器上下文")
            return

        sessions_dir = self._config.sessions_dir
        sessions_dir.mkdir(parents=True, exist_ok=True)

        session_path = sessions_dir / f"{name}.json"
        try:
            storage_state = self._context.storage_state()
            with open(session_path, "w", encoding="utf-8") as f:
                json.dump(storage_state, f, ensure_ascii=False, indent=2)
            logger.info("Session 已保存: %s", session_path)
        except (OSError, PlaywrightError) as exc:
            logger.error("保存 session 失败 (%s): %s", session_path, exc)

    def restore_session(self, name: str) -> bool:
        """从文件恢复 storage_state。

        Args:
            name: session 名称。

        Returns:
            True 表示成功恢复，False 表示文件不存在或恢复失败。
        """
        session_path = self._config.sessions_dir / f"{name}.json"

        if not session_path.exists():
            logger.info("restore_session: session 文件不存在: %s", session_path)
            return False

        try:
            with open(session_path, "r", encoding="utf-8") as f:
                storage_state = json.load(f)

            # 如果有现有 context 且有 cookies，则追加 cookies
            if self._context is not None:
                if "cookies" in storage_state:
                    self._context.add_cookies(storage_state["cookies"])
                    logger.info(
                        "Session 已恢复（cookies %d 条）: %s",
                        len(storage_state["cookies"]),
                        session_path,
                    )
                # 注意：localStorage 无法通过 Playwright API 直接批量注入，
                # 通常由网站通过 cookies 中的 session token 自动重建
                return True
            else:
                logger.warning("restore_session: 无活跃上下文，跳过恢复")
                return False

        except (json.JSONDecodeError, OSError, PlaywrightError) as exc:
            logger.error("恢复 session 失败 (%s): %s", session_path, exc)
            return False

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def page(self) -> Page | None:
        """当前活跃的 Page 对象。"""
        return self._page

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭浏览器，清理资源。"""
        logger.info("关闭 BrowserTool")

        try:
            if self._context is not None:
                self._context.close()
                self._context = None
        except PlaywrightError as exc:
            logger.warning("关闭 context 时出错: %s", exc)

        try:
            if self._browser is not None:
                self._browser.close()
                self._browser = None
        except PlaywrightError as exc:
            logger.warning("关闭 browser 时出错: %s", exc)

        self._page = None
        self._mode = None

    def __del__(self) -> None:
        """析构时自动清理。"""
        try:
            self.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass  # 析构函数中静默所有异常

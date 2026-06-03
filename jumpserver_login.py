# -*- coding: utf-8 -*-

from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import jumpserver_config as config


class JumpServerSession:
    """
    JumpServer 登录会话。

    账号密码由界面传入，不写死在代码或配置文件里。
    """

    def __init__(
        self,
        username: str,
        password: str,
        user_data_dir: str = "jumpserver_browser_profile",
        headless: bool | None = None,
        slow_mo: int | None = None,
    ):
        self.username = username
        self.password = password
        self.user_data_dir = user_data_dir
        self.headless = config.BROWSER_HEADLESS if headless is None else headless
        self.slow_mo = config.BROWSER_SLOW_MO if slow_mo is None else slow_mo

        self.playwright = None
        self.context = None
        self.page = None

    def start(self):
        """
        启动浏览器，登录 JumpServer，返回 page。
        """

        if not self.username:
            raise ValueError("JumpServer 账号不能为空。")

        if not self.password:
            raise ValueError("JumpServer 密码不能为空。")

        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()

        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            slow_mo=self.slow_mo,
            accept_downloads=True,
        )

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)

        self.login()

        return self.page

    def login(self):
        """
        登录 JumpServer。

        重点：
        不能用 `"luna" in page.url` 判断是否已登录，
        因为登录页本身也有 next=/luna/。
        """

        page = self.page

        page.goto(config.JUMP_LOGIN_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        # 如果当前页面已经是真正的 Luna 页面，则认为已登录
        if self.is_luna_page(page):
            page.goto(config.JUMP_LUNA_URL)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
            return

        # 如果页面上存在登录按钮或密码框，则执行登录
        if self.has_login_form(page):
            self._fill_username(page)
            self._fill_password(page)

            page.get_by_role("button", name="登录").click()
            page.wait_for_timeout(3000)

        # 登录后进入 Luna
        page.goto(config.JUMP_LUNA_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)

        # 再检查一次，如果还在登录页，说明登录失败或需要验证码/二次验证
        if self.is_login_page(page):
            raise RuntimeError(
                "JumpServer 登录后仍停留在登录页。"
                "请检查账号密码是否正确，或者页面是否需要验证码/二次验证。"
            )

    def is_login_page(self, page) -> bool:
        """
        判断当前是否是登录页。
        """

        url = page.url.lower()

        if "/core/auth/login" in url:
            return True

        try:
            password_input = page.locator("input[type='password']").first
            if password_input.count() > 0:
                return True
        except Exception:
            pass

        return False

    def is_luna_page(self, page) -> bool:
        """
        判断当前是否已经进入 Luna 页面。
        """

        url = page.url.lower()

        # 真正的 luna 页面一般是 /luna/，不能只判断包含 luna
        if "/luna/" in url and "/core/auth/login" not in url:
            return True

        try:
            # 登录后的页面一般能看到资产/文件管理/连接等内容
            if page.get_by_text("文件管理").count() > 0:
                return True
        except Exception:
            pass

        return False

    def has_login_form(self, page) -> bool:
        """
        判断页面是否存在登录表单。
        """

        try:
            password_input = page.locator("input[type='password']").first
            if password_input.count() > 0:
                return True
        except Exception:
            pass

        try:
            login_button = page.get_by_role("button", name="登录")
            if login_button.count() > 0:
                return True
        except Exception:
            pass

        return False

    def _fill_username(self, page):
        selectors = [
            "input[name='username']",
            "input[name='login']",
            "input[placeholder*='用户名']",
            "input[placeholder*='账号']",
            "input[placeholder*='邮箱']",
            "input[type='text']",
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=3000)
                locator.fill(self.username)
                return
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        raise RuntimeError("没有找到 JumpServer 账号输入框。")

    def _fill_password(self, page):
        selectors = [
            "input[name='password']",
            "input[placeholder*='密码']",
            "input[type='password']",
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=3000)
                locator.fill(self.password)
                return
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

        raise RuntimeError("没有找到 JumpServer 密码输入框。")

    def close(self):
        """
        关闭浏览器和 Playwright。
        """

        try:
            if self.context:
                self.context.close()
        finally:
            if self.playwright:
                self.playwright.stop()
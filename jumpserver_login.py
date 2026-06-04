# -*- coding: utf-8 -*-

from pathlib import Path
from urllib.parse import urljoin
import re

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import jumpserver_config as config


class JumpServerSession:
    """
    JumpServer 登录会话。

    账号密码由界面传入，不写死在代码或配置文件里。

    打包 exe 后的浏览器策略：
    1. 优先使用电脑本机 Microsoft Edge
    2. 如果 Edge 不可用，再尝试 Google Chrome
    3. 不再依赖 Playwright 自带 Chromium，避免 exe 运行时报：
       Executable doesn't exist ... .local-browsers/chromium-xxxx/chrome.exe

    验证码策略：
    1. 如果登录页存在验证码图片，则自动请求验证码图片
    2. 使用 ddddocr 识别验证码
    3. 如果识别结果是算术表达式，例如 1o+4，则修正成 10+4 并计算为 14
    4. 自动填充最终计算结果
    5. 如果没有验证码，则跳过验证码逻辑
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

        # ddddocr 实例缓存，避免每次识别都重新初始化
        self._ocr = None

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

        self.context = self.launch_browser_context()

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)

        self.login()

        return self.page

    def launch_browser_context(self):
        """
        启动浏览器上下文。

        重点：
        打包成 exe 后，不使用 Playwright 自带 Chromium。
        直接调用本机安装的 Edge / Chrome。
        """

        last_error = None

        browser_channels = [
            ("msedge", "Microsoft Edge"),
            ("chrome", "Google Chrome"),
        ]

        for channel, browser_name in browser_channels:
            try:
                print(f"尝试启动本机浏览器：{browser_name}")

                return self.playwright.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=self.headless,
                    slow_mo=self.slow_mo,
                    accept_downloads=True,
                    channel=channel,
                )

            except Exception as e:
                last_error = e
                print(f"启动 {browser_name} 失败：{e}")

        raise RuntimeError(
            "启动浏览器失败。\n\n"
            "当前 exe 版本需要电脑本机安装 Microsoft Edge 或 Google Chrome。\n"
            "请确认电脑已安装 Edge 或 Chrome 后再运行。\n\n"
            f"最后一次错误：{last_error}"
        )

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
            max_retry = 5

            for attempt in range(1, max_retry + 1):
                print(f"开始登录，第 {attempt} 次尝试")

                try:
                    self._fill_username(page)
                    self._fill_password(page)

                    # 如果页面上有验证码，则识别、计算并填写
                    self._fill_captcha_if_present(page)

                    page.get_by_role("button", name="登录").click()
                    page.wait_for_timeout(3000)

                    # 登录后进入 Luna
                    page.goto(config.JUMP_LUNA_URL)
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(2500)

                    # 如果已经不在登录页，说明登录成功
                    if not self.is_login_page(page):
                        print("JumpServer 登录成功。")
                        return

                    print("登录后仍停留在登录页，可能是验证码错误、账号密码错误或需要二次验证。")

                except Exception as e:
                    print(f"本次登录尝试失败：{e}")

                # 到这里说明本次失败了
                if attempt < max_retry:
                    print("准备刷新验证码后重试。")

                    try:
                        # 确保回到登录页
                        if not self.is_login_page(page):
                            page.goto(config.JUMP_LOGIN_URL)
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(1000)

                        self._refresh_captcha_if_present(page)
                        page.wait_for_timeout(1000)

                    except Exception as refresh_error:
                        print(f"刷新验证码失败，重新打开登录页：{refresh_error}")
                        page.goto(config.JUMP_LOGIN_URL)
                        page.wait_for_load_state("domcontentloaded")
                        page.wait_for_timeout(1500)

                    continue

                raise RuntimeError(
                    "JumpServer 登录失败。\n"
                    "已重试 3 次。\n"
                    "请检查账号密码是否正确，或者验证码识别失败，或者页面需要二次验证。"
                )

        # 如果没有识别到登录表单，也尝试进入 Luna
        page.goto(config.JUMP_LUNA_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)

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
            password_input = page.locator("input[type='password']")
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
            password_input = page.locator("input[type='password']")
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

    def _get_ocr(self):
        """
        初始化 ddddocr。

        放在方法里 import 的好处：
        如果某些环境暂时没有验证码，就不会因为没装 ddddocr 直接导致程序启动失败。
        只有真的需要识别验证码时才会导入。
        """

        if self._ocr is not None:
            return self._ocr

        try:
            import ddddocr
        except ImportError:
            raise RuntimeError(
                "当前环境没有安装 ddddocr。\n"
                "请先执行：pip install ddddocr"
            )

        try:
            self._ocr = ddddocr.DdddOcr(show_ad=False)
        except TypeError:
            # 兼容部分旧版本 ddddocr，不支持 show_ad 参数
            self._ocr = ddddocr.DdddOcr()

        # 限制识别范围，让 OCR 更倾向于识别数字和运算符
        try:
            self._ocr.set_ranges("0123456789+-*/xX×÷=oOIl|")
        except Exception:
            pass

        return self._ocr

    def _get_captcha_img_locator(self, page):
        """
        获取验证码图片 locator。

        你的验证码图片链接类似：
        /core/auth/captcha/image/cec7e38f1e2ff890a7db276e9a13bc67bdc8003f/

        所以这里主要匹配 src 里包含 captcha/image 的 img。
        """

        return page.locator(
            "img[src*='/core/auth/captcha/image/'], "
            "img[src*='/captcha/image/'], "
            "img[src*='captcha']"
        )

    def _has_captcha_image(self, page) -> bool:
        """
        判断页面上是否存在验证码图片。
        """

        try:
            captcha_img = self._get_captcha_img_locator(page)

            if captcha_img.count() == 0:
                return False

            for i in range(captcha_img.count()):
                item = captcha_img.nth(i)
                if item.is_visible():
                    return True

        except Exception:
            pass

        return False

    def _get_captcha_image_url(self, page) -> str | None:
        """
        获取验证码图片地址。

        页面上可能是相对路径：
        /core/auth/captcha/image/xxxx/

        这里会转换成完整 URL：
        https://jps.dyna.tech/core/auth/captcha/image/xxxx/
        """

        try:
            captcha_img = self._get_captcha_img_locator(page)

            if captcha_img.count() == 0:
                return None

            for i in range(captcha_img.count()):
                item = captcha_img.nth(i)

                if not item.is_visible():
                    continue

                src = item.get_attribute("src")

                if not src:
                    continue

                full_url = urljoin(page.url, src)
                return full_url

        except Exception as e:
            print(f"获取验证码图片地址失败：{e}")

        return None

    def _recognize_captcha(self, page, image_url: str) -> str:
        """
        请求验证码图片，并使用 ddddocr 识别。

        当前验证码是算术验证码，例如：
        10+4

        ddddocr 可能识别成：
        1o+4

        所以这里会：
        1. 先 OCR 识别
        2. 修正常见识别错误
        3. 计算算术结果
        4. 返回最终要填写的验证码，例如 14
        """

        print(f"开始获取验证码图片：{image_url}")

        response = page.context.request.get(
            image_url,
            headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )

        if not response.ok:
            raise RuntimeError(f"获取验证码图片失败，状态码：{response.status}")

        image_bytes = response.body()

        if not image_bytes:
            raise RuntimeError("获取到的验证码图片内容为空。")

        ocr = self._get_ocr()

        raw_text = ocr.classification(image_bytes)
        raw_text = str(raw_text).strip()

        print(f"验证码 OCR 原始识别结果：{raw_text}")

        if not raw_text:
            raise RuntimeError("验证码识别结果为空。")

        final_code = self._calculate_captcha_result(raw_text)

        print(f"验证码最终填写结果：{final_code}")

        return final_code

    def _normalize_captcha_text(self, text: str) -> str:
        """
        修正 ddddocr 对算术验证码的常见误识别。

        重点：
        这里不急着判断表达式是否合法，只做字符清洗和替换。
        后面再从清洗后的字符串中提取真正的算式。
        """

        if text is None:
            return ""

        text = str(text).strip()

        # 去掉空格和常见空白
        text = text.replace(" ", "")
        text = text.replace("\n", "")
        text = text.replace("\t", "")

        replace_map = {
            # 0 的常见误识别
            "o": "0",
            "O": "0",
            "Q": "0",

            # 1 的常见误识别
            "l": "1",
            "I": "1",
            "|": "1",

            # 乘号
            "×": "*",
            "x": "*",
            "X": "*",

            # 除号
            "÷": "/",
            "／": "/",

            # 加减等号
            "＋": "+",
            "－": "-",
            "—": "-",
            "–": "-",
            "＝": "=",
        }

        for old, new in replace_map.items():
            text = text.replace(old, new)

        # 只保留数字、运算符、等号
        text = re.sub(r"[^0-9+\-*/=]", "", text)

        return text

    def _extract_captcha_expression(self, raw_text: str) -> str:
        """
        从 OCR 结果里提取真正的算术表达式。

        处理场景：

        1. 正常：
           8-4      -> 8-4
           10+4     -> 10+4
           7*9      -> 7*9

        2. 等号或干扰线被识别成 -
           7*2-     -> 7*2
           -7*9-    -> 7*9
           10+4-    -> 10+4

        3. 运算符被识别到末尾：
           62-      -> 6-2
           84-      -> 8-4
           104+     -> 10+4
        """

        expression = self._normalize_captcha_text(raw_text)

        print(f"验证码修正后字符串：{expression}")

        if not expression:
            raise RuntimeError(
                f"验证码表达式解析失败。\n"
                f"OCR 原始结果：{raw_text}\n"
                f"修正后结果为空"
            )

        # 如果有等号，优先取等号前面的内容
        # 例如 8-4= -> 8-4
        if "=" in expression:
            before_equal = expression.split("=")[0]
            if before_equal:
                expression = before_equal

        # 第一优先级：
        # 从字符串中直接提取正常表达式
        # 例如：
        # -7*9- -> 7*9
        # 7*2-  -> 7*2
        # 8-4   -> 8-4
        normal_matches = re.findall(r"\d{1,3}[+\-*/]\d{1,3}", expression)

        if normal_matches:
            # 通常只有一个，取第一个即可
            real_expression = normal_matches[0]
            print(f"验证码提取到正常表达式：{real_expression}")
            return real_expression

        # 第二优先级：
        # 处理 62-、84-、104+ 这种，运算符跑到最后的情况
        tail_operator_match = re.fullmatch(r"(\d{2,})([+\-*/])", expression)

        if tail_operator_match:
            numbers = tail_operator_match.group(1)
            operator = tail_operator_match.group(2)

            if len(numbers) == 2:
                # 62- -> 6-2
                left_text = numbers[0]
                right_text = numbers[1]
            else:
                # 104+ -> 10+4
                # 123+ -> 12+3
                left_text = numbers[:-1]
                right_text = numbers[-1]

            real_expression = f"{left_text}{operator}{right_text}"
            print(f"验证码运算符末尾修正后表达式：{real_expression}")
            return real_expression

        # 第三优先级：
        # 处理 -84、+95 这种，运算符跑到最前面的情况
        head_operator_match = re.fullmatch(r"([+\-*/])(\d{2,})", expression)

        if head_operator_match:
            operator = head_operator_match.group(1)
            numbers = head_operator_match.group(2)

            if len(numbers) == 2:
                left_text = numbers[0]
                right_text = numbers[1]
            else:
                left_text = numbers[:-1]
                right_text = numbers[-1]

            real_expression = f"{left_text}{operator}{right_text}"
            print(f"验证码运算符开头修正后表达式：{real_expression}")
            return real_expression

        raise RuntimeError(
            f"验证码表达式解析失败。\n"
            f"OCR 原始结果：{raw_text}\n"
            f"修正后结果：{expression}"
        )

    def _calculate_captcha_result(self, raw_text: str) -> str:
        """
        将 OCR 识别出的算术表达式计算成最终验证码。

        不使用 eval，避免不安全。
        """

        expression = self._extract_captcha_expression(raw_text)

        print(f"验证码最终用于计算的表达式：{expression}")

        match = re.fullmatch(r"(\d+)([+\-*/])(\d+)", expression)

        if not match:
            raise RuntimeError(
                f"验证码表达式解析失败。\n"
                f"OCR 原始结果：{raw_text}\n"
                f"最终表达式：{expression}"
            )

        left = int(match.group(1))
        operator = match.group(2)
        right = int(match.group(3))

        if operator == "+":
            result = left + right
        elif operator == "-":
            result = left - right
        elif operator == "*":
            result = left * right
        elif operator == "/":
            if right == 0:
                raise RuntimeError("验证码表达式除数为 0，无法计算。")

            result = left / right

            if result.is_integer():
                result = int(result)
        else:
            raise RuntimeError(f"不支持的验证码运算符：{operator}")

        return str(result)

    def _fill_captcha_input(self, page, code: str):
        """
        填写验证码输入框。

        如果后续报错：
        页面上发现了验证码图片，但是没有找到验证码输入框。

        就说明验证码输入框的 selector 和这里不匹配。
        到时候 F12 看一下验证码输入框的 placeholder/name/id/class，再补一个 selector。
        """

        selectors = [
            "input[placeholder*='验证码']",
            "input[placeholder*='校验码']",
            "input[placeholder*='captcha']",
            "input[aria-label*='验证码']",
            "input[name*='captcha']",
            "input[id*='captcha']",
            "input[class*='captcha']",
            "input[name*='Captcha']",
            "input[id*='Captcha']",
            "input[class*='Captcha']",
            "input[name*='code']",
            "input[id*='code']",
            "input[class*='code']",
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector)

                if locator.count() == 0:
                    continue

                for i in range(locator.count()):
                    item = locator.nth(i)

                    if item.is_visible():
                        item.fill(code)
                        print("验证码已填写。")
                        return

            except Exception:
                continue

        raise RuntimeError(
            "页面上发现了验证码图片，但是没有找到验证码输入框。\n"
            "请检查验证码输入框的 placeholder/name/id/class，"
            "然后修改 _fill_captcha_input() 里的 selectors。"
        )

    def _fill_captcha_if_present(self, page):
        """
        如果页面存在验证码，则识别并填写。
        如果没有验证码，则什么都不做。
        """

        if not self._has_captcha_image(page):
            print("当前页面没有验证码，不需要填写。")
            return

        image_url = self._get_captcha_image_url(page)

        if not image_url:
            raise RuntimeError("页面上发现了验证码图片，但是没有获取到验证码图片地址。")

        code = self._recognize_captcha(page, image_url)
        self._fill_captcha_input(page, code)

    def _refresh_captcha_if_present(self, page):
        """
        如果验证码识别失败，下一次重试前刷新验证码。

        你说验证码图片点击后会刷新，所以这里直接 click 验证码图片。
        """

        try:
            captcha_img = self._get_captcha_img_locator(page)

            if captcha_img.count() == 0:
                return

            for i in range(captcha_img.count()):
                item = captcha_img.nth(i)

                if item.is_visible():
                    item.click()
                    print("已点击刷新验证码。")
                    page.wait_for_timeout(1000)
                    return

        except Exception as e:
            print(f"刷新验证码失败，继续重试：{e}")

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
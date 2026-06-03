# -*- coding: utf-8 -*-

import re
import time
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import jumpserver_config as config
from playwright.sync_api import Page, Locator


def click_title_by_prefix(page, prefix: str, timeout: int = 10000):
    """
    点击 title 为 prod / prod (4) 这类元素。
    """

    pattern = re.compile(rf"^{re.escape(prefix)}(?:\s*\(\d+\))?$")
    locator = page.get_by_title(pattern).first
    locator.wait_for(state="visible", timeout=timeout)
    locator.click()


def open_file_manager(page, log_func=print):
    """
    打开 JumpServer 文件管理弹窗。

    按你重新录制的流程：
    1. 进入 luna
    2. prod -> QA -> 10.0.11.247
    3. 点击连接
    4. 点击文件管理
    5. 点击连接，弹出文件管理页面
    """

    log_func("准备进入 JumpServer Luna 页面...")
    page.goto(config.JUMP_LUNA_URL)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)

    log_func("准备选择资产：prod -> QA -> 10.0.11.247")

    click_title_by_prefix(page, config.ASSET_ENV_TITLE)
    page.wait_for_timeout(400)

    click_title_by_prefix(page, config.ASSET_GROUP_TITLE)
    page.wait_for_timeout(400)

    page.get_by_title(config.ASSET_HOST_TITLE).click()
    page.wait_for_timeout(400)

    log_func("点击资产连接按钮...")
    page.get_by_role("button", name="连接").click()
    page.wait_for_timeout(2000)

    log_func("切换到文件管理...")
    page.get_by_text("文件管理").click()
    page.wait_for_timeout(800)

    # 录制代码里这里有 dialog dismiss，保留兜底
    try:
        page.once("dialog", lambda dialog: dialog.dismiss())
    except Exception:
        pass

    log_func("打开文件管理弹窗...")
    with page.expect_popup() as page1_info:
        page.get_by_text("连接", exact=True).click()

    file_page = page1_info.value
    file_page.wait_for_load_state("domcontentloaded")
    file_page.wait_for_timeout(2500)

    log_func("文件管理弹窗已打开。")
    return file_page


def enter_file_manager_target_dir(file_page, log_func=print):
    """
    进入文件管理中的目标目录：
    Home/Default/prod/QA/qa-press
    """

    log_func("准备进入文件管理目标目录...")

    for title in config.FILE_MANAGER_TITLES:
        try:
            locator = file_page.get_by_title(title).first
            locator.wait_for(state="visible", timeout=10000)
            locator.click()
            file_page.wait_for_timeout(600)
            log_func(f"已点击：{title}")
        except PlaywrightTimeoutError:
            log_func(f"未找到或无需点击：{title}")

    file_page.wait_for_timeout(2000)
    log_func("文件区域加载完成。")


def normalize_filename(text: str) -> str:
    """
    标准化文件名：
    去掉 JumpServer 页面里可能出现的零宽字符、空格、换行等不可见字符。
    """
    if text is None:
        return ""

    text = str(text)

    # 去掉常见不可见字符
    text = text.replace("\u200b", "")
    text = text.replace("\u200c", "")
    text = text.replace("\u200d", "")
    text = text.replace("\ufeff", "")
    text = text.replace("\xa0", "")

    # 去掉普通空格、换行、制表符
    text = re.sub(r"\s+", "", text)

    return text.strip()


def extract_jtl_filenames(text: str) -> list[str]:
    """
    从页面元素文本中提取 .jtl 文件名。

    有时元素文本可能不只是文件名，
    可能带大小、日期、其他隐藏文本，所以这里用正则提取真正的 .jtl 名称。
    """

    if text is None:
        return []

    clean_text = normalize_filename(text)

    if not clean_text:
        return []

    # 匹配：
    # ng_cbr_001_credit_ng_debug_5_10_log.jtl
    # ng_cbr_001_credit_ng_formal_5_110_log.jtl
    # ng_cbr_001_credit_ng_formal_tail_1_4_log.jtl
    names = re.findall(r"[\w.\-]+?\.jtl", clean_text, flags=re.IGNORECASE)

    result = []
    seen = set()

    for name in names:
        name = normalize_filename(name)

        if not name.lower().endswith(".jtl"):
            continue

        if name not in seen:
            seen.add(name)
            result.append(name)

    return result


def _get_jtl_candidate_locators(file_page: Page):
    """
    获取可能包含文件名的候选元素。

    前几个是 elfinder 文件管理器常见 class。
    最后一个 text=/\\.jtl/ 是兜底。
    """

    return [
        ("elfinder-cwd-filename", file_page.locator(".elfinder-cwd-filename")),
        ("elfinder-cwd-file-wrapper", file_page.locator(".elfinder-cwd-file-wrapper")),
        ("elfinder-cwd-file", file_page.locator(".elfinder-cwd-file")),
        ("text-jtl", file_page.locator("text=/\\.jtl/")),
    ]


def _read_locator_text(locator: Locator) -> str:
    """
    读取 locator 文本。
    """

    try:
        return locator.inner_text(timeout=1000)
    except Exception:
        try:
            return locator.text_content(timeout=1000) or ""
        except Exception:
            return ""


def scan_jtl_file_items_once(file_page: Page, log_func=print) -> list[tuple[str, Locator]]:
    """
    扫描当前页面上所有 .jtl 文件。

    返回：
        [
            ("xxx_debug_5_10_log.jtl", locator),
            ("xxx_formal_5_110_log.jtl", locator),
        ]
    """

    results = []
    seen = set()

    for selector_name, candidates in _get_jtl_candidate_locators(file_page):
        try:
            count = candidates.count()
        except Exception:
            count = 0

        if count <= 0:
            continue

        log_func(f"扫描选择器 {selector_name}，识别到元素数量：{count}")

        for i in range(count):
            item = candidates.nth(i)
            raw_text = _read_locator_text(item)

            if not raw_text:
                continue

            filenames = extract_jtl_filenames(raw_text)

            # 兜底：如果提取不到，但是整体文本本身就是 .jtl 文件名
            if not filenames:
                normalized_text = normalize_filename(raw_text)
                if normalized_text.lower().endswith(".jtl"):
                    filenames = [normalized_text]

            for filename in filenames:
                filename = normalize_filename(filename)

                if not filename.lower().endswith(".jtl"):
                    continue

                if filename in seen:
                    continue

                seen.add(filename)
                results.append((filename, item))

                log_func(f"页面 JTL 文件：{raw_text} -> {filename}")

    return results


def find_file_item(
    file_page: Page,
    remote_filename: str,
    timeout: int = 30000,
    log_func=print,
) -> Locator:
    """
    在 JumpServer 文件管理页面中查找指定文件。

    不使用 exact=True。
    因为 JumpServer 页面文件名可能包含零宽字符。
    """

    target_name = normalize_filename(remote_filename)

    log_func(f"准备查找文件：{remote_filename}")
    log_func(f"标准化后目标文件名：{target_name}")

    try:
        file_page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    file_page.wait_for_timeout(1000)

    end_time = time.time() + timeout / 1000

    while time.time() < end_time:
        jtl_items = scan_jtl_file_items_once(file_page, log_func=log_func)

        log_func(f"当前页面识别到 JTL 文件数量：{len(jtl_items)}")

        for filename, item in jtl_items:
            if filename == target_name:
                log_func(f"已找到目标文件：{filename}")
                item.scroll_into_view_if_needed(timeout=5000)
                item.wait_for(state="visible", timeout=5000)
                return item

        file_page.wait_for_timeout(1000)

    raise TimeoutError(
        f"未在页面找到目标文件：{remote_filename}，"
        f"标准化后目标文件名：{target_name}"
    )


def find_file_items_by_keywords(
    file_page: Page,
    keywords: list[str],
    timeout: int = 30000,
    log_func=print,
) -> list[str]:
    """
    按关键字模糊匹配页面上的 JTL 文件。

    例如 keywords:
        [
            "ng_cbr_001_credit_ng_debug",
            "ng_cbr_001_credit_ng_formal"
        ]

    只要文件名包含其中任意一个关键字，就会被下载。

    返回：
        [
            "ng_cbr_001_credit_ng_debug_5_10_log.jtl",
            "ng_cbr_001_credit_ng_formal_5_110_log.jtl",
            "ng_cbr_001_credit_ng_formal_tail_1_4_log.jtl",
        ]
    """

    normalized_keywords = []

    for keyword in keywords:
        keyword = normalize_filename(keyword)
        if keyword:
            normalized_keywords.append(keyword)

    if not normalized_keywords:
        raise ValueError("日志匹配关键字不能为空。")

    log_func("准备按关键字模糊匹配 JTL 文件：")
    for keyword in normalized_keywords:
        log_func(f"- {keyword}")

    try:
        file_page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    file_page.wait_for_timeout(1000)

    end_time = time.time() + timeout / 1000

    while time.time() < end_time:
        jtl_items = scan_jtl_file_items_once(file_page, log_func=log_func)

        log_func(f"当前页面识别到 JTL 文件数量：{len(jtl_items)}")

        matched = []
        seen = set()

        for filename, _item in jtl_items:
            is_match = any(keyword in filename for keyword in normalized_keywords)

            if is_match and filename not in seen:
                seen.add(filename)
                matched.append(filename)

        if matched:
            def sort_key(name: str):
                for index, keyword in enumerate(normalized_keywords):
                    if keyword in name:
                        return index, name
                return len(normalized_keywords), name

            matched.sort(key=sort_key)

            log_func("本次匹配到的 JTL 文件：")
            for name in matched:
                log_func(f"- {name}")

            return matched

        file_page.wait_for_timeout(1000)

    raise TimeoutError(
        "未在页面找到匹配的 JTL 文件，匹配关键字："
        + ", ".join(normalized_keywords)
    )


def right_click_recorded_anchor(file_page, log_func=print) -> bool:
    """
    按你录制的方式：
    先点击 hsperfdata_wwwsystemd-private，再右键它。

    如果这个元素找不到，再兜底右键任意已有文件。
    """

    log_func("尝试按录制方式右键 hsperfdata_wwwsystemd-private...")

    # 你录制的是 nth(1)，这里优先 nth(1)，再试 first
    for index in [1, 0]:
        try:
            locator = file_page.get_by_text("hsperfdata_wwwsystemd-private").nth(index)
            locator.wait_for(state="visible", timeout=3000)
            locator.click()
            file_page.wait_for_timeout(300)
            locator.click(button="right")
            file_page.wait_for_timeout(600)
            log_func(f"已右键 hsperfdata_wwwsystemd-private，index={index}")
            return True
        except Exception:
            continue

    log_func("没有找到 hsperfdata_wwwsystemd-private，尝试右键任意已有文件。")

    # 兜底：右键任意已有文件或目录
    selectors = [
        ".elfinder-cwd-file-wrapper",
        ".elfinder-cwd-file",
        ".elfinder-cwd-filename",
    ]

    for selector in selectors:
        try:
            locator = file_page.locator(selector).first
            locator.wait_for(state="visible", timeout=3000)
            locator.click()
            file_page.wait_for_timeout(300)
            locator.click(button="right", force=True)
            file_page.wait_for_timeout(600)
            log_func(f"已右键已有文件区域：{selector}")
            return True
        except Exception:
            continue

    return False


def click_upload_file_menu(file_page, log_func=print):
    """
    点击右键菜单里的“上传文件”。
    """

    log_func("准备点击右键菜单：上传文件")

    candidates = [
        file_page.get_by_text("上传文件", exact=True).first,
        file_page.get_by_text("上传文件", exact=False).first,
        file_page.locator("text=/上传文件/").first,
        file_page.locator("text=/上传/").first,
    ]

    last_error = None

    for locator in candidates:
        try:
            locator.wait_for(state="visible", timeout=5000)
            locator.click(force=True)
            file_page.wait_for_timeout(1000)
            log_func("已点击“上传文件”。")
            return
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"右键后没有找到“上传文件”菜单项。最后错误：{last_error}")


def open_upload_dialog(file_page, log_func=print):
    """
    打开上传文件弹窗。

    注意：
    这里不会直接打开系统文件选择器。
    它只是完成：
    右键已有文件/目录 -> 点击 上传文件 -> 出现上传窗口
    """

    log_func("准备打开上传文件弹窗...")

    ok = right_click_recorded_anchor(file_page, log_func=log_func)

    if not ok:
        raise RuntimeError("没有成功右键文件/目录，无法打开上传菜单。")

    click_upload_file_menu(file_page, log_func=log_func)

    log_func("上传文件弹窗已尝试打开。")


def set_files_in_upload_dialog(file_page, upload_paths: list[str], log_func=print):
    """
    在上传弹窗中选择本地文件。

    按录制流程，点击弹窗里的 Choose File。
    同时增加兜底：如果能找到 input[type=file]，直接 set_input_files。
    """

    log_func("准备在上传窗口中选择本地文件...")

    # 策略 1：直接找 input[type=file]，最快最稳
    try:
        file_input = file_page.locator("input[type='file']").first
        file_input.wait_for(state="attached", timeout=5000)
        file_input.set_input_files(upload_paths)
        log_func("已通过 input[type=file] 设置上传文件。")
        return
    except Exception as e:
        log_func(f"直接设置 input[type=file] 失败，准备点击 Choose File。原因：{e}")

    # 策略 2：按录制流程点击 Choose File
    choose_file_buttons = [
        file_page.get_by_role("button", name="Choose File").first,
        file_page.get_by_role("button", name=re.compile("Choose File|选择文件|浏览|Browse")).first,
        file_page.locator("text=/Choose File|选择文件|浏览|Browse/").first,
    ]

    last_error = None

    for button in choose_file_buttons:
        try:
            button.wait_for(state="visible", timeout=5000)

            with file_page.expect_file_chooser(timeout=30000) as file_chooser_info:
                button.click(force=True)

            file_chooser = file_chooser_info.value
            file_chooser.set_files(upload_paths)

            log_func("已通过 Choose File 选择上传文件。")
            return

        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"没有找到上传弹窗里的 Choose File 按钮。最后错误：{last_error}")
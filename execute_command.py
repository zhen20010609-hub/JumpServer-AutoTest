# -*- coding: utf-8 -*-

from pathlib import Path
import shlex
import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from jumpserver_login import JumpServerSession
from debug_usage_store import DebugUsageStore

import jumpserver_config as config


def shell_quote(value: str) -> str:
    """
    Linux shell 参数安全转义，避免文件名中有空格、中文、特殊字符导致命令失败。
    """
    return shlex.quote(value)


def validate_execute_params(
    run_debug: bool,
    run_formal: bool,
    throughput_limit_tps: int | None,
    total_quota: int,
    smoke_used_count: int,
    debug_threads: int,
    debug_loops: int,
    formal_threads: int,
):
    """
    基础参数校验。
    """

    if throughput_limit_tps is not None and throughput_limit_tps <= 0:
        raise ValueError("限制目标 TPS 如果填写，必须大于 0。")

    if total_quota < 0:
        raise ValueError("总额度不能小于 0。")

    if smoke_used_count < 0:
        raise ValueError("单条消耗不能小于 0。")

    if run_debug:
        if debug_threads <= 0:
            raise ValueError("勾选调试压测时，debug 线程必须大于 0。")
        if debug_loops <= 0:
            raise ValueError("勾选调试压测时，debug 循环必须大于 0。")

    if run_formal:
        if formal_threads <= 0:
            raise ValueError("勾选正式压测时，formal 线程必须大于 0。")


def build_commands(
    local_jmx_path: str,
    local_sample_paths: list[str] | None = None,
    run_smoke: bool = True,
    run_debug: bool = True,
    run_formal: bool = True,
    previous_debug_used_count: int = 0,
    throughput_limit_tps: int | None = None,
    total_quota: int = config.TOTAL_QUOTA,
    smoke_used_count: int = 1,
    debug_threads: int = config.DEBUG_THREADS,
    debug_loops: int = config.DEBUG_LOOPS,
    formal_threads: int = config.FORMAL_THREADS,
    count_debug_used: bool = config.COUNT_DEBUG_USED,
) -> list[str]:
    """
    生成服务器端自动压测 shell 脚本，并返回需要逐行输入到 Web Terminal 的命令。
    """

    local_sample_paths = local_sample_paths or []

    if not local_jmx_path:
        raise ValueError("请先选择 JMX 脚本文件。")

    validate_execute_params(
        run_debug=run_debug,
        run_formal=run_formal,
        throughput_limit_tps=throughput_limit_tps,
        total_quota=total_quota,
        smoke_used_count=smoke_used_count,
        debug_threads=debug_threads,
        debug_loops=debug_loops,
        formal_threads=formal_threads,
    )

    jmx_path = Path(local_jmx_path)
    jmx_filename = jmx_path.name
    jmx_stem = jmx_path.stem

    q_jmx_filename = shell_quote(jmx_filename)

    q_remote_upload_dir = shell_quote(config.REMOTE_UPLOAD_DIR)
    q_remote_work_dir = shell_quote(config.REMOTE_WORK_DIR)
    q_jmeter_bin_dir = shell_quote(config.JMETER_BIN_DIR)

    remote_jmx_path = f"{config.REMOTE_WORK_DIR}/{jmx_filename}"

    if throughput_limit_tps is not None:
        throughput_per_min = throughput_limit_tps * 60
        throughput_jmeter_arg = f" -Jthroughput_per_min={throughput_per_min}"
    else:
        throughput_per_min = None
        throughput_jmeter_arg = ""

    actual_smoke_used_count = smoke_used_count if run_smoke else 0

    if run_debug and count_debug_used:
        current_debug_used_count = debug_threads * debug_loops
    else:
        current_debug_used_count = 0

    if count_debug_used:
        total_debug_used_count = previous_debug_used_count + current_debug_used_count
    else:
        total_debug_used_count = 0

    remaining_count = total_quota - actual_smoke_used_count - total_debug_used_count

    if remaining_count < 0:
        raise ValueError(
            f"额度不足：总额度={total_quota}，"
            f"单条测试={actual_smoke_used_count}，"
            f"debug累计={total_debug_used_count}，已经超出额度。"
        )

    formal_loops = remaining_count // formal_threads if formal_threads > 0 else 0

    formal_main_count = formal_threads * formal_loops
    ignored_tail_count = remaining_count - formal_main_count

    debug_jtl = f"{jmx_stem}_debug_{debug_threads}_{debug_loops}_log.jtl"
    formal_jtl = f"{jmx_stem}_formal_{formal_threads}_{formal_loops}_log.jtl"

    debug_jtl_work_path = f"{config.REMOTE_WORK_DIR}/{debug_jtl}"
    debug_jtl_tmp_path = f"{config.REMOTE_UPLOAD_DIR}/{debug_jtl}"

    formal_jtl_work_path = f"{config.REMOTE_WORK_DIR}/{formal_jtl}"
    formal_jtl_tmp_path = f"{config.REMOTE_UPLOAD_DIR}/{formal_jtl}"

    planned_debug_log_text = debug_jtl_tmp_path if run_debug else "未执行 debug"
    planned_formal_log_text = (
        formal_jtl_tmp_path if run_formal and formal_loops > 0 else "未执行 formal"
    )

    server_script_path = "/tmp/run_jmeter_auto.sh"

    script_lines = [
        "#!/bin/bash",
        "set -e",
        "",
        "echo '===== 开始执行自动压测流程 ====='",
        "",
        "sudo su - www <<'EOSU'",
        "set -e",
        "",
        f"cd {q_remote_upload_dir}",
        "",
        "echo '===== 处理上传文件权限和移动 ====='",
        (
            f"if [ -f {q_jmx_filename} ]; then "
            f"sudo chown www.www {q_jmx_filename}; "
            f"chmod 777 {q_jmx_filename}; "
            f"mv {q_jmx_filename} {q_remote_work_dir}; "
            f"else echo '未在 /tmp 找到 {jmx_filename}，可能已移动到 zz_polly，跳过移动 jmx'; "
            f"fi"
        ),
    ]

    for sample_path in local_sample_paths:
        sample_filename = Path(sample_path).name
        q_sample_filename = shell_quote(sample_filename)

        script_lines.append(
            (
                f"if [ -f {q_sample_filename} ]; then "
                f"sudo chown www.www {q_sample_filename}; "
                f"chmod 777 {q_sample_filename}; "
                f"mv {q_sample_filename} {q_remote_work_dir}; "
                f"else echo '未在 /tmp 找到 {sample_filename}，可能已移动到 zz_polly，跳过移动样本文件'; "
                f"fi"
            )
        )

    script_lines.extend([
        "",
        f"cd {q_remote_work_dir}",
        (
            f"if [ ! -f {q_jmx_filename} ]; then "
            f"echo '错误：zz_polly 目录下找不到 {jmx_filename}'; "
            f"exit 1; "
            f"fi"
        ),
        "",
        "echo '===== 替换 JMX 中的 txt/csv 样本路径 ====='",
        (
            "sed -i -E "
            f"'s#(<stringProp name=\"filename\">)([^<]*[/\\\\])?([^/\\\\<]+\\.(txt|csv))"
            f"(</stringProp>)#\\1{config.REMOTE_WORK_DIR}/\\3\\5#g' "
            f"{q_jmx_filename}"
        ),
        "",
    ])

    if run_smoke:
        script_lines.extend([
            "echo '===== 开始单条测试 / smoke：使用原始脚本配置执行 ====='",
            f"cd {q_jmeter_bin_dir}",
            (
                f"./jmeter -n "
                f"-t {shell_quote(remote_jmx_path)}"
            ),
            f"cd {q_remote_work_dir}",
            "echo '===== 单条测试 / smoke 完成 ====='",
            "",
        ])
    else:
        script_lines.extend([
            "echo '跳过单条测试 / smoke。'",
            "",
        ])

    script_lines.extend([
        "echo '===== 开始参数化 JMX：线程数 / 循环次数 ====='",

        (
            "perl -0pi -e "
            f"'s#<(?:intProp|stringProp) name=\"ThreadGroup.num_threads\">.*?</(?:intProp|stringProp)>"
            f"#<stringProp name=\"ThreadGroup.num_threads\">\\${{__P(threads,1)}}</stringProp>#gs' "
            f"{q_jmx_filename}"
        ),

        (
            "perl -0pi -e "
            f"'s#<(?:intProp|stringProp) name=\"LoopController.loops\">.*?</(?:intProp|stringProp)>"
            f"#<stringProp name=\"LoopController.loops\">\\${{__P(loops,1)}}</stringProp>#gs' "
            f"{q_jmx_filename}"
        ),

        "echo '===== 线程数 / 循环次数参数化完成 ====='",
        "",
    ])

    if throughput_limit_tps is not None:
        script_lines.extend([
            "echo '===== 已填写限制目标 TPS，开始修改 Constant Throughput Timer ====='",

            (
                "perl -0pi -e "
                f"'s#<doubleProp>\\s*<name>throughput</name>\\s*<value>.*?</value>\\s*<savedValue>.*?</savedValue>\\s*</doubleProp>"
                f"#<stringProp name=\"throughput\">\\${{__P(throughput_per_min,60)}}</stringProp>#gs' "
                f"{q_jmx_filename}"
            ),

            (
                "perl -0pi -e "
                f"'s#<(?:doubleProp|stringProp) name=\"throughput\">.*?</(?:doubleProp|stringProp)>"
                f"#<stringProp name=\"throughput\">\\${{__P(throughput_per_min,60)}}</stringProp>#gs' "
                f"{q_jmx_filename}"
            ),

            f"echo 'Constant Throughput Timer 已设置为：{throughput_per_min} / min'",
            "",
        ])
    else:
        script_lines.extend([
            "echo '未填写限制目标 TPS，跳过 Constant Throughput Timer 修改，使用 JMX 原始吞吐量配置。'",
            "",
        ])

    if run_debug:
        script_lines.extend([
            "echo '===== 开始 debug 调试压测 ====='",
            f"cd {q_jmeter_bin_dir}",
            (
                f"./jmeter -n "
                f"-t {shell_quote(remote_jmx_path)} "
                f"-l {shell_quote(debug_jtl_work_path)} "
                f"-Jthreads={debug_threads} "
                f"-Jloops={debug_loops}"
                f"{throughput_jmeter_arg}"
            ),

            "echo '===== 移动 debug 日志到 /tmp ====='",
            (
                f"if [ -f {shell_quote(debug_jtl_work_path)} ]; then "
                f"mv -f {shell_quote(debug_jtl_work_path)} {shell_quote(debug_jtl_tmp_path)}; "
                f"echo 'debug 日志已移动到：{debug_jtl_tmp_path}'; "
                f"else echo '警告：未找到 debug 日志：{debug_jtl_work_path}'; "
                f"fi"
            ),

            f"cd {q_remote_work_dir}",
            "echo '===== debug 调试压测完成 ====='",
            "",
        ])
    else:
        script_lines.extend([
            "echo '跳过 debug 调试压测。'",
            "",
        ])

    if run_formal:
        script_lines.extend([
            "echo '===== 开始 formal 正式压测 ====='",
            f"cd {q_jmeter_bin_dir}",
        ])

        if formal_loops > 0:
            script_lines.append(
                f"./jmeter -n "
                f"-t {shell_quote(remote_jmx_path)} "
                f"-l {shell_quote(formal_jtl_work_path)} "
                f"-Jthreads={formal_threads} "
                f"-Jloops={formal_loops}"
                f"{throughput_jmeter_arg}"
            )

            script_lines.extend([
                "echo '===== 移动 formal 日志到 /tmp ====='",
                (
                    f"if [ -f {shell_quote(formal_jtl_work_path)} ]; then "
                    f"mv -f {shell_quote(formal_jtl_work_path)} {shell_quote(formal_jtl_tmp_path)}; "
                    f"echo 'formal 日志已移动到：{formal_jtl_tmp_path}'; "
                    f"else echo '警告：未找到 formal 日志：{formal_jtl_work_path}'; "
                    f"fi"
                ),
            ])
        else:
            script_lines.append(
                "echo 'formal_loops=0，剩余额度不足以按当前 FORMAL_THREADS 执行一轮 formal，跳过 formal 主压测。'"
            )

        script_lines.extend([
            f"cd {q_remote_work_dir}",
            "echo '===== formal 正式压测完成 ====='",
            "",
        ])
    else:
        script_lines.extend([
            "echo '跳过 formal 正式压测。'",
            "",
        ])

    script_lines.extend([
        "echo '===== 执行计划 ====='",
        f"echo 'RUN_SMOKE={run_smoke}, RUN_DEBUG={run_debug}, RUN_FORMAL={run_formal}'",
        f"echo 'THROUGHPUT_LIMIT_TPS={throughput_limit_tps}, THROUGHPUT_PER_MIN={throughput_per_min}'",
        (
            f"echo 'TOTAL_QUOTA={total_quota}, "
            f"SMOKE_USED_COUNT={actual_smoke_used_count}, "
            f"PREVIOUS_DEBUG_USED_COUNT={previous_debug_used_count}, "
            f"CURRENT_DEBUG_USED_COUNT={current_debug_used_count}, "
            f"TOTAL_DEBUG_USED_COUNT={total_debug_used_count}, "
            f"REMAINING_COUNT={remaining_count}'"
        ),
        f"echo 'DEBUG_CURRENT={debug_threads}线程 x {debug_loops}循环 = {current_debug_used_count}条'",
        f"echo 'DEBUG_TOTAL_USED_FOR_FORMAL={total_debug_used_count}条'",
        f"echo 'FORMAL_MAIN={formal_threads}线程 x {formal_loops}循环 = {formal_main_count}条'",
        f"echo 'IGNORED_TAIL_COUNT={ignored_tail_count}条，因为已取消 formal tail 尾部测试'",
        f"echo 'DEBUG_LOG={planned_debug_log_text}'",
        f"echo 'FORMAL_LOG={planned_formal_log_text}'",
        "",
        "EOSU",
        "",
        "echo '===== 自动压测脚本执行结束 ====='",
    ])

    commands = [
        f"cat > {server_script_path} <<'EOF'",
        *script_lines,
        "EOF",
        f"chmod +x {server_script_path}",
        f"bash {server_script_path}",
    ]

    return commands


def find_terminal_area(page, timeout_seconds: int = 30, log_func=print):
    """
    遍历所有 iframe，查找真正可点击的 xterm 终端区域。
    """

    log_func("开始查找 Web Terminal 可点击区域...")

    terminal_selectors = [
        ".xterm-screen",
        ".xterm-viewport",
        ".xterm",
        "div[class*='xterm']",
    ]

    last_error = None

    for _ in range(timeout_seconds):
        for frame in page.frames:
            for selector in terminal_selectors:
                try:
                    terminal = frame.locator(selector).first

                    if terminal.count() == 0:
                        continue

                    terminal.wait_for(state="visible", timeout=1000)

                    box = terminal.bounding_box()
                    if not box:
                        continue

                    width = box.get("width", 0)
                    height = box.get("height", 0)

                    if width < 100 or height < 50:
                        continue

                    log_func(
                        f"已找到 Web Terminal 可点击区域：selector={selector}, "
                        f"width={width}, height={height}"
                    )

                    return terminal

                except Exception as e:
                    last_error = e
                    continue

        page.wait_for_timeout(1000)

    raise RuntimeError(
        "没有找到 Web Terminal 可点击区域，请确认已经进入压测机终端页面。\n"
        f"最后一次错误：{last_error}"
    )


def connect_press_server(page, log_func=print):
    """
    连接压测机：prod -> QA -> 10.0.11.247。

    重点：
    1. 等 Luna 页面内容真正加载出来，再点 prod
    2. 点击“连接”后，判断是否打开新页面
    3. 返回真正的 Web Terminal 页面
    """

    log_func("准备连接压测机...")

    def is_luna_url() -> bool:
        current_url = page.url.lower()
        return "/luna/" in current_url and "/core/auth/login" not in current_url

    def wait_for_luna_ready(timeout_ms: int = 60000) -> bool:
        log_func("等待 Luna 页面内容加载完成...")

        asset_pattern = re.compile(
            rf"^{re.escape(config.ASSET_ENV_TITLE)}(?:\s*\(\d+\))?$"
        )

        try:
            page.get_by_title(asset_pattern).first.wait_for(
                state="visible",
                timeout=timeout_ms,
            )
            log_func(f"已识别到资产环境：{config.ASSET_ENV_TITLE}")
            return True
        except Exception:
            pass

        try:
            page.get_by_text("文件管理").first.wait_for(
                state="visible",
                timeout=3000,
            )
            log_func("已识别到 Luna 页面内容：文件管理")
            return True
        except Exception:
            pass

        return False

    if not is_luna_url():
        log_func("当前不在 Luna 页面，开始进入 Luna。")
        page.goto(config.JUMP_LUNA_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
    else:
        log_func("当前 URL 已经是 Luna，先等待页面内容加载，不立即点击资产树。")

    if not wait_for_luna_ready(timeout_ms=60000):
        log_func("Luna 页面内容等待超时，尝试重新进入 Luna。")

        page.goto(config.JUMP_LUNA_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)

        if not wait_for_luna_ready(timeout_ms=60000):
            raise RuntimeError(
                "Luna 页面加载超时：没有找到资产环境 "
                f"{config.ASSET_ENV_TITLE}。"
                "请检查页面是否加载很慢、账号是否有资产权限，或 JumpServer 页面是否异常。"
            )

    def click_title_by_prefix(prefix: str, timeout_ms: int = 60000):
        pattern = re.compile(rf"^{re.escape(prefix)}(?:\s*\(\d+\))?$")
        locator = page.get_by_title(pattern).first

        log_func(f"等待并点击：{prefix}")
        locator.wait_for(state="visible", timeout=timeout_ms)
        locator.scroll_into_view_if_needed(timeout=5000)
        locator.click()

    click_title_by_prefix(config.ASSET_ENV_TITLE)
    page.wait_for_timeout(800)

    click_title_by_prefix(config.ASSET_GROUP_TITLE)
    page.wait_for_timeout(800)

    log_func(f"等待并点击压测机：{config.ASSET_HOST_TITLE}")
    host = page.get_by_title(config.ASSET_HOST_TITLE).first
    host.wait_for(state="visible", timeout=60000)
    host.scroll_into_view_if_needed(timeout=5000)
    host.click()
    page.wait_for_timeout(800)

    log_func("等待并点击连接按钮。")
    connect_button = page.get_by_role("button", name="连接").first
    connect_button.wait_for(state="visible", timeout=30000)

    old_pages = list(page.context.pages)
    terminal_page = None

    try:
        with page.context.expect_page(timeout=8000) as new_page_info:
            connect_button.click()

        terminal_page = new_page_info.value
        terminal_page.wait_for_load_state("domcontentloaded", timeout=30000)
        terminal_page.set_default_timeout(15000)
        log_func("检测到 Web Terminal 打开在新页面。")

    except PlaywrightTimeoutError:
        log_func("未检测到新页面，认为 Web Terminal 在当前页面打开。")
        terminal_page = page

    current_pages = list(page.context.pages)

    if terminal_page is page and len(current_pages) > len(old_pages):
        terminal_page = current_pages[-1]
        terminal_page.wait_for_load_state("domcontentloaded", timeout=30000)
        terminal_page.set_default_timeout(15000)
        log_func("兜底检测到新的页面，切换到最新 Web Terminal 页面。")

    log_func("已点击连接，等待 Web Terminal 加载。")

    find_terminal_area(terminal_page, timeout_seconds=90, log_func=log_func)

    log_func("Web Terminal 已加载完成。")

    return terminal_page


def paste_commands_to_terminal(page, commands: list[str], log_func=print):
    """
    逐行输入命令到 Web Terminal。
    """

    log_func("准备逐行输入命令到 Web Terminal...")

    terminal_area = find_terminal_area(page, log_func=log_func)

    box = terminal_area.bounding_box()
    if not box:
        raise RuntimeError("找到终端区域，但是无法获取终端区域坐标。")

    click_x = box["x"] + min(80, box["width"] / 2)
    click_y = box["y"] + min(40, box["height"] / 2)

    page.mouse.click(click_x, click_y)
    page.wait_for_timeout(500)

    for index, cmd in enumerate(commands, start=1):
        log_func(f"正在输入第 {index} 条命令：{cmd}")

        page.mouse.click(click_x, click_y)
        page.wait_for_timeout(100)

        page.keyboard.insert_text(cmd)
        page.keyboard.press("Enter")

        if cmd.startswith("bash "):
            page.wait_for_timeout(1000)
        else:
            page.wait_for_timeout(200)

    log_func("所有命令已逐行输入完成，请在网页终端观察执行结果。")


def wait_until_browser_closed(page, log_func=print):
    """
    无黑框 exe 不能使用 input() 等待用户按回车。
    命令提交后保持浏览器打开，用户手动关闭浏览器窗口后，程序再结束。
    """

    log_func("浏览器将保持打开。压测结束后，请手动关闭浏览器窗口。")

    while True:
        try:
            if page.is_closed():
                log_func("检测到浏览器页面已关闭。")
                break

            page.wait_for_timeout(1000)

        except Exception:
            log_func("检测到浏览器已关闭或连接已断开。")
            break


def execute_jmeter(
    username: str,
    password: str,
    local_jmx_path: str,
    local_sample_paths: list[str] | None,
    run_smoke: bool,
    run_debug: bool,
    run_formal: bool,
    throughput_limit_tps: int | None,
    total_quota: int,
    smoke_used_count: int,
    debug_threads: int,
    debug_loops: int,
    formal_threads: int,
    count_debug_used: bool,
    log_func=print,
):
    """
    登录 JumpServer，连接 Web Terminal，并执行自动压测命令。
    """

    debug_usage_store = DebugUsageStore()
    previous_debug_used_count = debug_usage_store.get_debug_used_count(local_jmx_path)

    log_func(f"当前脚本历史累计 debug 消耗条数：{previous_debug_used_count}")
    log_func(f"debug 记录文件路径：{debug_usage_store.get_store_path()}")

    commands = build_commands(
        local_jmx_path=local_jmx_path,
        local_sample_paths=local_sample_paths,
        run_smoke=run_smoke,
        run_debug=run_debug,
        run_formal=run_formal,
        previous_debug_used_count=previous_debug_used_count,
        throughput_limit_tps=throughput_limit_tps,
        total_quota=total_quota,
        smoke_used_count=smoke_used_count,
        debug_threads=debug_threads,
        debug_loops=debug_loops,
        formal_threads=formal_threads,
        count_debug_used=count_debug_used,
    )

    session = JumpServerSession(
        username=username,
        password=password,
        user_data_dir="jumpserver_exec_browser_profile",
    )

    page = None
    terminal_page = None

    try:
        page = session.start()

        log_func("登录成功，开始自动连接压测机。")

        terminal_page = connect_press_server(page, log_func=log_func)

        log_func("等待 Web Terminal 加载完成...")

        paste_commands_to_terminal(terminal_page, commands, log_func=log_func)

        if run_debug and count_debug_used:
            current_debug_used_count = debug_threads * debug_loops
            new_debug_total = debug_usage_store.add_debug_used_count(
                local_jmx_path=local_jmx_path,
                add_count=current_debug_used_count,
            )
            log_func(
                f"已记录本次 debug 消耗：{current_debug_used_count} 条；"
                f"当前脚本累计 debug 消耗：{new_debug_total} 条。"
            )

        log_func("命令已提交。请等待 Web Terminal 中显示“自动压测脚本执行结束”后，再手动关闭浏览器。")

        wait_until_browser_closed(terminal_page, log_func=log_func)

    except Exception as e:
        log_func(f"执行过程中发生异常：{e}")
        log_func("浏览器将保持打开，方便你查看当前页面。手动关闭浏览器后程序结束。")

        try:
            if terminal_page is not None and not terminal_page.is_closed():
                wait_until_browser_closed(terminal_page, log_func=log_func)
            elif page is not None and not page.is_closed():
                wait_until_browser_closed(page, log_func=log_func)
        except Exception:
            pass

        raise

    finally:
        session.close()


if __name__ == "__main__":
    execute_jmeter(
        username="",
        password="",
        local_jmx_path=r"",
        local_sample_paths=[],
        run_smoke=True,
        run_debug=True,
        run_formal=True,
        throughput_limit_tps=None,
        total_quota=config.TOTAL_QUOTA,
        smoke_used_count=1,
        debug_threads=config.DEBUG_THREADS,
        debug_loops=config.DEBUG_LOOPS,
        formal_threads=config.FORMAL_THREADS,
        count_debug_used=config.COUNT_DEBUG_USED,
    )
# -*- coding: utf-8 -*-

from pathlib import Path
import shlex
import re

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

    当前流程：
    1. JMX / TXT / CSV 从 /tmp 移动到 zz_polly
    2. 替换 JMX 中 txt/csv 样本路径
    3. 可选执行单条测试 smoke，不生成日志
    4. 参数化 JMX：线程数 / 循环次数
    5. 如果填写了限制目标 TPS，才修改 Constant Throughput Timer
    6. 可选执行 debug，日志生成后移动到 /tmp
    7. 可选执行 formal，日志生成后移动到 /tmp
    8. 不执行 formal tail

    吞吐量逻辑：
    - throughput_limit_tps 有值：Constant Throughput Timer = throughput_limit_tps * 60
    - throughput_limit_tps 为空：不修改 Constant Throughput Timer，使用 JMX 原始配置

    debug 额度计算逻辑：
    - previous_debug_used_count：当前 JMX 脚本历史累计 debug 消耗
    - current_debug_used_count：本次如果勾选 debug，本次 debug 消耗
    - formal 扣除：历史 debug + 本次 debug
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

    # ======================
    # 吞吐量限制逻辑
    # ======================
    # 只有填写了限制目标 TPS，才修改 Constant Throughput Timer
    # 不填写时，不修改 JMX 原始吞吐量配置，也不传 -Jthroughput_per_min
    if throughput_limit_tps is not None:
        throughput_per_min = throughput_limit_tps * 60
        throughput_jmeter_arg = f" -Jthroughput_per_min={throughput_per_min}"
    else:
        throughput_per_min = None
        throughput_jmeter_arg = ""

    # ======================
    # 额度计算
    # ======================

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

    # ======================
    # smoke 单条测试
    # ======================

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

    # ======================
    # 参数化线程数 / 循环次数
    # ======================

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

    # ======================
    # 可选修改 Constant Throughput Timer
    # ======================

    if throughput_limit_tps is not None:
        script_lines.extend([
            "echo '===== 已填写限制目标 TPS，开始修改 Constant Throughput Timer ====='",

            # Constant Throughput Timer：doubleProp 子节点格式
            (
                "perl -0pi -e "
                f"'s#<doubleProp>\\s*<name>throughput</name>\\s*<value>.*?</value>\\s*<savedValue>.*?</savedValue>\\s*</doubleProp>"
                f"#<stringProp name=\"throughput\">\\${{__P(throughput_per_min,60)}}</stringProp>#gs' "
                f"{q_jmx_filename}"
            ),

            # Constant Throughput Timer：name 属性格式
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

    # ======================
    # debug 调试压测
    # ======================

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

    # ======================
    # formal 正式压测
    # ======================

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

    # ======================
    # 执行计划输出
    # ======================

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


def connect_press_server(page, log_func=print):
    """
    连接压测机：prod -> QA -> 10.0.11.247。
    """

    log_func("准备连接压测机...")

    page.goto(config.JUMP_LUNA_URL)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)

    def click_title_by_prefix(prefix: str):
        pattern = re.compile(rf"^{re.escape(prefix)}(?:\s*\(\d+\))?$")
        locator = page.get_by_title(pattern).first
        locator.wait_for(state="visible", timeout=10000)
        locator.click()

    click_title_by_prefix(config.ASSET_ENV_TITLE)
    page.wait_for_timeout(500)

    click_title_by_prefix(config.ASSET_GROUP_TITLE)
    page.wait_for_timeout(500)

    page.get_by_title(config.ASSET_HOST_TITLE).click()
    page.wait_for_timeout(500)

    page.get_by_role("button", name="连接").click()
    page.wait_for_timeout(5000)

    log_func("已点击连接，等待 Web Terminal 加载。")


def find_terminal_input(page, timeout_seconds: int = 30, log_func=print):
    """
    遍历所有 iframe，查找 Terminal input。
    """

    log_func("开始查找 Web Terminal 输入框...")

    for _ in range(timeout_seconds):
        for frame in page.frames:
            try:
                terminal_input = frame.get_by_role("textbox", name="Terminal input")

                if terminal_input.count() > 0:
                    terminal_input.first.wait_for(state="attached", timeout=1000)
                    log_func("已找到 Terminal input。")
                    return terminal_input.first

            except Exception:
                pass

        page.wait_for_timeout(1000)

    raise RuntimeError("没有找到 Web Terminal 输入框，请确认已经进入压测机终端页面。")


def paste_commands_to_terminal(page, commands: list[str], log_func=print):
    """
    逐行输入命令。
    """

    log_func("准备逐行输入命令到 Web Terminal...")

    terminal_input = find_terminal_input(page, log_func=log_func)

    terminal_input.click(force=True)
    page.wait_for_timeout(500)

    for index, cmd in enumerate(commands, start=1):
        log_func(f"正在输入第 {index} 条命令：{cmd}")

        terminal_input.click(force=True)
        page.keyboard.insert_text(cmd)
        page.keyboard.press("Enter")

        if cmd.startswith("bash "):
            page.wait_for_timeout(1000)
        else:
            page.wait_for_timeout(200)

    log_func("所有命令已逐行输入完成，请在网页终端观察执行结果。")


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

    try:
        page = session.start()

        log_func("登录成功，开始自动连接压测机。")

        connect_press_server(page, log_func=log_func)

        log_func("等待 Web Terminal 加载完成...")

        paste_commands_to_terminal(page, commands, log_func=log_func)

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

        input("压测结束后按 Enter 关闭浏览器...")

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
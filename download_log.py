# -*- coding: utf-8 -*-

from pathlib import Path

from jumpserver_login import JumpServerSession
from jumpserver_file_manager import (
    open_file_manager,
    enter_file_manager_target_dir,
    find_file_item,
    find_file_items_by_keywords,
)

import jumpserver_config as config


def build_log_filenames(
    local_jmx_path: str,
    run_debug: bool,
    run_formal: bool,
    run_smoke: bool,
    total_quota: int,
    smoke_used_count: int,
    debug_threads: int,
    debug_loops: int,
    formal_threads: int,
    count_debug_used: bool,
) -> list[str]:
    """
    保留旧函数，兼容旧逻辑。

    注意：
    新版下载逻辑已经不再依赖这个函数精准匹配文件名。
    因为 formal 的 loops 可能和代码推算值不一致。
    """

    if not local_jmx_path:
        raise ValueError("请先选择 JMX 脚本文件，否则无法推算日志名。")

    jmx_path = Path(local_jmx_path)
    jmx_stem = jmx_path.stem

    log_files = []

    actual_smoke_used_count = smoke_used_count if run_smoke else 0

    if run_debug and count_debug_used:
        debug_used_count = debug_threads * debug_loops
    else:
        debug_used_count = 0

    remaining_count = total_quota - actual_smoke_used_count - debug_used_count

    if remaining_count < 0:
        raise ValueError(
            f"额度不足：总额度={total_quota}，"
            f"单条测试={actual_smoke_used_count}，"
            f"debug={debug_used_count}，已经超出额度。"
        )

    formal_loops = remaining_count // formal_threads

    if run_debug:
        debug_jtl = f"{jmx_stem}_debug_{debug_threads}_{debug_loops}_log.jtl"
        log_files.append(debug_jtl)

    if run_formal and formal_loops > 0:
        formal_jtl = f"{jmx_stem}_formal_{formal_threads}_{formal_loops}_log.jtl"
        log_files.append(formal_jtl)

    return log_files


def build_log_match_keywords(
    local_jmx_path: str,
    run_debug: bool,
    run_formal: bool,
) -> list[str]:
    """
    生成模糊匹配日志关键字。

    例如 JMX 文件名：
        ng_cbr_001_credit_ng.jmx

    则生成：
        ng_cbr_001_credit_ng_debug
        ng_cbr_001_credit_ng_formal

    这样可以匹配：
        ng_cbr_001_credit_ng_debug_5_10_log.jtl
        ng_cbr_001_credit_ng_formal_5_99_log.jtl
        ng_cbr_001_credit_ng_formal_5_110_log.jtl
        ng_cbr_001_credit_ng_formal_tail_1_4_log.jtl
    """

    if not local_jmx_path:
        raise ValueError("请先选择 JMX 脚本文件，否则无法推算日志匹配关键字。")

    jmx_path = Path(local_jmx_path)
    jmx_stem = jmx_path.stem

    keywords = []

    if run_debug:
        keywords.append(f"{jmx_stem}_debug")

    if run_formal:
        keywords.append(f"{jmx_stem}_formal")

    return keywords


def click_download_menu(file_page, log_func=print):
    """
    点击右键菜单里的“下载”。
    """

    candidates = [
        file_page.get_by_text("下载", exact=True).last,
        file_page.get_by_text("下载", exact=False).last,
        file_page.locator("text=/下载/").last,
    ]

    last_error = None

    for locator in candidates:
        try:
            locator.wait_for(state="visible", timeout=10000)
            locator.click(timeout=10000)
            return
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"右键后没有找到“下载”菜单项。最后错误：{last_error}")


def download_one_log(file_page, remote_filename: str, local_download_dir: str, log_func=print):
    """
    下载单个日志文件到指定目录。

    remote_filename 是页面上实际扫描到的文件名，
    不是代码推算出来的 formal_5_xx_log.jtl。
    """

    local_dir = Path(local_download_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    local_file_path = local_dir / remote_filename

    log_func("=" * 80)
    log_func(f"准备下载：{remote_filename}")
    log_func(f"保存路径：{local_file_path}")

    if local_file_path.exists():
        log_func(f"本地已存在同名文件，将覆盖：{local_file_path}")
        local_file_path.unlink()

    file_item = find_file_item(
        file_page=file_page,
        remote_filename=remote_filename,
        log_func=log_func,
    )

    file_item.scroll_into_view_if_needed(timeout=5000)

    # 先左键点一下，确保文件被选中
    try:
        file_item.click(timeout=5000)
        file_page.wait_for_timeout(300)
    except Exception:
        pass

    log_func("右键目标文件...")
    file_item.click(button="right", timeout=10000)
    file_page.wait_for_timeout(500)

    log_func("点击下载菜单...")
    with file_page.expect_download(timeout=120000) as download_info:
        click_download_menu(file_page=file_page, log_func=log_func)

    download = download_info.value
    download.save_as(str(local_file_path))

    log_func(f"下载完成：{local_file_path}")

    # 防止连续下载时右键菜单或页面状态没恢复
    file_page.wait_for_timeout(1000)

    return str(local_file_path)


def download_logs_from_jumpserver(
    username: str,
    password: str,
    local_jmx_path: str,
    local_download_dir: str,
    run_smoke: bool,
    run_debug: bool,
    run_formal: bool,
    total_quota: int,
    smoke_used_count: int,
    debug_threads: int,
    debug_loops: int,
    formal_threads: int,
    count_debug_used: bool,
    log_func=print,
):
    """
    登录 JumpServer，进入文件管理，下载 debug / formal 日志。

    新版逻辑：
    1. 不再按完整日志名精准下载
    2. 只根据 JMX 文件名生成关键字
    3. 页面上只要包含 xxx_debug 或 xxx_formal 的 .jtl 都下载
    """

    if not local_download_dir:
        raise ValueError("请先选择日志下载目录。")

    match_keywords = build_log_match_keywords(
        local_jmx_path=local_jmx_path,
        run_debug=run_debug,
        run_formal=run_formal,
    )

    if not match_keywords:
        log_func("当前配置下没有需要下载的日志。")
        return

    log_func("本次将按以下关键字模糊匹配日志：")
    for keyword in match_keywords:
        log_func(f"- {keyword}")

    session = JumpServerSession(
        username=username,
        password=password,
        user_data_dir="jumpserver_download_browser_profile",
    )

    try:
        page = session.start()

        log_func("登录成功，开始进入文件管理。")

        file_page = open_file_manager(page, log_func=log_func)

        enter_file_manager_target_dir(file_page, log_func=log_func)

        matched_filenames = find_file_items_by_keywords(
            file_page=file_page,
            keywords=match_keywords,
            log_func=log_func,
        )

        if not matched_filenames:
            log_func("没有匹配到需要下载的日志。")
            return

        log_func("本次匹配到的待下载 JTL 文件：")
        for filename in matched_filenames:
            log_func(f"- {filename}")

        downloaded_files = []

        for filename in matched_filenames:
            downloaded_file = download_one_log(
                file_page=file_page,
                remote_filename=filename,
                local_download_dir=local_download_dir,
                log_func=log_func,
            )
            downloaded_files.append(downloaded_file)

        log_func("=" * 80)
        log_func("全部日志下载完成。")
        log_func("本次下载文件：")
        for file_path in downloaded_files:
            log_func(f"- {file_path}")

        return downloaded_files

    finally:
        session.close()


if __name__ == "__main__":
    download_logs_from_jumpserver(
        username="",
        password="",
        local_jmx_path=r"",
        local_download_dir=config.LOCAL_LOG_DOWNLOAD_DIR,
        run_smoke=True,
        run_debug=True,
        run_formal=True,
        total_quota=config.TOTAL_QUOTA,
        smoke_used_count=config.SMOKE_USED_COUNT,
        debug_threads=config.DEBUG_THREADS,
        debug_loops=config.DEBUG_LOOPS,
        formal_threads=config.FORMAL_THREADS,
        count_debug_used=config.COUNT_DEBUG_USED,
    )
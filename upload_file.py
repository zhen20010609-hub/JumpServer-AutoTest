# -*- coding: utf-8 -*-

from pathlib import Path

from jumpserver_login import JumpServerSession
from jumpserver_file_manager import (
    open_file_manager,
    enter_file_manager_target_dir,
    open_upload_dialog,
    set_files_in_upload_dialog,
)


def upload_files(
    username: str,
    password: str,
    jmx_path: str,
    sample_paths: list[str] | None = None,
    log_func=print,
):
    """
    上传 JMX 脚本和样本 TXT/CSV 文件。

    按你录制的新流程：
    1. 打开文件管理
    2. 进入 Home/Default/prod/QA/qa-press
    3. 右键已有文件/目录
    4. 点击 上传文件
    5. 在上传弹窗里设置本地文件
    """

    sample_paths = sample_paths or []

    if not jmx_path:
        raise ValueError("请先选择 JMX 脚本文件。")

    jmx_file = Path(jmx_path)

    if not jmx_file.exists():
        raise FileNotFoundError(f"JMX 文件不存在：{jmx_path}")

    upload_paths = [str(jmx_file)]

    for path in sample_paths:
        sample_file = Path(path)
        if not sample_file.exists():
            raise FileNotFoundError(f"样本文件不存在：{path}")
        upload_paths.append(str(sample_file))

    log_func("本次准备上传文件：")
    for path in upload_paths:
        log_func(f"- {path}")

    session = JumpServerSession(
        username=username,
        password=password,
        user_data_dir="jumpserver_upload_browser_profile",
    )

    try:
        page = session.start()
        log_func("登录成功，开始进入文件管理。")

        file_page = open_file_manager(page, log_func=log_func)
        enter_file_manager_target_dir(file_page, log_func=log_func)

        open_upload_dialog(
            file_page=file_page,
            log_func=log_func,
        )

        set_files_in_upload_dialog(
            file_page=file_page,
            upload_paths=upload_paths,
            log_func=log_func,
        )

        log_func("文件已提交上传，等待上传完成...")
        file_page.wait_for_timeout(10000)

        log_func("上传流程完成。")

    finally:
        session.close()


if __name__ == "__main__":
    upload_files(
        username="",
        password="",
        jmx_path=r"",
        sample_paths=[],
    )
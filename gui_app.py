# -*- coding: utf-8 -*-

import os
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import jumpserver_config as config

from account_store import AccountStore
from debug_usage_store import DebugUsageStore
from upload_file import upload_files
from execute_command import execute_jmeter
from download_log import download_logs_from_jumpserver

from jtl_parser import parse_jtl_file
from report_generator import generate_report_from_template


def safe_filename(name: str) -> str:
    """
    清理 Windows 文件名中不允许出现的字符。
    """
    if name is None:
        return ""

    name = str(name)

    invalid_chars = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']

    for char in invalid_chars:
        name = name.replace(char, "_")

    return name.strip()


def guess_report_name_from_jtl(jtl_path: str) -> str:
    """
    根据 JTL 文件名推测报告名称。

    例如：
    ng_cbr_001_credit_ng_debug_5_10_log.jtl
    -> ng_cbr_001_credit_ng

    ng_cbr_001_credit_ng_formal_5_110_log.jtl
    -> ng_cbr_001_credit_ng

    ng_cbr_001_credit_ng_formal_tail_1_4_log.jtl
    -> ng_cbr_001_credit_ng
    """

    stem = Path(jtl_path).stem

    if "_formal_tail_" in stem:
        return stem.split("_formal_tail_")[0]

    if "_formal_" in stem:
        return stem.split("_formal_")[0]

    if "_debug_" in stem:
        return stem.split("_debug_")[0]

    if stem.endswith("_log"):
        stem = stem[:-4]

    return stem


def build_test_plan_name(jtl_path: str, index: int) -> str:
    """
    生成报告表格第一列“测试计划”的名称。

    根据文件名自动判断：
    - formal_tail -> 正式补充
    - formal -> 正式
    - debug -> 调试
    - 其他 -> 文件1 / 文件2
    """

    filename = Path(jtl_path).name.lower()

    if "_formal_tail_" in filename:
        return "正式补充"

    if "_formal_" in filename:
        return "正式"

    if "_debug_" in filename:
        return "调试"

    if index == 1:
        return "正式"

    return f"文件{index}"


class JumpServerJMeterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("JumpServer + JMeter 自动化工具")
        self.root.geometry("950x830")

        # 本地账号存储
        self.account_store = AccountStore()

        # debug 计数存储
        self.debug_usage_store = DebugUsageStore()

        # 样本文件路径列表
        self.sample_paths = []

        # 登录信息
        self.selected_account_var = tk.StringVar()
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()

        # 文件选择
        self.jmx_path_var = tk.StringVar()
        self.sample_display_var = tk.StringVar()

        # 执行类型
        self.run_smoke_var = tk.BooleanVar(value=True)
        self.run_debug_var = tk.BooleanVar(value=True)
        self.run_formal_var = tk.BooleanVar(value=True)

        # debug 是否计入额度
        self.count_debug_used_var = tk.BooleanVar(value=config.COUNT_DEBUG_USED)

        # 限制目标 TPS：为空时不修改 JMX 原本的 Constant Throughput Timer
        self.throughput_limit_tps_var = tk.StringVar(value="")

        # 压测参数
        self.total_quota_var = tk.StringVar(value=str(config.TOTAL_QUOTA))
        self.debug_threads_var = tk.StringVar(value=str(config.DEBUG_THREADS))
        self.debug_loops_var = tk.StringVar(value=str(config.DEBUG_LOOPS))
        self.formal_threads_var = tk.StringVar(value=str(config.FORMAL_THREADS))

        # 当前脚本 debug 累计展示
        self.current_debug_used_var = tk.StringVar(value="当前脚本累计 debug：未选择 JMX")

        # 日志下载目录
        self.download_dir_var = tk.StringVar(value=config.LOCAL_LOG_DOWNLOAD_DIR)

        # 生成测试报告使用的 JTL 文件
        self.report_jtl_paths = []
        self.report_jtl_display_var = tk.StringVar()

        # 执行日志折叠状态：默认关闭
        self.log_visible = False
        self.log_toggle_button = None
        self.log_frame = None
        self.log_text = None

        self.account_combo = None

        self.build_ui()
        self.reload_account_options()

        self.log(f"账号配置文件路径：{self.account_store.get_config_path()}")
        self.log(f"debug 计数文件路径：{self.debug_usage_store.get_store_path()}")

    def build_ui(self):
        padding_x = 12
        padding_y = 6

        # ======================
        # 1. 登录信息
        # ======================
        account_frame = tk.LabelFrame(self.root, text="1. JumpServer 登录信息")
        account_frame.pack(fill="x", padx=padding_x, pady=padding_y)

        tk.Label(account_frame, text="已保存账号：").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )

        self.account_combo = ttk.Combobox(
            account_frame,
            textvariable=self.selected_account_var,
            width=28,
            state="readonly"
        )
        self.account_combo.grid(
            row=0, column=1, sticky="w", padx=8, pady=6
        )
        self.account_combo.bind("<<ComboboxSelected>>", self.on_account_selected)

        tk.Button(
            account_frame,
            text="添加 / 更新账号",
            command=self.on_save_account
        ).grid(
            row=0, column=2, sticky="w", padx=8, pady=6
        )

        tk.Button(
            account_frame,
            text="删除账号",
            command=self.on_delete_account
        ).grid(
            row=0, column=3, sticky="w", padx=8, pady=6
        )

        tk.Label(account_frame, text="账号：").grid(
            row=1, column=0, sticky="w", padx=8, pady=6
        )

        tk.Entry(
            account_frame,
            textvariable=self.username_var,
            width=35
        ).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )

        tk.Label(account_frame, text="密码：").grid(
            row=1, column=2, sticky="w", padx=8, pady=6
        )

        tk.Entry(
            account_frame,
            textvariable=self.password_var,
            width=35,
            show="*"
        ).grid(
            row=1, column=3, sticky="w", padx=8, pady=6
        )

        tk.Label(
            account_frame,
            text="说明：账号密码保存到本机用户目录，不会生成在项目代码目录；但密码仍是明文，请勿共享该文件。",
            fg="gray"
        ).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=8, pady=3
        )

        # ======================
        # 2. 上传脚本和样本
        # ======================
        upload_frame = tk.LabelFrame(self.root, text="2. 选择并上传脚本 / 样本")
        upload_frame.pack(fill="x", padx=padding_x, pady=padding_y)

        tk.Label(upload_frame, text="JMX 脚本：").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )

        tk.Entry(
            upload_frame,
            textvariable=self.jmx_path_var,
            width=90
        ).grid(
            row=0, column=1, sticky="we", padx=8, pady=6
        )

        tk.Button(
            upload_frame,
            text="选择 JMX",
            command=self.choose_jmx
        ).grid(
            row=0, column=2, sticky="e", padx=8, pady=6
        )

        tk.Label(upload_frame, text="样本文件：").grid(
            row=1, column=0, sticky="w", padx=8, pady=6
        )

        tk.Entry(
            upload_frame,
            textvariable=self.sample_display_var,
            width=90
        ).grid(
            row=1, column=1, sticky="we", padx=8, pady=6
        )

        tk.Button(
            upload_frame,
            text="选择样本",
            command=self.choose_samples
        ).grid(
            row=1, column=2, sticky="e", padx=8, pady=6
        )

        tk.Button(
            upload_frame,
            text="上传脚本和样本",
            command=self.on_upload
        ).grid(
            row=2, column=1, sticky="w", padx=8, pady=6
        )

        upload_frame.grid_columnconfigure(1, weight=1)

        # ======================
        # 3. 执行压测
        # ======================
        execute_frame = tk.LabelFrame(self.root, text="3. 执行压测")
        execute_frame.pack(fill="x", padx=padding_x, pady=padding_y)

        tk.Label(execute_frame, text="执行类型：").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )

        tk.Checkbutton(
            execute_frame,
            text="单条测试",
            variable=self.run_smoke_var
        ).grid(
            row=0, column=1, sticky="w", padx=8, pady=6
        )

        tk.Checkbutton(
            execute_frame,
            text="调试压测",
            variable=self.run_debug_var
        ).grid(
            row=0, column=2, sticky="w", padx=8, pady=6
        )

        tk.Checkbutton(
            execute_frame,
            text="正式压测",
            variable=self.run_formal_var
        ).grid(
            row=0, column=3, sticky="w", padx=8, pady=6
        )

        tk.Checkbutton(
            execute_frame,
            text="debug 计入总额度",
            variable=self.count_debug_used_var
        ).grid(
            row=0, column=4, sticky="w", padx=8, pady=6
        )

        tk.Label(
            execute_frame,
            text="说明：单条测试固定消耗 1 条。",
            fg="gray"
        ).grid(
            row=0, column=5, columnspan=3, sticky="w", padx=8, pady=6
        )

        throughput_frame = tk.Frame(execute_frame)
        throughput_frame.grid(
            row=1, column=0, columnspan=8, sticky="w", padx=8, pady=6
        )

        tk.Label(throughput_frame, text="限制目标 TPS：").pack(side="left")

        tk.Entry(
            throughput_frame,
            textvariable=self.throughput_limit_tps_var,
            width=10
        ).pack(side="left", padx=(2, 12))

        tk.Label(
            throughput_frame,
            text="填写则修改常数吞吐量定时器为 限制目标 TPS * 60；留空则不修改 JMX 原本配置。",
            fg="gray"
        ).pack(side="left")

        param_row_frame = tk.Frame(execute_frame)
        param_row_frame.grid(
            row=2, column=0, columnspan=8, sticky="w", padx=8, pady=6
        )

        tk.Label(param_row_frame, text="总额度：").pack(side="left")
        tk.Entry(
            param_row_frame,
            textvariable=self.total_quota_var,
            width=10
        ).pack(side="left", padx=(2, 18))

        tk.Label(param_row_frame, text="debug 线程：").pack(side="left")
        tk.Entry(
            param_row_frame,
            textvariable=self.debug_threads_var,
            width=10
        ).pack(side="left", padx=(2, 18))

        tk.Label(param_row_frame, text="debug 循环：").pack(side="left")
        tk.Entry(
            param_row_frame,
            textvariable=self.debug_loops_var,
            width=10
        ).pack(side="left", padx=(2, 18))

        tk.Label(param_row_frame, text="formal 线程：").pack(side="left")
        tk.Entry(
            param_row_frame,
            textvariable=self.formal_threads_var,
            width=10
        ).pack(side="left", padx=(2, 18))

        tk.Label(
            execute_frame,
            textvariable=self.current_debug_used_var,
            fg="blue"
        ).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=8, pady=6
        )

        tk.Button(
            execute_frame,
            text="执行选中压测",
            command=self.on_execute
        ).grid(
            row=4, column=1, sticky="w", padx=8, pady=8
        )

        tk.Button(
            execute_frame,
            text="重置当前脚本 debug 计数",
            command=self.on_reset_debug_count
        ).grid(
            row=4, column=2, sticky="w", padx=8, pady=8
        )

        # ======================
        # 4. 下载日志
        # ======================
        download_frame = tk.LabelFrame(self.root, text="4. 下载压测日志")
        download_frame.pack(fill="x", padx=padding_x, pady=padding_y)

        tk.Label(download_frame, text="保存目录：").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )

        tk.Entry(
            download_frame,
            textvariable=self.download_dir_var,
            width=90
        ).grid(
            row=0, column=1, sticky="we", padx=8, pady=6
        )

        tk.Button(
            download_frame,
            text="选择目录",
            command=self.choose_download_dir
        ).grid(
            row=0, column=2, sticky="e", padx=8, pady=6
        )

        tk.Button(
            download_frame,
            text="下载日志",
            command=self.on_download
        ).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )

        download_frame.grid_columnconfigure(1, weight=1)

        # ======================
        # 5. 解析生成测试报告
        # ======================
        report_frame = tk.LabelFrame(self.root, text="5. 解析生成测试报告")
        report_frame.pack(fill="x", padx=padding_x, pady=padding_y)

        tk.Label(report_frame, text="JTL 文件：").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )

        tk.Entry(
            report_frame,
            textvariable=self.report_jtl_display_var,
            width=80
        ).grid(
            row=0, column=1, sticky="we", padx=8, pady=6
        )

        tk.Button(
            report_frame,
            text="选择 JTL",
            command=self.choose_report_jtl_files
        ).grid(
            row=0, column=2, sticky="e", padx=4, pady=6
        )

        tk.Button(
            report_frame,
            text="清空 JTL",
            command=self.clear_report_jtl_files
        ).grid(
            row=0, column=3, sticky="e", padx=4, pady=6
        )

        tk.Button(
            report_frame,
            text="解析生成测试报告",
            command=self.on_generate_report
        ).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )

        tk.Label(
            report_frame,
            text="说明：支持多次追加选择 JTL/TXT；报告会生成到第一个 JTL 文件所在目录；报告名称会根据第一个 JTL 文件名前缀自动生成。",
            fg="gray"
        ).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=8, pady=3
        )

        report_frame.grid_columnconfigure(1, weight=1)

        # ======================
        # 6. 执行日志，默认收起
        # ======================
        log_toggle_frame = tk.Frame(self.root)
        log_toggle_frame.pack(fill="x", padx=padding_x, pady=(padding_y, 2))

        self.log_toggle_button = tk.Button(
            log_toggle_frame,
            text="展开执行日志",
            command=self.toggle_log_panel
        )
        self.log_toggle_button.pack(side="left", padx=2, pady=3)

        tk.Label(
            log_toggle_frame,
            text="日志默认收起，点击后展开/收起。",
            fg="gray"
        ).pack(side="left", padx=8)

        self.log_frame = tk.LabelFrame(self.root, text="执行日志")

        self.log_text = ScrolledText(self.log_frame, height=7)
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # 默认不 pack self.log_frame，所以日志框默认关闭

    # ======================
    # 账号管理
    # ======================

    def reload_account_options(self, select_name: str | None = None):
        names = self.account_store.list_account_names()

        if self.account_combo:
            self.account_combo["values"] = names

        if not names:
            self.selected_account_var.set("")
            return

        if select_name and select_name in names:
            self.selected_account_var.set(select_name)
            self.fill_account_to_input(select_name)
            return

        current = self.selected_account_var.get()

        if current in names:
            self.fill_account_to_input(current)
            return

        first_name = names[0]
        self.selected_account_var.set(first_name)
        self.fill_account_to_input(first_name)

    def fill_account_to_input(self, account_name: str):
        account = self.account_store.get_account(account_name)

        if not account:
            return

        self.username_var.set(account.get("username", ""))
        self.password_var.set(account.get("password", ""))

    def on_account_selected(self, event=None):
        account_name = self.selected_account_var.get()

        if not account_name:
            return

        self.fill_account_to_input(account_name)
        self.log(f"已切换账号：{account_name}")

    def on_save_account(self):
        username = self.username_var.get().strip()
        password = self.password_var.get()

        if not username:
            messagebox.showwarning("提示", "请先输入账号。")
            return

        if not password:
            messagebox.showwarning("提示", "请先输入密码。")
            return

        account_name = username

        try:
            self.account_store.upsert_account(
                name=account_name,
                username=username,
                password=password,
            )
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return

        self.reload_account_options(select_name=account_name)
        self.log(f"账号已保存：{account_name}")
        self.log(f"账号配置文件路径：{self.account_store.get_config_path()}")
        messagebox.showinfo("保存成功", f"账号已保存：{account_name}")

    def on_delete_account(self):
        account_name = self.selected_account_var.get()

        if not account_name:
            messagebox.showwarning("提示", "当前没有选中的账号。")
            return

        confirm = messagebox.askyesno(
            "确认删除",
            f"确定要删除账号：{account_name} 吗？"
        )

        if not confirm:
            return

        try:
            self.account_store.delete_account(account_name)
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return

        self.selected_account_var.set("")
        self.username_var.set("")
        self.password_var.set("")

        self.reload_account_options()
        self.log(f"账号已删除：{account_name}")
        messagebox.showinfo("删除成功", f"账号已删除：{account_name}")

    # ======================
    # 文件选择
    # ======================

    def choose_jmx(self):
        path = filedialog.askopenfilename(
            title="选择 JMX 脚本文件",
            filetypes=[
                ("JMeter 脚本", "*.jmx"),
                ("所有文件", "*.*"),
            ],
        )

        if path:
            self.jmx_path_var.set(path)
            self.refresh_current_debug_used_display()

    def choose_samples(self):
        paths = filedialog.askopenfilenames(
            title="选择样本 TXT / CSV 文件，可多选",
            filetypes=[
                ("样本文件", "*.txt *.csv"),
                ("TXT 样本文件", "*.txt"),
                ("CSV 样本文件", "*.csv"),
                ("所有文件", "*.*"),
            ],
        )

        if paths:
            self.sample_paths = list(paths)
            self.sample_display_var.set("; ".join(self.sample_paths))

    def choose_download_dir(self):
        path = filedialog.askdirectory(title="选择日志保存目录")

        if path:
            self.download_dir_var.set(path)

    def choose_report_jtl_files(self):
        """
        选择用于生成测试报告的 JTL / TXT 文件。
        支持多次追加选择。
        """

        initial_dir = self.download_dir_var.get().strip()

        if not initial_dir or not os.path.isdir(initial_dir):
            initial_dir = os.getcwd()

        paths = filedialog.askopenfilenames(
            title="选择一个或多个 JTL / TXT 文件",
            initialdir=initial_dir,
            filetypes=[
                ("JTL / TXT 文件", "*.jtl *.txt"),
                ("JTL 文件", "*.jtl"),
                ("TXT 文件", "*.txt"),
                ("所有文件", "*.*"),
            ],
        )

        if not paths:
            return

        self.append_report_jtl_paths(list(paths), log_message=True)

    def append_report_jtl_paths(self, paths: list[str], log_message: bool = False):
        """
        追加报告生成使用的 JTL 文件路径。
        不覆盖已有文件，且自动去重。
        """

        added_paths = []

        for path in paths:
            if not path:
                continue

            path = str(path).strip()

            if not path:
                continue

            if path not in self.report_jtl_paths:
                self.report_jtl_paths.append(path)
                added_paths.append(path)

        self.refresh_report_jtl_display()

        if log_message:
            if added_paths:
                self.log("已追加用于生成报告的 JTL 文件：")
                for path in added_paths:
                    self.log(f"- {path}")
            else:
                self.log("本次选择的 JTL 文件已存在，未重复添加。")

    def set_report_jtl_paths(self, paths: list[str], log_message: bool = False):
        """
        设置报告生成使用的 JTL 文件路径。
        下载完成后自动回填时使用。
        这里也改成追加，避免覆盖用户手动选择的文件。
        """

        self.append_report_jtl_paths(paths, log_message=log_message)

    def refresh_report_jtl_display(self):
        """
        刷新 JTL 输入框展示。
        """

        if not self.report_jtl_paths:
            self.report_jtl_display_var.set("")
            return

        display_names = [
            f"文件{index}：{Path(path).name}"
            for index, path in enumerate(self.report_jtl_paths, start=1)
        ]

        self.report_jtl_display_var.set("；".join(display_names))

    def clear_report_jtl_files(self):
        """
        清空已选择的 JTL 文件。
        """

        self.report_jtl_paths = []
        self.report_jtl_display_var.set("")
        self.log("已清空报告生成区域选择的 JTL 文件。")

    # ======================
    # debug 计数展示和重置
    # ======================

    def refresh_current_debug_used_display(self):
        jmx_path = self.jmx_path_var.get().strip()

        if not jmx_path:
            self.current_debug_used_var.set("当前脚本累计 debug：未选择 JMX")
            return

        used_count = self.debug_usage_store.get_debug_used_count(jmx_path)
        self.current_debug_used_var.set(f"当前脚本累计 debug：{used_count} 条")

    def on_reset_debug_count(self):
        jmx_path = self.jmx_path_var.get().strip()

        if not jmx_path:
            messagebox.showwarning("提示", "请先选择 JMX 脚本文件。")
            return

        confirm = messagebox.askyesno(
            "确认重置",
            "确定要重置当前脚本的 debug 累计消耗条数吗？"
        )

        if not confirm:
            return

        self.debug_usage_store.reset_debug_used_count(jmx_path)
        self.refresh_current_debug_used_display()

        self.log(f"已重置当前脚本 debug 计数：{jmx_path}")
        messagebox.showinfo("完成", "当前脚本 debug 计数已重置。")

    # ======================
    # 日志输出
    # ======================

    def toggle_log_panel(self):
        """
        展开 / 收起执行日志区域。
        展开和收起时，窗口整体高度跟随变化。
        """

        current_width = self.root.winfo_width()

        if self.log_visible:
            # 收起日志区域
            self.log_frame.pack_forget()
            self.log_visible = False
            self.log_toggle_button.config(text="展开执行日志")

            self.root.update_idletasks()
            new_height = self.root.winfo_reqheight()
            self.root.geometry(f"{current_width}x{new_height}")

        else:
            # 展开日志区域
            self.log_frame.pack(
                fill="x",
                expand=False,
                padx=12,
                pady=6
            )
            self.log_visible = True
            self.log_toggle_button.config(text="收起执行日志")

            self.root.update_idletasks()
            new_height = self.root.winfo_reqheight()
            self.root.geometry(f"{current_width}x{new_height}")

    def log(self, message: str):
        """
        输出日志。
        即使日志框当前是收起状态，也会先写进去，展开后可以看到。
        """
        self.root.after(0, self._append_log, message)

    def _append_log(self, message: str):
        """
        真正往日志框里追加内容。
        """
        if self.log_text is None:
            return

        self.log_text.insert("end", str(message) + "\n")
        self.log_text.see("end")

    # ======================
    # 参数读取和校验
    # ======================

    def get_login_info(self):
        username = self.username_var.get().strip()
        password = self.password_var.get()

        if not username:
            raise ValueError("请先输入 JumpServer 账号。")

        if not password:
            raise ValueError("请先输入 JumpServer 密码。")

        return username, password

    def get_common_values(self):
        jmx_path = self.jmx_path_var.get().strip()

        if not jmx_path:
            raise ValueError("请先选择 JMX 脚本文件。")

        return {
            "jmx_path": jmx_path,
            "sample_paths": self.sample_paths,
            "run_smoke": self.run_smoke_var.get(),
            "run_debug": self.run_debug_var.get(),
            "run_formal": self.run_formal_var.get(),
            "throughput_limit_tps": self.parse_optional_int(
                self.throughput_limit_tps_var.get(),
                "限制目标 TPS"
            ),
            "total_quota": self.parse_int(self.total_quota_var.get(), "总额度"),
            "smoke_used_count": 1,
            "debug_threads": self.parse_int(self.debug_threads_var.get(), "debug 线程"),
            "debug_loops": self.parse_int(self.debug_loops_var.get(), "debug 循环"),
            "formal_threads": self.parse_int(self.formal_threads_var.get(), "formal 线程"),
            "count_debug_used": self.count_debug_used_var.get(),
        }

    def parse_int(self, value: str, field_name: str) -> int:
        try:
            result = int(value)
        except ValueError:
            raise ValueError(f"{field_name} 必须是整数。")

        if result < 0:
            raise ValueError(f"{field_name} 不能小于 0。")

        return result

    def parse_optional_int(self, value: str, field_name: str) -> int | None:
        value = value.strip()

        if value == "":
            return None

        try:
            result = int(value)
        except ValueError:
            raise ValueError(f"{field_name} 必须是整数，或者留空。")

        if result <= 0:
            raise ValueError(f"{field_name} 如果填写，必须大于 0。")

        return result

    # ======================
    # 线程执行，避免界面卡死
    # ======================

    def run_async(self, task_func):
        def worker():
            try:
                task_func()
            except Exception as e:
                error_message = str(e)
                error_trace = traceback.format_exc()

                self.log("发生异常：")
                self.log(error_message)
                self.log(error_trace)

                self.root.after(
                    0,
                    lambda msg=error_message: messagebox.showerror("执行失败", msg)
                )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    # ======================
    # 按钮事件：上传
    # ======================

    def on_upload(self):
        try:
            username, password = self.get_login_info()
            values = self.get_common_values()
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return

        def task():
            self.log("=" * 80)
            self.log("开始上传脚本和样本...")

            upload_files(
                username=username,
                password=password,
                jmx_path=values["jmx_path"],
                sample_paths=values["sample_paths"],
                log_func=self.log,
            )

            self.log("上传完成。")

        self.run_async(task)

    # ======================
    # 按钮事件：执行
    # ======================

    def on_execute(self):
        try:
            username, password = self.get_login_info()
            values = self.get_common_values()
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return

        if (
            not values["run_smoke"]
            and not values["run_debug"]
            and not values["run_formal"]
        ):
            messagebox.showerror("参数错误", "请至少勾选一种执行类型。")
            return

        def task():
            self.log("=" * 80)
            self.log("开始执行压测...")

            execute_jmeter(
                username=username,
                password=password,
                local_jmx_path=values["jmx_path"],
                local_sample_paths=values["sample_paths"],
                run_smoke=values["run_smoke"],
                run_debug=values["run_debug"],
                run_formal=values["run_formal"],
                throughput_limit_tps=values["throughput_limit_tps"],
                total_quota=values["total_quota"],
                smoke_used_count=values["smoke_used_count"],
                debug_threads=values["debug_threads"],
                debug_loops=values["debug_loops"],
                formal_threads=values["formal_threads"],
                count_debug_used=values["count_debug_used"],
                log_func=self.log,
            )

            self.refresh_current_debug_used_display()
            self.log("执行命令已提交。")

        self.run_async(task)

    # ======================
    # 按钮事件：下载日志
    # ======================

    def on_download(self):
        try:
            username, password = self.get_login_info()
            values = self.get_common_values()
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return

        download_dir = self.download_dir_var.get().strip()

        if not download_dir:
            messagebox.showerror("参数错误", "请先选择日志保存目录。")
            return

        if not values["run_debug"] and not values["run_formal"]:
            messagebox.showerror("参数错误", "当前未勾选调试压测或正式压测，没有可下载的日志。")
            return

        def task():
            self.log("=" * 80)
            self.log("开始下载日志...")

            downloaded_files = download_logs_from_jumpserver(
                username=username,
                password=password,
                local_jmx_path=values["jmx_path"],
                local_download_dir=download_dir,
                run_smoke=values["run_smoke"],
                run_debug=values["run_debug"],
                run_formal=values["run_formal"],
                total_quota=values["total_quota"],
                smoke_used_count=values["smoke_used_count"],
                debug_threads=values["debug_threads"],
                debug_loops=values["debug_loops"],
                formal_threads=values["formal_threads"],
                count_debug_used=values["count_debug_used"],
                log_func=self.log,
            )

            self.log("日志下载完成。")

            if downloaded_files:
                valid_downloaded_files = [
                    str(path)
                    for path in downloaded_files
                    if str(path).lower().endswith((".jtl", ".txt"))
                ]

                if valid_downloaded_files:
                    self.root.after(
                        0,
                        lambda paths=valid_downloaded_files: self.append_report_jtl_paths(
                            paths,
                            log_message=True
                        )
                    )
                    self.log("已自动把下载完成的 JTL 文件追加到报告生成区域。")

        self.run_async(task)

    # ======================
    # 按钮事件：解析生成测试报告
    # ======================

    def on_generate_report(self):
        """
        解析选中的 JTL 文件，并在第一个 JTL 文件同目录生成测试报告。
        """

        if not self.report_jtl_paths:
            messagebox.showwarning("提示", "请先选择 JTL 文件。")
            return

        def task():
            self.log("=" * 80)
            self.log("开始解析 JTL 并生成测试报告...")

            report_path = self.generate_report_for_selected_jtls()

            self.log(f"测试报告生成成功：{report_path}")

            self.root.after(
                0,
                lambda path=report_path: messagebox.showinfo(
                    "生成成功",
                    f"测试报告生成成功！\n\n文件路径：\n{path}"
                )
            )

        self.run_async(task)

    def generate_report_for_selected_jtls(self) -> str:
        """
        使用当前 report_jtl_paths 解析并生成报告。
        报告输出到第一个 JTL 文件所在目录。
        """

        valid_paths = []

        for path in self.report_jtl_paths:
            if not path:
                continue

            path = str(path).strip()

            if not path:
                continue

            if not os.path.exists(path):
                raise FileNotFoundError(f"JTL 文件不存在：{path}")

            valid_paths.append(path)

        if not valid_paths:
            raise ValueError("没有有效的 JTL 文件。")

        first_jtl_path = Path(valid_paths[0])
        output_dir = first_jtl_path.parent

        report_base_name = guess_report_name_from_jtl(str(first_jtl_path))

        if not report_base_name:
            report_base_name = "压测"

        safe_name = safe_filename(report_base_name)

        output_path = output_dir / f"{safe_name}接口测试报告.docx"

        self.log("本次用于生成报告的 JTL 文件：")
        for path in valid_paths:
            self.log(f"- {path}")

        self.log(f"报告输出目录：{output_dir}")
        self.log(f"报告文件名：{output_path.name}")

        result_list = []

        for index, path in enumerate(valid_paths, start=1):
            self.log("-" * 80)
            self.log(f"开始解析文件{index}：{path}")

            result = parse_jtl_file(path)

            result["test_plan"] = build_test_plan_name(path, index)
            result["source_file"] = path
            result["source_file_name"] = Path(path).name

            request_count = result.get("request_count", 0)
            avg_response = result.get("avg_response", 0)
            mid_response = result.get("mid_response", 0)
            p90_response = result.get("p90_response", 0)
            p95_response = result.get("p95_response", 0)
            p99_response = result.get("p99_response", 0)
            tps = result.get("tps", 0)
            error_rate = result.get("error_rate", 0)
            target_url = result.get("target_url", "")

            self.log(f"测试计划：{result['test_plan']}")
            self.log(f"请求数量：{request_count}")
            self.log(f"平均响应时间 avg：{avg_response} ms")
            self.log(f"中位数 mid：{mid_response} ms")
            self.log(f"P90：{p90_response} ms")
            self.log(f"P95：{p95_response} ms")
            self.log(f"P99：{p99_response} ms")
            self.log(f"TPS：{tps}")
            self.log(f"异常率：{error_rate}%")
            self.log(f"目标 URL：{target_url}")

            if request_count > 0:
                result_list.append(result)
            else:
                self.log("警告：该文件解析出的请求数量为 0，生成报告时将跳过。")

        if not result_list:
            raise ValueError("所有 JTL 文件解析后的请求数量都是 0，无法生成测试报告。")

        self.log("-" * 80)
        self.log("开始填充 Word 模板...")

        report_result = generate_report_from_template(
            result_data=result_list,
            output_path=output_path,
            third_party_name=report_base_name,
        )

        if not report_result.get("success"):
            raise RuntimeError(report_result.get("message", "测试报告生成失败"))

        return report_result.get("file_path", str(output_path))


def main():
    root = tk.Tk()
    JumpServerJMeterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-

import os
import re
from pathlib import Path

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

    # 优先处理 formal_tail
    if "_formal_tail_" in stem:
        return stem.split("_formal_tail_")[0]

    if "_formal_" in stem:
        return stem.split("_formal_")[0]

    if "_debug_" in stem:
        return stem.split("_debug_")[0]

    # 兜底：去掉 _log
    if stem.endswith("_log"):
        stem = stem[:-4]

    return stem


def build_test_plan_name(jtl_path: str, index: int) -> str:
    """
    生成报告表格第一列“测试计划”的名称。

    根据文件名自动判断：
    - formal -> 正式
    - formal_tail -> 正式补充
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


def generate_report_for_jtl_files(
    jtl_file_paths: list[str],
    third_party_name: str = "",
    log_func=print,
):
    """
    解析 JTL 文件，并在第一个 JTL 文件的同目录下生成测试报告。

    参数：
    - jtl_file_paths：用户选择的一个或多个 JTL/TXT 文件
    - third_party_name：模板里 XX 替换成的名称；为空时自动从 JTL 文件名推断
    - log_func：用于输出日志，GUI 里可以传 self.log
    """

    if not jtl_file_paths:
        raise ValueError("请先选择 JTL 文件。")

    valid_paths = []

    for path in jtl_file_paths:
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

    if not third_party_name:
        third_party_name = guess_report_name_from_jtl(str(first_jtl_path))

    third_party_name = str(third_party_name).strip()

    if not third_party_name:
        third_party_name = "压测"

    safe_name = safe_filename(third_party_name)

    output_path = output_dir / f"{safe_name}接口测试报告.docx"

    log_func("开始解析 JTL 文件...")

    result_list = []

    for index, path in enumerate(valid_paths, start=1):
        log_func(f"解析文件{index}：{path}")

        result = parse_jtl_file(path)

        result["test_plan"] = build_test_plan_name(path, index)
        result["source_file"] = path
        result["source_file_name"] = Path(path).name

        request_count = result.get("request_count", 0)
        avg_response = result.get("avg_response", 0)
        tps = result.get("tps", 0)
        error_rate = result.get("error_rate", 0)

        log_func(
            f"解析结果：条数={request_count}，"
            f"avg={avg_response}ms，"
            f"tps={tps}，"
            f"异常率={error_rate}%"
        )

        if request_count > 0:
            result_list.append(result)
        else:
            log_func(f"警告：文件请求数为 0，生成报告时会跳过：{path}")

    if not result_list:
        raise ValueError("所有 JTL 文件解析后请求数量都是 0，无法生成报告。")

    log_func("开始生成 Word 测试报告...")

    report_result = generate_report_from_template(
        result_data=result_list,
        output_path=output_path,
        third_party_name=third_party_name,
    )

    if not report_result.get("success"):
        raise RuntimeError(report_result.get("message", "测试报告生成失败"))

    report_path = report_result.get("file_path", str(output_path))

    log_func(f"测试报告生成成功：{report_path}")

    return report_path
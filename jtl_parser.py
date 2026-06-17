# -*- coding: utf-8 -*-

import re
import csv
import sys
import math
import html
import json
from collections import Counter

def set_csv_field_size_limit():
    """
    放大 CSV 单字段读取上限。
    JMeter 的 JTL 如果保存了 requestData / responseData，
    单个字段可能非常大，Python csv 默认 131072 会报错。
    """
    max_size = sys.maxsize

    while True:
        try:
            csv.field_size_limit(max_size)
            return
        except OverflowError:
            max_size = max_size // 10

def percentile_nearest(values, percent):
    """
    百分位计算：nearest-rank 方法
    例如 P90 = 90% 请求耗时不超过该值。
    """
    if not values:
        return 0

    sorted_values = sorted(values)
    index = math.ceil(len(sorted_values) * percent / 100) - 1
    index = max(0, min(index, len(sorted_values) - 1))

    return sorted_values[index]


def detect_encoding(file_path):
    """
    尝试识别文件编码。
    """
    encodings = ["utf-8-sig", "utf-8", "gbk"]

    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                f.read(4096)
            return encoding
        except UnicodeDecodeError:
            continue

    return "utf-8"


def is_token_request(request_name):
    """
    判断请求名称中是否包含 token。
    大小写不敏感。
    """
    if request_name is None:
        return False

    return "token" in str(request_name).lower()


def get_xml_attr(sample: str, attr_name: str):
    """
    从 <httpSample ...> 标签中提取属性值。

    支持：
    t="240"
    t=&quot;240&quot;
    """
    pattern = rf'\b{attr_name}=(?:"([^"]*)"|&quot;([^&]*)&quot;)'
    match = re.search(pattern, sample)

    if not match:
        return None

    return html.unescape(match.group(1) or match.group(2))


def extract_xml_tag_text(block_text: str, tag_name: str):
    """
    提取 XML 子节点内容，例如：
    <java.net.URL>https://xxx</java.net.URL>
    <queryString class="java.lang.String">...</queryString>
    <responseData class="java.lang.String">...</responseData>
    """
    tag = re.escape(tag_name)

    pattern = (
        rf'(?:<{tag}(?:\s[^>]*)?>|&lt;{tag}(?:\s[^&]*)?&gt;)'
        rf'(.*?)'
        rf'(?:</{tag}>|&lt;/{tag}&gt;)'
    )

    match = re.search(pattern, block_text, flags=re.S)

    if not match:
        return ""

    return html.unescape(match.group(1).strip())


def format_body_text(body_text):
    """
    request body / response body 格式化。
    XML 里的 &quot; 会还原为 "。
    如果内容是 JSON，则格式化成缩进 JSON。
    """
    if body_text is None:
        return ""

    body_text = html.unescape(str(body_text).strip())

    if not body_text:
        return ""

    try:
        parsed = json.loads(body_text)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except Exception:
        return body_text


def get_most_common_url(url_counter):
    """
    多个目标 URL 时，取出现次数最多的那个。
    """
    if not url_counter:
        return ""

    return url_counter.most_common(1)[0][0]


def get_header_index(header, possible_names):
    """
    从 CSV 表头里找字段下标。
    """
    for name in possible_names:
        if name in header:
            return header.index(name)

    return None


def parse_csv_jtl(file_path, encoding):
    """
    解析 CSV 格式 JTL。
    会过滤 label 中包含 token 的请求。
    如果 CSV 里有 URL / requestData / responseData 字段，也会尝试提取。
    """

    set_csv_field_size_limit()

    elapsed_list = []
    start_times = []
    end_times = []

    success_count = 0
    fail_count = 0
    status_code_count = Counter()
    url_counter = Counter()

    skipped_token_count = 0

    sample_request_body = ""
    sample_response_body = ""

    with open(file_path, "r", encoding=encoding, newline="") as f:
        reader = csv.reader(f)

        header = None

        for row in reader:
            if not row:
                continue

            if "elapsed" in row and "responseCode" in row:
                header = row
                break

        if header is None:
            return None

        elapsed_index = header.index("elapsed")
        timestamp_index = header.index("timeStamp") if "timeStamp" in header else None
        success_index = header.index("success") if "success" in header else None
        response_code_index = header.index("responseCode") if "responseCode" in header else None
        label_index = header.index("label") if "label" in header else None

        url_index = get_header_index(header, ["URL", "url", "Url"])
        request_body_index = get_header_index(
            header,
            ["requestData", "requestBody", "request_body", "queryString", "body"]
        )
        response_body_index = get_header_index(
            header,
            ["responseData", "responseBody", "response_body"]
        )

        for cols in reader:
            if not cols:
                continue

            if len(cols) <= elapsed_index:
                continue

            request_name = ""

            if label_index is not None and len(cols) > label_index:
                request_name = cols[label_index].strip()

            if is_token_request(request_name):
                skipped_token_count += 1
                continue

            elapsed = cols[elapsed_index].strip()

            if not elapsed.isdigit():
                continue

            elapsed_ms = int(elapsed)
            elapsed_list.append(elapsed_ms)

            if url_index is not None and len(cols) > url_index:
                url = cols[url_index].strip()
                if url:
                    url_counter[url] += 1

            if timestamp_index is not None and len(cols) > timestamp_index:
                ts = cols[timestamp_index].strip()

                if ts.isdigit():
                    start_ts = int(ts)
                    start_times.append(start_ts)
                    end_times.append(start_ts + elapsed_ms)

            success = None
            if success_index is not None and len(cols) > success_index:
                success = cols[success_index].strip().lower()

            response_code = None
            if response_code_index is not None and len(cols) > response_code_index:
                response_code = cols[response_code_index].strip()

                if response_code:
                    status_code_count[response_code] += 1

            # 保存一条非 token 目标请求的入参和响应样例
            if not sample_request_body and request_body_index is not None and len(cols) > request_body_index:
                sample_request_body = format_body_text(cols[request_body_index])

            if not sample_response_body and response_body_index is not None and len(cols) > response_body_index:
                sample_response_body = format_body_text(cols[response_body_index])

            if success in ["true", "false"]:
                if success == "true":
                    success_count += 1
                else:
                    fail_count += 1
            else:
                if response_code and (response_code.startswith("2") or response_code.startswith("3")):
                    success_count += 1
                else:
                    fail_count += 1

    return {
        "elapsed_list": elapsed_list,
        "start_times": start_times,
        "end_times": end_times,
        "success_count": success_count,
        "fail_count": fail_count,
        "status_code_count": dict(status_code_count),
        "url_counter": url_counter,
        "file_type": "CSV JTL",
        "skipped_token_count": skipped_token_count,
        "sample_request_body": sample_request_body,
        "sample_response_body": sample_response_body
    }


def handle_xml_sample_block(
    block_text,
    elapsed_list,
    start_times,
    end_times,
    status_code_count,
    url_counter,
    sample_holder
):
    """
    处理一个完整的 <httpSample>...</httpSample> 块。

    返回：
    - "TOKEN"：表示被过滤的 token 请求
    - "VALID_TRUE"：有效成功请求
    - "VALID_FALSE"：有效失败请求
    - "INVALID"：无效请求块

    sample_holder 用于保存一条目标接口的 request_body / response_body。
    """
    start_tag_match = re.search(
        r'(?:<|&lt;)(?:httpSample|sample)\b.*?(?:>|&gt;)',
        block_text,
        flags=re.S
    )

    if not start_tag_match:
        return "INVALID"

    start_tag = start_tag_match.group(0)

    request_name = get_xml_attr(start_tag, "lb")

    if is_token_request(request_name):
        return "TOKEN"

    t = get_xml_attr(start_tag, "t")
    ts = get_xml_attr(start_tag, "ts")
    success = get_xml_attr(start_tag, "s")
    response_code = get_xml_attr(start_tag, "rc")

    if not t or not t.isdigit():
        return "INVALID"

    elapsed_ms = int(t)
    elapsed_list.append(elapsed_ms)

    if ts and ts.isdigit():
        start_ts = int(ts)
        start_times.append(start_ts)
        end_times.append(start_ts + elapsed_ms)

    if response_code:
        status_code_count[response_code] += 1

    url = extract_xml_tag_text(block_text, "java.net.URL")
    if url:
        url_counter[url] += 1

    # 保存一条目标接口的 request body / response body
    # 默认取第一条有效目标请求，不在界面展示，只用于生成报告附录。
    if not sample_holder.get("request_body") and not sample_holder.get("response_body"):
        request_body = extract_xml_tag_text(block_text, "queryString")
        response_body = extract_xml_tag_text(block_text, "responseData")

        sample_holder["request_body"] = format_body_text(request_body)
        sample_holder["response_body"] = format_body_text(response_body)

    if success in ["true", "false"]:
        if success == "true":
            return "VALID_TRUE"
        else:
            return "VALID_FALSE"

    if response_code and (response_code.startswith("2") or response_code.startswith("3")):
        return "VALID_TRUE"

    return "VALID_FALSE"


def parse_xml_jtl(file_path, encoding):
    """
    解析 XML 格式 JTL，或 XML 原样转成 txt 后的文件。
    会过滤 lb 中包含 token 的请求。
    会解析 <java.net.URL>...</java.net.URL> 作为目标接口 URL。
    会保存一条目标请求的 queryString 和 responseData 作为报告附录样例。
    """
    elapsed_list = []
    start_times = []
    end_times = []

    success_count = 0
    fail_count = 0
    status_code_count = Counter()
    url_counter = Counter()

    skipped_token_count = 0

    sample_holder = {
        "request_body": "",
        "response_body": ""
    }

    start_pattern = re.compile(r'(?:<|&lt;)(?:httpSample|sample)\b')
    end_pattern = re.compile(r'(?:</|&lt;/)(?:httpSample|sample)(?:>|&gt;)')

    inside_sample = False
    block_lines = []

    with open(file_path, "r", encoding=encoding, errors="ignore") as f:
        for line in f:
            if not inside_sample:
                if start_pattern.search(line):
                    inside_sample = True
                    block_lines = [line]

                    if end_pattern.search(line):
                        block_text = "".join(block_lines)
                        result = handle_xml_sample_block(
                            block_text,
                            elapsed_list,
                            start_times,
                            end_times,
                            status_code_count,
                            url_counter,
                            sample_holder
                        )

                        if result == "TOKEN":
                            skipped_token_count += 1
                        elif result == "VALID_TRUE":
                            success_count += 1
                        elif result == "VALID_FALSE":
                            fail_count += 1

                        inside_sample = False
                        block_lines = []
            else:
                block_lines.append(line)

                if end_pattern.search(line):
                    block_text = "".join(block_lines)
                    result = handle_xml_sample_block(
                        block_text,
                        elapsed_list,
                        start_times,
                        end_times,
                        status_code_count,
                        url_counter,
                        sample_holder
                    )

                    if result == "TOKEN":
                        skipped_token_count += 1
                    elif result == "VALID_TRUE":
                        success_count += 1
                    elif result == "VALID_FALSE":
                        fail_count += 1

                    inside_sample = False
                    block_lines = []

    # 如果文件片段不完整，最后一个块没有闭合，也尽量处理一次
    if inside_sample and block_lines:
        block_text = "".join(block_lines)
        result = handle_xml_sample_block(
            block_text,
            elapsed_list,
            start_times,
            end_times,
            status_code_count,
            url_counter,
            sample_holder
        )

        if result == "TOKEN":
            skipped_token_count += 1
        elif result == "VALID_TRUE":
            success_count += 1
        elif result == "VALID_FALSE":
            fail_count += 1

    return {
        "elapsed_list": elapsed_list,
        "start_times": start_times,
        "end_times": end_times,
        "success_count": success_count,
        "fail_count": fail_count,
        "status_code_count": dict(status_code_count),
        "url_counter": url_counter,
        "file_type": "XML JTL / TXT",
        "skipped_token_count": skipped_token_count,
        "sample_request_body": sample_holder.get("request_body", ""),
        "sample_response_body": sample_holder.get("response_body", "")
    }


def build_result(parsed_data):
    """
    根据解析出的数据计算最终性能指标。
    """
    elapsed_list = parsed_data["elapsed_list"]
    start_times = parsed_data["start_times"]
    end_times = parsed_data["end_times"]
    success_count = parsed_data["success_count"]
    fail_count = parsed_data["fail_count"]
    status_code_count = parsed_data["status_code_count"]
    skipped_token_count = parsed_data.get("skipped_token_count", 0)
    url_counter = parsed_data.get("url_counter", Counter())

    request_count = len(elapsed_list)

    if request_count > 0:
        avg_response = int(round(sum(elapsed_list) / request_count))
        mid_response = int(round(percentile_nearest(elapsed_list, 50)))
        p90_response = int(round(percentile_nearest(elapsed_list, 90)))
        p95_response = int(round(percentile_nearest(elapsed_list, 95)))
        p99_response = int(round(percentile_nearest(elapsed_list, 99)))
        min_response = int(round(min(elapsed_list)))
        max_response = int(round(max(elapsed_list)))
    else:
        avg_response = 0
        mid_response = 0
        p90_response = 0
        p95_response = 0
        p99_response = 0
        min_response = 0
        max_response = 0

    if request_count > 0:
        error_rate = round(fail_count / request_count * 100, 2)
    else:
        error_rate = 0.00

    if start_times and end_times and max(end_times) > min(start_times):
        duration_seconds = (max(end_times) - min(start_times)) / 1000
        tps = round(request_count / duration_seconds, 1)
    else:
        duration_seconds = 0
        tps = 0.0

    target_url = get_most_common_url(url_counter)

    return {
        "file_type": parsed_data.get("file_type", ""),
        "request_count": request_count,
        "avg_response": avg_response,
        "mid_response": mid_response,
        "p90_response": p90_response,
        "p95_response": p95_response,
        "p99_response": p99_response,
        "min_response": min_response,
        "max_response": max_response,
        "error_rate": error_rate,
        "tps": tps,
        "success_count": success_count,
        "fail_count": fail_count,
        "duration_seconds": round(duration_seconds, 3),
        "status_code_count": status_code_count,
        "skipped_token_count": skipped_token_count,

        # 目标接口 URL
        "target_url": target_url,
        "target_url_count": dict(url_counter),

        # 报告附录使用：查得入参 + 响应样例
        "sample_request_body": parsed_data.get("sample_request_body", ""),
        "sample_response_body": parsed_data.get("sample_response_body", "")
    }


def parse_jtl_file(file_path):
    """
    外部只需要调用这个函数。
    自动判断是 CSV JTL 还是 XML JTL。

    注意：
    会自动过滤请求名称中包含 token 的请求。
    会解析目标接口 URL。
    会保存一条目标请求的 request body / response body。
    """
    encoding = detect_encoding(file_path)

    csv_result = parse_csv_jtl(file_path, encoding)

    if csv_result is not None and len(csv_result["elapsed_list"]) > 0:
        return build_result(csv_result)

    xml_result = parse_xml_jtl(file_path, encoding)

    if xml_result is not None and len(xml_result["elapsed_list"]) > 0:
        return build_result(xml_result)

    return {
        "file_type": "未知格式",
        "request_count": 0,
        "avg_response": 0,
        "mid_response": 0,
        "p90_response": 0,
        "p95_response": 0,
        "p99_response": 0,
        "min_response": 0,
        "max_response": 0,
        "error_rate": 0.00,
        "tps": 0.0,
        "success_count": 0,
        "fail_count": 0,
        "duration_seconds": 0,
        "status_code_count": {},
        "skipped_token_count": 0,
        "target_url": "",
        "target_url_count": {},
        "sample_request_body": "",
        "sample_response_body": ""
    }
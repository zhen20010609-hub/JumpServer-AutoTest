# -*- coding: utf-8 -*-

import json
import os
import hashlib
from pathlib import Path
from datetime import datetime


def get_default_debug_usage_path() -> Path:
    """
    获取 debug 使用记录默认保存路径。

    Windows 默认：
    C:\\Users\\用户名\\AppData\\Local\\JumpServerJMeterTool\\jmeter_debug_usage.json

    如果不是 Windows 或取不到 LOCALAPPDATA，则使用：
    用户主目录\\.jumpserver_jmeter_tool\\jmeter_debug_usage.json
    """

    local_appdata = os.environ.get("LOCALAPPDATA")

    if local_appdata:
        base_dir = Path(local_appdata) / "JumpServerJMeterTool"
    else:
        base_dir = Path.home() / ".jumpserver_jmeter_tool"

    return base_dir / "jmeter_debug_usage.json"


class DebugUsageStore:
    """
    按 JMX 脚本维度记录 debug 已消耗条数。

    文件格式示例：
    {
        "records": {
            "脚本key": {
                "jmx_path": "D:/xxx/ng_pay_001.jmx",
                "jmx_name": "ng_pay_001.jmx",
                "debug_used_count": 200,
                "updated_at": "2026-06-02 15:20:00"
            }
        }
    }
    """

    def __init__(self, store_path: str | None = None):
        if store_path:
            self.store_path = Path(store_path)
        else:
            self.store_path = get_default_debug_usage_path()

    def make_script_key(self, local_jmx_path: str) -> str:
        """
        根据 JMX 文件的绝对路径生成唯一 key。

        同一个 JMX 路径会累计 debug 条数。
        换一个 JMX 路径后，会使用新的累计记录，互不影响。
        """

        path_text = str(Path(local_jmx_path).resolve()).lower()
        return hashlib.sha256(path_text.encode("utf-8")).hexdigest()[:16]

    def load_data(self) -> dict:
        if not self.store_path.exists():
            return {"records": {}}

        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            return {"records": {}}

        if not isinstance(data, dict):
            return {"records": {}}

        records = data.get("records", {})

        if not isinstance(records, dict):
            records = {}

        return {"records": records}

    def save_data(self, data: dict):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def get_debug_used_count(self, local_jmx_path: str) -> int:
        """
        获取当前 JMX 脚本已经累计消耗的 debug 条数。
        """

        data = self.load_data()
        key = self.make_script_key(local_jmx_path)

        record = data.get("records", {}).get(key)

        if not record:
            return 0

        try:
            return int(record.get("debug_used_count", 0))
        except Exception:
            return 0

    def add_debug_used_count(self, local_jmx_path: str, add_count: int) -> int:
        """
        给当前 JMX 脚本累计增加 debug 消耗条数。
        返回增加后的累计值。
        """

        if add_count <= 0:
            return self.get_debug_used_count(local_jmx_path)

        data = self.load_data()
        records = data.get("records", {})

        key = self.make_script_key(local_jmx_path)
        jmx_path = Path(local_jmx_path)

        old_record = records.get(key, {})

        try:
            old_count = int(old_record.get("debug_used_count", 0))
        except Exception:
            old_count = 0

        new_count = old_count + add_count

        records[key] = {
            "jmx_path": str(jmx_path.resolve()),
            "jmx_name": jmx_path.name,
            "debug_used_count": new_count,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        data["records"] = records
        self.save_data(data)

        return new_count

    def reset_debug_used_count(self, local_jmx_path: str):
        """
        重置当前 JMX 脚本的 debug 累计条数。
        """

        data = self.load_data()
        records = data.get("records", {})

        key = self.make_script_key(local_jmx_path)

        if key in records:
            del records[key]

        data["records"] = records
        self.save_data(data)

    def get_store_path(self) -> str:
        """
        返回 debug 记录文件路径。
        """

        return str(self.store_path)
# -*- coding: utf-8 -*-

import json
import os
from pathlib import Path


def get_default_account_config_path() -> Path:
    """
    获取账号配置文件默认保存路径。

    Windows 默认：
    C:\\Users\\用户名\\AppData\\Local\\JumpServerJMeterTool\\jumpserver_accounts.json

    如果不是 Windows 或取不到 LOCALAPPDATA，则使用：
    用户主目录\\.jumpserver_jmeter_tool\\jumpserver_accounts.json
    """

    local_appdata = os.environ.get("LOCALAPPDATA")

    if local_appdata:
        base_dir = Path(local_appdata) / "JumpServerJMeterTool"
    else:
        base_dir = Path.home() / ".jumpserver_jmeter_tool"

    return base_dir / "jumpserver_accounts.json"


class AccountStore:
    """
    本地账号配置管理。

    默认不会生成在项目目录下，而是生成在本机用户目录：
    %LOCALAPPDATA%\\JumpServerJMeterTool\\jumpserver_accounts.json

    文件格式：
    {
        "accounts": [
            {
                "name": "账号名",
                "username": "登录账号",
                "password": "登录密码"
            }
        ]
    }

    注意：
    这里是明文保存密码，只适合本机自用工具。
    """

    def __init__(self, config_path: str | None = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = get_default_account_config_path()

    def load_data(self) -> dict:
        """
        读取本地账号配置。
        如果文件不存在，返回空账号列表。
        """

        if not self.config_path.exists():
            return {"accounts": []}

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            return {"accounts": []}

        if not isinstance(data, dict):
            return {"accounts": []}

        accounts = data.get("accounts", [])

        if not isinstance(accounts, list):
            accounts = []

        return {"accounts": accounts}

    def save_data(self, data: dict):
        """
        保存账号配置到本地 JSON 文件。
        """

        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def list_account_names(self) -> list[str]:
        """
        获取所有已保存账号名。
        """

        data = self.load_data()
        accounts = data.get("accounts", [])

        names = []

        for account in accounts:
            name = account.get("name")
            if name:
                names.append(name)

        return names

    def get_account(self, name: str) -> dict | None:
        """
        根据账号名获取账号信息。
        """

        data = self.load_data()

        for account in data.get("accounts", []):
            if account.get("name") == name:
                return account

        return None

    def upsert_account(self, name: str, username: str, password: str):
        """
        新增或更新账号。
        如果 name 已存在，则覆盖 username/password。
        """

        if not name:
            raise ValueError("账号名不能为空。")

        if not username:
            raise ValueError("登录账号不能为空。")

        if not password:
            raise ValueError("登录密码不能为空。")

        data = self.load_data()
        accounts = data.get("accounts", [])

        for account in accounts:
            if account.get("name") == name:
                account["username"] = username
                account["password"] = password
                self.save_data(data)
                return

        accounts.append(
            {
                "name": name,
                "username": username,
                "password": password,
            }
        )

        data["accounts"] = accounts
        self.save_data(data)

    def delete_account(self, name: str):
        """
        删除指定账号。
        """

        data = self.load_data()
        accounts = data.get("accounts", [])

        new_accounts = [
            account
            for account in accounts
            if account.get("name") != name
        ]

        data["accounts"] = new_accounts
        self.save_data(data)

    def get_config_path(self) -> str:
        """
        返回当前账号配置文件路径。
        """

        return str(self.config_path)
# -*- coding: utf-8 -*-

"""
JumpServer + JMeter 自动化配置文件。

注意：
1. JumpServer 账号密码不写在这里，改为从界面输入或从本地账号配置读取。
2. 本文件只放环境、路径、压测参数默认值。
"""

# ======================
# JumpServer 地址
# ======================

JUMP_LOGIN_URL = "https://jps.dyna.tech/core/auth/login/?next=/luna/"
JUMP_LUNA_URL = "https://jps.dyna.tech/luna/"


# ======================
# 浏览器配置
# ======================

BROWSER_HEADLESS = False
BROWSER_SLOW_MO = 0


# ======================
# JumpServer 资产路径配置
# ======================

ASSET_ENV_TITLE = "prod"
ASSET_GROUP_TITLE = "QA"
ASSET_HOST_TITLE = "10.0.11.247"

# 文件管理中的路径
FILE_MANAGER_TITLES = [
    "Home/Default",
    "Home/Default/prod",
    "Home/Default/prod/QA",
    "Home/Default/prod/QA/qa-press",
]


# ======================
# 远程服务器路径
# ======================

# JMeter bin 目录
JMETER_BIN_DIR = "/opt/apache-jmeter-5.6.3/bin"

# JMX / TXT 最终工作目录
REMOTE_WORK_DIR = "/opt/apache-jmeter-5.6.3/bin/zz_polly"

# JumpServer 上传后文件默认所在目录，同时也是日志最终移动目录
REMOTE_UPLOAD_DIR = "/tmp"


# ======================
# 压测参数默认值
# 这些值会作为界面的默认值展示，也可以在界面里改
# ======================

TARGET_TPS = 5

TOTAL_QUOTA = 1000
SMOKE_USED_COUNT = 1

DEBUG_THREADS = 5
DEBUG_LOOPS = 20

FORMAL_THREADS = 5

# debug 是否计入总额度
COUNT_DEBUG_USED = True


# ======================
# 本地日志默认下载目录
# 界面里也可以重新选择
# ======================

LOCAL_LOG_DOWNLOAD_DIR = r"D:\Polly_Python_Project\压测日志"
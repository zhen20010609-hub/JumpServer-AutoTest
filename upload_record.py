import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://jps.dyna.tech/core/auth/login/?next=/luna/")
    page.get_by_role("textbox", name="用户名 *").click()
    page.get_by_role("textbox", name="用户名 *").fill("zhen.zhang")
    page.get_by_role("textbox", name="密码").click()
    page.get_by_role("textbox", name="密码").press("CapsLock")
    page.get_by_role("textbox", name="密码").fill("ZHAN")
    page.get_by_role("textbox", name="密码").press("CapsLock")
    page.get_by_role("textbox", name="密码").fill("ZHANg20010609.")
    page.get_by_role("button", name="登录").click()
    page.goto("https://jps.dyna.tech/luna/")
    page.get_by_title("prod (4)").click()
    page.get_by_title("QA (1)").click()
    page.get_by_title("10.0.11.247").click()
    page.get_by_role("button", name="连接").click()
    page.get_by_text("文件管理").click()
    page.once("dialog", lambda dialog: dialog.dismiss())
    with page.expect_popup() as page1_info:
        page.get_by_text("连接", exact=True).click()
    page1 = page1_info.value
    page1.get_by_title("Home/Default").click()
    page1.get_by_title("Home/Default/prod").click()
    page1.get_by_title("Home/Default/prod/QA").click()
    page1.get_by_title("Home/Default/prod/QA/qa-press").click()
    page1.get_by_text("hsperfdata_wwwsystemd-private").nth(1).click()
    page1.get_by_text("hsperfdata_wwwsystemd-private").nth(1).click(button="right")
    page1.get_by_text("上传文件", exact=True).click()
    page1.get_by_role("button", name="Choose File").first.click()
    page1.close()
    page.close()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)

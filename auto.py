#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动回车脚本 - 每隔10秒按下回车键
可以跨窗口运行，即使用户在其他应用程序中也能工作

使用方法:
1. 运行脚本
2. 按 Ctrl+C 停止
"""

import pyautogui
import time

def main():
    print("=" * 50)
    print("自动回车脚本已启动")
    print("每隔 10 秒将自动按下回车键")
    print("按 Ctrl+C 停止脚本")
    print("=" * 50)

    # 禁用pyautogui的安全功能（防止鼠标移动到屏幕角落时抛出异常）
    pyautogui.FAILSAFE = True

    try:
        while True:
            # 按下并释放回车键
            pyautogui.press('return')
            print(f"[{time.strftime('%H:%M:%S')}] 已按下回车键")

            # 等待10秒
            time.sleep(10)

    except KeyboardInterrupt:
        print("\n" + "=" * 50)
        print("脚本已停止")
        print("=" * 50)

if __name__ == "__main__":
    main()
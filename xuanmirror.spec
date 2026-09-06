# -*- mode: python ; coding: utf-8 -*-
"""玄鉴 XuanMirror 打包配置（PyInstaller）。

用法：
    cd F:/agi/xuanmirror
    pyinstaller --clean --noconfirm xuanmirror.spec

产物：dist/玄鉴XuanMirror.exe（单文件）
数据文件（.env / data/）运行期放 exe 同目录。
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# 前端构建产物 → 打包进 exe，运行期从 _MEIPASS/dist 读取
# Haar 人脸级联：本仓库内置副本（cv2 headless 构建不带 data 目录）
datas = [
    ("frontend/dist", "dist"),
    ("app/core/face/assets", "app/core/face/assets"),
    # 公众人物回测静态产物（模型页回测页签只读展示）
    ("docs/回测数据-公众人物.json", "docs"),
]

hiddenimports = ["python_multipart"]

# uvicorn 动态导入 worker 协议 / 日志
hiddenimports += collect_submodules("uvicorn")

# 术数引擎（适配器内动态 import）
hiddenimports += collect_submodules("iztro_py")
hiddenimports += collect_submodules("lunar_python")

# 序列化 / ORM 动态导入
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("sqlmodel")

# MediaPipe（面相 FaceMesh / 掌纹 Hands 真测量）：子模块 + 内置模型 + 原生库
hiddenimports += collect_submodules("mediapipe")
# mediapipe 1.x tasks 链路间接 import matplotlib（缺失即 ModuleNotFoundError）
hiddenimports += collect_submodules("matplotlib")
hiddenimports += ["matplotlib.pyplot", "matplotlib.backends.backend_agg"]
datas += collect_data_files("matplotlib")
datas += collect_data_files("mediapipe")
binaries_extra = collect_dynamic_libs("mediapipe")

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries_extra,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "ruff"],  # matplotlib 勿排除：mediapipe tasks 链路需要
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="玄鉴XuanMirror",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 保留控制台窗口：可看启动日志、关窗即退出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

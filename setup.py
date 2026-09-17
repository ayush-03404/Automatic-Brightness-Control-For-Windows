import sys
from cx_Freeze import setup, Executable

# 1. Define the dependencies and files to bundle
# 1. Define the dependencies and files to bundle
build_exe_options = {
    "packages": ["screen_brightness_control", "cv2", "pystray", "PIL", "tkinter", "collections", "queue", "threading"],
    "include_files": ["logo.ico"],
    "excludes": ["unittest", "test", "email", "http", "html", "xmlrpc"],
    
    # NEW: Pack all Python libraries into a single ZIP archive to eliminate thousands of tiny files
    "zip_include_packages": ["*"],
    "zip_exclude_packages": [],
    
    # NEW: Strip out unused Python docstrings and debug data to shrink the file size
    "optimize": 2
}

# 2. Hide the black terminal console on Windows
base = "Win32GUI" if sys.platform == "win32" else None

# 3. Configure the Windows Shortcuts
executables = [
    Executable(
        "auto_brightness_app.py",
        base=base,
        target_name="AutoBrightness.exe",
        icon="logo.ico",
        shortcut_name="AutoBrightness Engine",
        shortcut_dir="ProgramMenuFolder"
    )
]

# 4. Configure the MSI Installer specifics
bdist_msi_options = {
    "add_to_path": True,
    "initial_target_dir": r"[ProgramFilesFolder]\AutoBrightness",
}

setup(
    name="AutoBrightness Engine",
    version="1.0",
    description="Advanced Dual Display Auto-Brightness Sync",
    author="Your Name",
    options={
        "build_exe": build_exe_options,
        "bdist_msi": bdist_msi_options
    },
    executables=executables
)
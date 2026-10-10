#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CakBro 2.10.4 - Safe Exam Browser (Python cross-platform port)
Portable & Lightweight Kiosk Browser - Auto: Edge / Chrome / Brave / Firefox

Fitur:
  - Kiosk fullscreen tanpa UI browser (prioritas: Edge > Chrome > Brave > Firefox)
  - Blokir 60+ shortcut berbahaya
  - Blokir translate popup & notifikasi
  - Sembunyikan taskbar (Windows)
  - Tutup aplikasi terlarang otomatis
  - Auto-restart browser bila ditutup paksa
  - Home confirmation: tutup tab, hapus profil/cookie kiosk
  - Expiry: overlay semi-transparan; 5 detik tanpa klik -> Home otomatis
  - GitHub update check + SHA-256 + helper handoff + log
  - Keluar: CTRL+ALT+SHIFT+Q
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox

# ---------- optional deps ----------
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import keyboard as kb_lib
    HAS_KB = True
except Exception:
    HAS_KB = False

try:
    from pynput import mouse as pyn_mouse
    HAS_PYNPUT_MOUSE = True
except Exception:
    HAS_PYNPUT_MOUSE = False


# ============================================================
# CONFIG (standalone - hardcoded)
# ============================================================
APP_NAME             = "CakBro"
APP_VERSION          = "2.10.4"
EXAM_URL             = "https://ujikom.pakkar.my.id/2026/10/try-out-tka-sby.html"
UPDATE_MANIFEST_URL  = "https://raw.githubusercontent.com/pakkar1/cakbro-updates/main/latest.ini"
UPDATE_BINARY_URL    = "https://github.com/pakkar1/cakbro-updates/releases/latest/download/CakBro.exe"
UPDATE_RELEASE_BASE  = "https://github.com/pakkar1/cakbro-updates/releases/download/v"

PLATFORM   = platform.system()  # 'Windows' | 'Linux' | 'Darwin'
IS_WINDOWS = PLATFORM == "Windows"
IS_LINUX   = PLATFORM == "Linux"
IS_MAC     = PLATFORM == "Darwin"

BAR_HEIGHT      = 42
BAR_COLOR       = "#1a1a2e"
BAR_TEXT_COLOR  = "#00d4ff"
ACCENT_COLOR    = "#00d4ff"

BRIDGE_MARKER   = "|CAKBRO_AHK|"

# ============================================================
# APP DIR / DATA DIR
# ============================================================
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def data_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    elif IS_MAC:
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / "CakBro"
    d.mkdir(parents=True, exist_ok=True)
    return d

def log_update(msg: str) -> None:
    try:
        logdir = data_dir() / "Update"
        logdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(logdir / "update.log", "a", encoding="utf-8") as f:
            f.write(f"{stamp} | {msg}\n")
    except Exception:
        pass


# ============================================================
# WINDOW TITLE ENUMERATION (untuk bridge)
# ============================================================
def _enum_titles_windows() -> List[str]:
    titles: List[str] = []
    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        def cb(hwnd, _lparam):
            if not IsWindowVisible(hwnd):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                titles.append(buf.value)
            return True

        EnumWindows(EnumWindowsProc(cb), 0)
    except Exception:
        pass
    return titles

def _enum_titles_linux() -> List[str]:
    for tool, args in (("wmctrl", ["wmctrl", "-l"]), ("xdotool", ["xdotool", "search", "--name", ""])):
        try:
            out = subprocess.check_output(args, timeout=2, text=True, stderr=subprocess.DEVNULL)
            if tool == "wmctrl":
                return [line.split(None, 3)[-1].strip() for line in out.strip().splitlines()]
        except Exception:
            continue
    return []

def enum_window_titles() -> List[str]:
    if IS_WINDOWS:
        return _enum_titles_windows()
    if IS_LINUX:
        return _enum_titles_linux()
    return []  # macOS: stub (butuh PyObjC; dapat ditambah bila perlu)


# ============================================================
# FENSTER control per platform
# ============================================================
def hide_taskbar() -> None:
    if IS_WINDOWS:
        try:
            hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
            hwnd2 = ctypes.windll.user32.FindWindowW("Shell_SecondaryTrayWnd", None)
            if hwnd2:
                ctypes.windll.user32.ShowWindow(hwnd2, 0)
        except Exception:
            pass

def show_taskbar() -> None:
    if IS_WINDOWS:
        try:
            hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            hwnd2 = ctypes.windll.user32.FindWindowW("Shell_SecondaryTrayWnd", None)
            if hwnd2:
                ctypes.windll.user32.ShowWindow(hwnd2, 5)
        except Exception:
            pass


def find_window_by_pid_windows(pid: int) -> Optional[int]:
    result = {"hwnd": None}
    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        def cb(hwnd, _):
            if not IsWindowVisible(hwnd):
                return True
            wpid = ctypes.c_ulong()
            GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                result["hwnd"] = hwnd
                return False
            return True

        EnumWindows(EnumWindowsProc(cb), 0)
    except Exception:
        pass
    return result["hwnd"]


def resize_external_window(pid: int, x: int, y: int, w: int, h: int) -> None:
    if IS_WINDOWS:
        hwnd = find_window_by_pid_windows(pid)
        if hwnd:
            HWND_TOP = 0
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOP, x, y, w, h,
                                              SWP_NOACTIVATE | SWP_SHOWWINDOW)
            return
    if IS_LINUX:
        try:
            subprocess.run(["xdotool", "search", "--pid", str(pid), "windowsize", str(w), str(h),
                            "windowmove", str(x), str(y)],
                           timeout=2, check=False, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def maximize_external_window(pid: int) -> None:
    if IS_WINDOWS:
        hwnd = find_window_by_pid_windows(pid)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE


def kill_process_tree(pid: int) -> None:
    if not HAS_PSUTIL:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        return
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try: child.kill()
            except Exception: pass
        try: parent.kill()
        except Exception: pass
    except Exception:
        pass


def taskkill_names(names: List[str]) -> None:
    if not names:
        return
    if not HAS_PSUTIL:
        if IS_WINDOWS:
            args = ["taskkill", "/F"]
            for n in names:
                args += ["/IM", n]
            args.append("/T")
            subprocess.run(args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    lowered = {n.lower() for n in names}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            nm = (proc.info.get("name") or "").lower()
            if nm in lowered:
                proc.kill()
        except Exception:
            pass


# ============================================================
# BROWSER FINDER (prioritas: Edge > Chrome > Brave > Firefox)
# ============================================================
def _win_reg_app_paths(exe: str) -> Optional[str]:
    if not IS_WINDOWS:
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive, access in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_64KEY),
                         (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_32KEY)):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe,
                                0, access) as k:
                val, _ = winreg.QueryValueEx(k, None)
                val = val.strip('"')
                if Path(val).is_file():
                    return val
        except Exception:
            continue
    return None


def find_browser() -> Tuple[str, str, str, str]:
    """Return (path, type, name, exe). type in {'edge','chromium','firefox'}"""
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", "")) if IS_WINDOWS else home

    candidates = {
        "edge": [
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            str(local / "Microsoft/Edge/Application/msedge.exe"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
            "/usr/bin/microsoft-edge-beta", "/usr/bin/microsoft-edge-dev",
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(local / "Google/Chrome/Application/chrome.exe"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium", "/usr/bin/chromium-browser",
        ],
        "brave": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            str(local / "BraveSoftware/Brave-Browser/Application/brave.exe"),
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/usr/bin/brave-browser", "/usr/bin/brave",
        ],
        "firefox": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            "/Applications/Firefox.app/Contents/MacOS/firefox",
            "/usr/bin/firefox",
        ],
    }
    order = [("edge", "edge", "Microsoft Edge", "msedge.exe"),
             ("chrome", "chromium", "Google Chrome", "chrome.exe"),
             ("brave", "chromium", "Brave", "brave.exe"),
             ("firefox", "firefox", "Mozilla Firefox", "firefox.exe")]

    for key, btype, name, exe in order:
        for p in candidates[key]:
            if p and Path(p).is_file():
                return p, btype, name, exe
        regpath = _win_reg_app_paths(exe)
        if regpath:
            return regpath, btype, name, exe

    # PATH fallback
    for exe in ("msedge.exe", "chrome.exe", "brave.exe", "firefox.exe"):
        try:
            which = shutil.which(exe)
            if which:
                mapping = {"msedge.exe": ("edge", "Microsoft Edge"),
                           "chrome.exe": ("chromium", "Google Chrome"),
                           "brave.exe": ("chromium", "Brave"),
                           "firefox.exe": ("firefox", "Mozilla Firefox")}
                btype, name = mapping[exe]
                return which, btype, name, exe
        except Exception:
            pass

    return "", "", "", ""


# ============================================================
# UPDATE (SHA-256 + manifest)
# ============================================================
def http_get_text(url: str, timeout: int = 15) -> str:
    if HAS_REQUESTS:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": f"CakBroUpdater/{APP_VERSION}"})
        r.raise_for_status()
        return r.text
    req = urllib.request.Request(url, headers={"User-Agent": f"CakBroUpdater/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def http_download(url: str, dest: Path, timeout: int = 90) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if HAS_REQUESTS:
        with requests.get(url, stream=True, timeout=timeout,
                          headers={"User-Agent": f"CakBroUpdater/{APP_VERSION}"}) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return
    req = urllib.request.Request(url, headers={"User-Agent": f"CakBroUpdater/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def is_newer(remote: str, current: str) -> bool:
    try:
        r = [int(x) for x in remote.split(".")]
        c = [int(x) for x in current.split(".")]
        return r > c
    except Exception:
        return False


def check_for_update_and_apply() -> bool:
    """
    Return True bila update akan diterapkan (caller harus ExitApp).
    Return False bila lanjut normal.
    """
    log_update("----- Pemeriksaan update dimulai -----")
    log_update(f"Versi lokal={APP_VERSION}; frozen={getattr(sys, 'frozen', False)}")

    if not getattr(sys, "frozen", False):
        log_update("Lewati update: script berjalan dari interpreter, bukan EXE terkompilasi.")
        return False

    try:
        manifest = http_get_text(UPDATE_MANIFEST_URL)
    except Exception as e:
        log_update(f"Gagal mengambil manifest: {e}")
        return False
    manifest = manifest.lstrip("\ufeff")

    m = re.search(r"(?im)^\s*version\s*=\s*(\d+\.\d+\.\d+)\s*$", manifest)
    if not m:
        log_update("Manifest: baris Version tidak ditemukan.")
        return False
    remote = m.group(1)
    log_update(f"Versi remote={remote}")
    if not is_newer(remote, APP_VERSION):
        log_update("Tidak ada versi lebih baru.")
        return False

    m = re.search(r"(?im)^\s*sha256\s*=\s*([A-Fa-f0-9]{64})\s*$", manifest)
    if not m:
        log_update("Manifest: SHA-256 tidak valid.")
        return False
    expected = m.group(1).lower()

    udir = data_dir() / "Update"
    bdir = data_dir() / "Backup"
    udir.mkdir(parents=True, exist_ok=True)
    bdir.mkdir(parents=True, exist_ok=True)

    staged = udir / f"CakBro-{remote}.download"
    if staged.exists():
        staged.unlink()

    versioned = f"{UPDATE_RELEASE_BASE}{remote}/CakBro.exe"
    log_update(f"Mengunduh aset: {versioned}")
    try:
        http_download(versioned, staged)
    except Exception as e:
        log_update(f"Unduhan URL versi gagal: {e}; coba URL latest.")
        if staged.exists():
            staged.unlink()
        try:
            http_download(UPDATE_BINARY_URL, staged)
        except Exception as e2:
            log_update(f"Unduhan URL latest gagal: {e2}")
            return False

    log_update(f"Unduhan selesai; size={staged.stat().st_size} byte")
    actual = sha256_file(staged)
    if actual != expected:
        log_update(f"SHA-256 mismatch. Manifest={expected} file={actual}")
        staged.unlink(missing_ok=True)
        return False
    log_update("SHA-256 cocok; update terverifikasi.")

    target = Path(sys.executable).resolve()
    backup = bdir / (target.stem + ".previous.exe")

    # tulis helper updater (PowerShell untuk Windows, sh untuk Unix)
    if IS_WINDOWS:
        return _spawn_update_helper_windows(target, staged, backup)
    return _spawn_update_helper_unix(target, staged, backup)


def _spawn_update_helper_windows(target: Path, staged: Path, backup: Path) -> bool:
    udir = data_dir() / "Update"
    ready = udir / f"ApplyCakBroUpdate-{int(time.time()*1000)}.ready"
    helper = udir / "ApplyCakBroUpdate.ps1"
    log_path = udir / "update.log"
    parent_pid = os.getpid()

    ps = rf"""$ErrorActionPreference = 'Stop'
$target = '{target}'
$staged = '{staged}'
$backup = '{backup}'
$ready  = '{ready}'
$log    = '{log_path}'
$parentPid = {parent_pid}
$pending = $target + '.pending.exe'
$backupMade = $false
$parentExited = $false
function Log($m) {{
  try {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' | ' + $m) -Encoding UTF8 }} catch {{}}
}}
try {{
  [IO.File]::WriteAllText($ready, 'ready')
  Log 'Helper started; waiting for parent exit.'
  $deadline = (Get-Date).AddSeconds(90)
  while (Get-Process -Id $parentPid -ErrorAction SilentlyContinue) {{
    if ((Get-Date) -ge $deadline) {{ throw 'Timeout waiting for parent.' }}
    Start-Sleep -Milliseconds 500
  }}
  $parentExited = $true
  Start-Sleep -Milliseconds 500
  Copy-Item -LiteralPath $target -Destination $backup -Force
  $backupMade = $true
  Copy-Item -LiteralPath $staged -Destination $pending -Force
  Move-Item -LiteralPath $pending -Destination $target -Force
  Log 'New EXE installed; starting CakBro.'
  Start-Process -FilePath $target -WorkingDirectory (Split-Path $target -Parent)
  Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
}} catch {{
  Log ('FAILED: ' + $_.Exception.Message)
  if ($backupMade -and (Test-Path $backup)) {{
    try {{ Copy-Item -LiteralPath $backup -Destination $target -Force; Log 'Backup restored.' }} catch {{}}
  }}
  if ($parentExited -and (Test-Path $target)) {{
    try {{ Start-Process -FilePath $target -WorkingDirectory (Split-Path $target -Parent) }} catch {{}}
  }}
}} finally {{
  if (Test-Path $pending) {{ Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue }}
}}
"""
    helper.write_text(ps, encoding="utf-8")
    ready.unlink(missing_ok=True)

    pwsh = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not pwsh.is_file():
        log_update(f"PowerShell tidak ditemukan: {pwsh}")
        return False

    log_update("Meluncurkan PowerShell helper...")
    try:
        subprocess.Popen([str(pwsh), "-NoProfile", "-NonInteractive",
                          "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-File", str(helper)],
                         cwd=str(udir),
                         creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0)
    except Exception as e:
        log_update(f"Gagal menjalankan helper: {e}")
        return False

    # tunggu ready
    for _ in range(60):
        if ready.exists():
            ready.unlink(missing_ok=True)
            log_update("Helper ready; CakBro akan keluar untuk swap EXE.")
            return True
        time.sleep(0.25)
    log_update("Helper tidak siap dalam 15 detik; update dibatalkan.")
    return False


def _spawn_update_helper_unix(target: Path, staged: Path, backup: Path) -> bool:
    udir = data_dir() / "Update"
    ready = udir / f"ApplyCakBroUpdate-{int(time.time()*1000)}.ready"
    helper = udir / "ApplyCakBroUpdate.sh"
    log_path = udir / "update.log"
    parent_pid = os.getpid()

    sh = f"""#!/bin/sh
set -e
TARGET="{target}"
STAGED="{staged}"
BACKUP="{backup}"
READY="{ready}"
LOG="{log_path}"
PID={parent_pid}
PENDING="$TARGET.pending"
log() {{ echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" >> "$LOG"; }}
echo ready > "$READY"
log "Helper started; waiting for parent exit."
i=0
while kill -0 "$PID" 2>/dev/null; do
  i=$((i+1))
  [ $i -gt 180 ] && {{ log "Timeout"; exit 1; }}
  sleep 0.5
done
sleep 0.5
cp -f "$TARGET" "$BACKUP"
cp -f "$STAGED" "$PENDING"
mv -f "$PENDING" "$TARGET"
chmod +x "$TARGET"
log "New binary installed; starting CakBro."
nohup "$TARGET" >/dev/null 2>&1 &
rm -f "$STAGED"
"""
    helper.write_text(sh, encoding="utf-8")
    helper.chmod(0o755)
    ready.unlink(missing_ok=True)

    log_update("Meluncurkan shell helper...")
    try:
        subprocess.Popen(["/bin/sh", str(helper)], cwd=str(udir))
    except Exception as e:
        log_update(f"Gagal menjalankan helper: {e}")
        return False

    for _ in range(60):
        if ready.exists():
            ready.unlink(missing_ok=True)
            log_update("Helper ready; CakBro akan keluar untuk swap binary.")
            return True
        time.sleep(0.25)
    log_update("Helper tidak siap dalam 15 detik; update dibatalkan.")
    return False


# ============================================================
# HOTKEY BLOCKER
# ============================================================
WINDOWS_HOTKEYS = [
    "windows", "left windows", "right windows",
    "windows+d", "windows+e", "windows+r", "windows+l", "windows+tab",
    "windows+m", "windows+shift+m", "windows+b", "windows+i",
    "windows+s", "windows+a", "windows+x", "windows+p", "windows+k",
    "windows+g", "windows+h", "windows+u", "windows+v", "windows+w",
    "windows+q", "windows+.", "windows+;", "windows+c",
    "windows+shift+s",
    "windows+1", "windows+2", "windows+3", "windows+4", "windows+5",
    "windows+6", "windows+7", "windows+8", "windows+9", "windows+0",
    "windows+space",
    "alt+tab", "alt+f4", "alt+escape", "alt+space", "alt+f8",
    "ctrl+escape", "ctrl+shift+escape", "ctrl+alt+delete",
    # browser shortcuts
    "ctrl+t", "ctrl+n", "ctrl+shift+n", "ctrl+w", "ctrl+shift+w",
    "ctrl+l", "ctrl+d", "ctrl+h", "ctrl+j", "ctrl+shift+i", "ctrl+shift+j",
    "ctrl+shift+c", "ctrl+u", "ctrl+p", "ctrl+o", "ctrl+s", "ctrl+g",
    "ctrl+shift+delete", "ctrl+tab", "ctrl+shift+tab", "ctrl+f5",
    "ctrl+shift+t", "ctrl+k", "ctrl+e",
    # function keys
    "f1", "f3", "f6", "f7", "f10", "f11", "f12",
    # print screen
    "print screen", "alt+print screen", "ctrl+print screen",
    # misc
    "apps", "ctrl+alt+a", "ctrl+alt+s",
]


def setup_hotkey_blocker() -> Optional[object]:
    """
    Return listener object (harus disimpan agar tidak di-GC) atau None.
    Windows: pakai `keyboard` (tanpa admin).
    Linux:   pakai `keyboard` (butuh root; akan fail gracefully).
    macOS:   pakai `keyboard` (butuh Accessibility).
    """
    if not HAS_KB:
        log_update("Modul 'keyboard' tidak tersedia; hotkey blocker dinonaktifkan.")
        return None

    try:
        for hk in WINDOWS_HOTKEYS:
            try:
                kb_lib.add_hotkey(hk, lambda: None, suppress=True)
            except Exception:
                pass
        # blokir tombol Windows penuh (kiri/kanan)
        for key in ("windows", "left windows", "right windows"):
            try: kb_lib.block_key(key)
            except Exception: pass

        # cek kombinasi keluar sendiri
        kb_lib.add_hotkey("ctrl+alt+shift+q", _exit_hotkey_pressed, suppress=False)
        log_update("Hotkey blocker aktif.")
        return kb_lib
    except Exception as e:
        log_update(f"Hotkey blocker gagal: {e}")
        return None


def block_context_menu() -> Optional[object]:
    """Blokir klik kanan (mouse). Windows via keyboard lib, lainnya via pynput."""
    if HAS_PYNPUT_MOUSE:
        try:
            def on_click(x, y, button, pressed):
                if pressed and button == pyn_mouse.Button.right:
                    return False  # suppress
            listener = pyn_mouse.Listener(on_click=on_click, suppress=True)
            listener.daemon = True
            listener.start()
            return listener
        except Exception:
            return None
    return None


# Exit hotkey callback - akan di-override oleh CakBroApp
_exit_hotkey_cb = None
def _exit_hotkey_pressed():
    if _exit_hotkey_cb:
        try: _exit_hotkey_cb()
        except Exception: pass


# ============================================================
# MAIN APP
# ============================================================
class CakBroApp:
    def __init__(self):
        # state
        self.browser_path = ""
        self.browser_type = ""
        self.browser_name = ""
        self.browser_exe = ""
        self.browser_proc: Optional[subprocess.Popen] = None
        self.browser_pid = 0

        self.exam_url = EXAM_URL
        self.is_exam_expired = False
        self.is_home_resetting = False
        self.is_exiting = False

        # bridge
        self.bridge_active = False
        self.bridge_base_remaining = 0
        self.bridge_base_token_wait = 0
        self.bridge_token = ""
        self.bridge_sync_tick = 0.0
        self.bridge_last_remaining = -1
        self.bridge_last_sync_tick = 0.0
        self.bridge_last_token = ""

        self.warn5_shown = False
        self.warn1_shown = False

        # ui
        self.root = tk.Tk()
        self.root.withdraw()
        self.bar_win: Optional[tk.Toplevel] = None
        self.warning_win: Optional[tk.Toplevel] = None
        self.expired_win: Optional[tk.Toplevel] = None

        self.status_var = tk.StringVar(value="Menunggu ujian...")
        self.clock_var = tk.StringVar(value="--:--:--")
        self.warning_head_var = tk.StringVar(value="")
        self.warning_body_var = tk.StringVar(value="")
        self.expired_countdown_var = tk.StringVar(value="")

        self.expired_auto_home_at = 0.0
        self.warning_minutes = 0

        # geometry
        self.mon_w = self.root.winfo_screenwidth()
        self.mon_h = self.root.winfo_screenheight()
        self.browser_h = self.mon_h - BAR_HEIGHT

        # hotkey listener (keep reference!)
        self._hotkey_handle = None
        self._mouse_handle = None

        # register exit callback
        global _exit_hotkey_cb
        _exit_hotkey_cb = self.trigger_exit

    # ---------------- lifecycle ----------------
    def run(self):
        # cek update SEBELUM meluncurkan browser
        if check_for_update_and_apply():
            self.cleanup(is_applying_update=True)
            sys.exit(0)

        self.find_browser_step()
        self.show_splash()
        self.kill_existing_browsers()
        hide_taskbar()
        self.launch_browser()
        self.create_bar()
        self.setup_blocking()
        self._schedule_timers()
        self.root.mainloop()

    def _schedule_timers(self):
        self.root.after(500, self.security_check)
        self.root.after(1000, self.update_clock)
        self.root.after(250, self.poll_bridge)
        self.root.after(300, self.keep_focus)

    # ---------------- browser ----------------
    def find_browser_step(self):
        p, t, n, e = find_browser()
        if not p:
            self._topmost_msgbox("error",
                                 "Browser tidak ditemukan!\n\n"
                                 "Install salah satu: Edge / Chrome / Brave / Firefox")
            sys.exit(1)
        self.browser_path, self.browser_type, self.browser_name, self.browser_exe = p, t, n, e
        log_update(f"Browser terdeteksi: {n} -> {p}")

    def kill_existing_browsers(self):
        taskkill_names(["msedge.exe", "chrome.exe", "brave.exe", "firefox.exe",
                        "msedge", "chrome", "brave", "firefox"])
        time.sleep(0.8)

    def _profile_dir(self) -> Path:
        return app_dir() / "CakBroProfile"

    def _write_firefox_user_js(self, data_dir_: Path):
        user_js = data_dir_ / "user.js"
        if user_js.exists():
            return
        content = "\n".join([
            'user_pref("browser.shell.checkDefaultBrowser", false);',
            'user_pref("browser.translations.enable", false);',
            'user_pref("browser.translations.automaticallyPopup", false);',
            'user_pref("browser.sessionstore.resume_from_crash", false);',
            'user_pref("dom.webnotifications.enabled", false);',
            'user_pref("geo.enabled", false);',
            'user_pref("signon.rememberSignons", false);',
            'user_pref("browser.download.promptForDownload", false);',
            'user_pref("app.update.auto", false);',
        ])
        user_js.write_text(content, encoding="utf-8")

    def _write_chromium_prefs(self, profile: Path):
        prefs_dir = profile / "Default"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        prefs_file = prefs_dir / "Preferences"
        if prefs_file.exists():
            return
        data = {
            "translate": {"enabled": False},
            "translate_blocked_languages": ["*"],
            "translate_site_blacklist": ["*"],
            "browser": {"enable_spellchecking": False, "show_home_button": False},
            "autofill": {"enabled": False},
            "profile": {
                "default_content_setting_values": {
                    "notifications": 2, "geolocation": 2,
                    "media_stream_camera": 2, "media_stream_mic": 2,
                },
                "password_manager_enabled": False,
            },
            "search": {"suggest_enabled": False},
            "signin": {"allowed": False},
            "enable_do_not_track": True,
        }
        prefs_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def launch_browser(self):
        if self.browser_type == "firefox":
            self._launch_firefox()
        else:
            self._launch_chromium()

        # tunggu window
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.browser_proc and self.browser_proc.poll() is not None:
                break
            if self._find_browser_hwnd():
                break
            time.sleep(0.25)

        if not self.browser_proc or self.browser_proc.poll() is not None:
            self._topmost_msgbox("error", f"Gagal menjalankan {self.browser_name}!")
            self.cleanup()
            sys.exit(1)

        self.browser_pid = self.browser_proc.pid
        time.sleep(2.0)
        self._setup_browser_window()

    def _launch_firefox(self):
        data_dir_ = self._profile_dir() / "Firefox"
        data_dir_.mkdir(parents=True, exist_ok=True)
        self._write_firefox_user_js(data_dir_)
        cmd = [
            self.browser_path,
            "-kiosk", self.exam_url,
            "-profile", str(data_dir_),
            "-no-remote",
        ]
        log_update(f"Launch Firefox: {' '.join(cmd)}")
        self.browser_proc = subprocess.Popen(cmd)

    def _launch_chromium(self):
        profile = self._profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        self._write_chromium_prefs(profile)

        cmd = [self.browser_path]
        cmd += ["--kiosk", self.exam_url]
        if self.browser_type == "edge":
            cmd += ["--edge-kiosk-type=fullscreen"]
        cmd += ["--app=" + self.exam_url]
        cmd += ["--start-fullscreen"]
        cmd += [f"--window-size={self.mon_w},{self.browser_h}"]
        cmd += ["--window-position=0,0"]
        cmd += [f"--user-data-dir={profile}"]
        cmd += ["--disable-translate", "--disable-features=TranslateUI,Translate",
                "AutofillServerCommunication,OverlayScrollbar"]
        if self.browser_type == "edge":
            cmd += ["--disable-features=" + ",".join([
                "msTranslateCompactMenu", "msEdgeSidebarV2", "msEdgeDiscoverBar",
                "msEdgeMoveToDesktop", "msEdgeShoppingUI", "msEdgeSplitWindow",
                "msEdgeDropUI", "msEdgeMathSolver", "msEdgeCopilot",
                "msEdgeCouponDetector", "msEdgeReadAloud", "msSmartScreenProtection",
                "msEdgeCollections", "msEdgeShare", "msEdgeWebCapture",
                "msEdgeWorkspaces", "msEdgeEnhancedSecurityMode", "msImplicitSignin",
                "msEdgeAutoImport", "msEnableTranslatePageContextMenu",
                "msEdgeHubAppHost", "msEdgeOnRamp", "msCompactTranslate",
            ])]
        cmd += ["--lang=id", "--accept-lang=id"]
        cmd += ["--disable-notifications", "--disable-popup-blocking",
                "--disable-infobars", "--disable-extensions",
                "--disable-component-update", "--disable-background-networking",
                "--disable-sync", "--disable-default-apps",
                "--disable-client-side-phishing-detection",
                "--disable-domain-reliability", "--disable-hang-monitor",
                "--disable-prompt-on-repost", "--disable-session-crashed-bubble",
                "--disable-background-timer-throttling",
                "--disable-offer-store-unmasked-wallet-cards",
                "--disable-offer-upload-credit-cards",
                "--no-first-run", "--no-default-browser-check", "--no-service-autorun",
                "--disable-save-password-bubble", "--disable-single-click-autofill",
                "--password-store=basic", "--disable-breakpad",
                "--metrics-recording-only", "--safebrowsing-disable-auto-update",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-ipc-flooding-protection",
                "--bookmark-bar-ntp=hidden"]
        log_update(f"Launch Chromium: {' '.join(cmd)}")
        self.browser_proc = subprocess.Popen(cmd)

    def _find_browser_hwnd(self) -> Optional[int]:
        if not self.browser_proc:
            return None
        if IS_WINDOWS:
            return find_window_by_pid_windows(self.browser_proc.pid)
        return None

    def _setup_browser_window(self):
        if not self.browser_proc:
            return
        pid = self.browser_proc.pid
        resize_external_window(pid, 0, 0, self.mon_w, self.browser_h)

    # ---------------- splash ----------------
    def show_splash(self):
        sp = tk.Toplevel(self.root)
        sp.overrideredirect(True)
        sp.configure(bg="#0d1117")
        sp.attributes("-topmost", True)
        W, H = 500, 220
        x = (self.mon_w - W) // 2
        y = (self.mon_h - H) // 2
        sp.geometry(f"{W}x{H}+{x}+{y}")

        tk.Frame(sp, bg=ACCENT_COLOR, height=3).pack(fill="x")
        tk.Label(sp, text="CakBro", fg=ACCENT_COLOR, bg="#0d1117",
                 font=("Segoe UI", 28, "bold")).pack(pady=(20, 0))
        tk.Label(sp, text="Safe Exam Browser", fg="#cccccc", bg="#0d1117",
                 font=("Segoe UI", 11)).pack(pady=(6, 0))
        tk.Frame(sp, bg="#333333", height=2, width=200).pack(pady=16)
        tk.Label(sp, text="Mempersiapkan lingkungan ujian yang aman...",
                 fg="#888888", bg="#0d1117", font=("Segoe UI", 9)).pack()
        tk.Label(sp, text=f"v{APP_VERSION} — Powered by Pakkar",
                 fg="#444444", bg="#0d1117", font=("Consolas", 8)).pack(pady=(8, 0))

        self.root.update_idletasks()
        sp.update()
        time.sleep(2.5)
        sp.destroy()

    # ---------------- bottom bar ----------------
    def create_bar(self):
        self.bar_win = tk.Toplevel(self.root)
        self.bar_win.overrideredirect(True)
        self.bar_win.configure(bg=BAR_COLOR)
        self.bar_win.attributes("-topmost", True)
        y = self.mon_h - BAR_HEIGHT
        self.bar_win.geometry(f"{self.mon_w}x{BAR_HEIGHT}+0+{y}")

        # top accent
        tk.Frame(self.bar_win, bg=ACCENT_COLOR, height=2).pack(fill="x", side="top")

        inner = tk.Frame(self.bar_win, bg=BAR_COLOR, height=BAR_HEIGHT - 2)
        inner.pack(fill="both", expand=True)
        inner.pack_propagate(False)

        tk.Label(inner, text=f"CakBro V{APP_VERSION}", fg=BAR_TEXT_COLOR, bg=BAR_COLOR,
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(14, 10))

        tk.Frame(inner, bg="#444444", width=1).pack(side="left", fill="y", pady=8, padx=4)

        tk.Button(inner, text="⟳ Refresh", command=self.refresh_browser,
                  bg="#0f3460", fg="white", activebackground="#1c4a86",
                  activeforeground="white", relief="flat", bd=0,
                  font=("Segoe UI", 10), padx=12, pady=2).pack(side="left", padx=8)

        tk.Frame(inner, bg="#444444", width=1).pack(side="left", fill="y", pady=8, padx=4)

        tk.Label(inner, textvariable=self.status_var, fg="#E2E8F0", bg=BAR_COLOR,
                 font=("Segoe UI", 9, "bold")).pack(side="left", expand=True, padx=10)

        self.home_btn = tk.Button(inner, text="Home", command=self.return_home_from_bar,
                                  bg="#0f3460", fg="white", activebackground="#1c4a86",
                                  activeforeground="white", relief="flat", bd=0,
                                  font=("Segoe UI", 9, "bold"), padx=14, pady=2)
        self.home_btn.pack(side="right", padx=6)
        self.home_btn.pack_forget()  # hidden by default

        tk.Label(inner, text="Pakkar", fg="#4ade80", bg=BAR_COLOR,
                 font=("Segoe UI", 9)).pack(side="right", padx=10)

        tk.Label(inner, textvariable=self.clock_var, fg="#AAAAAA", bg=BAR_COLOR,
                 font=("Consolas", 12)).pack(side="right", padx=14)

    # ---------------- hotkey ----------
    def setup_blocking(self):
        self._hotkey_handle = setup_hotkey_blocker()
        self._mouse_handle = block_context_menu()

    # ---------------- timers ----------------
    def security_check(self):
        if self.is_exiting:
            return
        hide_taskbar()

        # restart browser bila mati (kecuali saat reset home)
        if not self.is_home_resetting and self.browser_proc:
            if self.browser_proc.poll() is not None:
                time.sleep(0.5)
                self.kill_existing_browsers()
                time.sleep(0.3)
                self.launch_browser()

        # kill aplikasi terlarang
        forbidden = ["taskmgr.exe", "cmd.exe", "powershell.exe", "WindowsTerminal.exe",
                     "snippingtool.exe", "ScreenSketch.exe", "ScreenClippingHost.exe",
                     "regedit.exe", "control.exe", "mmc.exe", "osk.exe",
                     "calc.exe", "notepad.exe", "mspaint.exe",
                     "wmplayer.exe", "vlc.exe"]
        taskkill_names(forbidden)

        self.root.after(500, self.security_check)

    def update_clock(self):
        self.clock_var.set(time.strftime("%H:%M:%S"))
        self._update_bar_status()
        self.root.after(1000, self.update_clock)

    def keep_focus(self):
        if self.bar_win and self.bar_win.winfo_exists():
            try:
                self.bar_win.attributes("-topmost", True)
                self.bar_win.lift()
            except Exception:
                pass

        if self.is_exiting or self.is_home_resetting:
            self.root.after(300, self.keep_focus)
            return

        # tetap resize browser ke area yang benar (di atas bar)
        if self.browser_proc and self.browser_proc.poll() is None:
            resize_external_window(self.browser_proc.pid, 0, 0, self.mon_w, self.browser_h)

        # overlay tetap di atas
        if self.expired_win and self.expired_win.winfo_exists():
            try:
                self.expired_win.attributes("-topmost", True)
                self.expired_win.lift()
            except Exception:
                pass
        elif self.warning_win and self.warning_win.winfo_exists():
            try:
                self.warning_win.attributes("-topmost", True)
                self.warning_win.lift()
            except Exception:
                pass

        self.root.after(300, self.keep_focus)

    # ---------------- bridge (window-title polling) ----------------
    def poll_bridge(self):
        if not self.is_home_resetting:
            titles = enum_window_titles()
            for t in titles:
                pos = t.find(BRIDGE_MARKER)
                if pos < 0:
                    continue
                payload = t[pos + len(BRIDGE_MARKER):]
                m = re.match(r"^(ON|OFF)\|(\d+)\|(\d+)\|([A-Za-z0-9_-]*)\|", payload)
                if not m:
                    continue
                state, rem, token_wait, token = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
                if token == "-":
                    token = ""
                if state == "OFF":
                    self.bridge_active = False
                    self.bridge_base_remaining = 0
                    self.bridge_base_token_wait = 0
                    self.bridge_token = ""
                    self.bridge_sync_tick = 0.0
                else:
                    if not self.bridge_active:
                        elapsed = int((time.time() - self.bridge_last_sync_tick)) if self.bridge_last_sync_tick else 0
                        expected = self.bridge_last_remaining - elapsed if self.bridge_last_remaining >= 0 else -1
                        if (self.bridge_last_remaining < 0 or token != self.bridge_last_token
                                or rem > expected + 5):
                            self.warn5_shown = False
                            self.warn1_shown = False
                    self.bridge_active = True
                    self.bridge_base_remaining = rem
                    self.bridge_base_token_wait = token_wait
                    self.bridge_token = token
                    self.bridge_sync_tick = time.time()
                    self.bridge_last_remaining = rem
                    self.bridge_last_sync_tick = self.bridge_sync_tick
                    self.bridge_last_token = token

                    if rem <= 0 and not self.is_exam_expired:
                        self.is_exam_expired = True
                        self.show_expired_overlay()
                        self._update_bar_status()
                break
        self.root.after(250, self.poll_bridge)

    # ---------------- status bar ----------------
    def _update_bar_status(self):
        show_home = False
        if self.is_exam_expired:
            text = "WAKTU HABIS"
        elif not self.bridge_active:
            text = "Menunggu ujian..."
        else:
            elapsed = int(time.time() - self.bridge_sync_tick)
            remaining = max(0, self.bridge_base_remaining - elapsed)
            token_wait = max(0, self.bridge_base_token_wait - elapsed)
            mm, ss = divmod(remaining, 60)
            time_text = f"{mm}:{ss:02d}"
            if remaining == 0:
                text = "WAKTU HABIS"
                if not self.is_exam_expired:
                    self.is_exam_expired = True
                    self.show_expired_overlay()
            elif token_wait > 0:
                tm, ts = divmod(token_wait, 60)
                text = f"Sisa {time_text} | Token {tm}:{ts:02d} lagi"
            elif self.bridge_token:
                text = f"Sisa {time_text} | Token: {self.bridge_token}"
                show_home = True
            else:
                text = f"Sisa {time_text} | Token belum diatur"

            if 0 < remaining <= 60 and not self.warn1_shown:
                self.warn1_shown = True
                self.warning_minutes = 1
                self.show_time_warning()
            elif 60 < remaining <= 300 and not self.warn5_shown:
                self.warn5_shown = True
                self.warning_minutes = 5
                self.show_time_warning()

        self.status_var.set(text)

        # update tombol
        try:
            if self.is_exam_expired or (self.warning_win and self.warning_win.winfo_exists()):
                self.home_btn.pack_forget()
            else:
                if show_home:
                    if not self.home_btn.winfo_ismapped():
                        self.home_btn.pack(side="right", padx=6)
                else:
                    self.home_btn.pack_forget()
        except Exception:
            pass

    # ---------------- warning overlay ----------------
    def show_time_warning(self):
        if self.warning_minutes == 1:
            self.warning_head_var.set("Waktu ujian tersisa 1 menit")
            self.warning_body_var.set("Segera selesaikan dan kirim jawaban sebelum waktu habis.")
        else:
            self.warning_head_var.set("Waktu ujian tersisa 5 menit")
            self.warning_body_var.set("Pastikan jawaban sudah lengkap dan dikirim sebelum waktu habis.")

        if self.warning_win and self.warning_win.winfo_exists():
            return

        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.configure(bg="#101827")
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.86)
        w.geometry(f"{self.mon_w}x{self.browser_h}+0+0")

        tk.Label(w, textvariable=self.warning_head_var, fg="white", bg="#101827",
                 font=("Segoe UI", 22, "bold")).pack(pady=(self.browser_h // 2 - 90, 0))
        tk.Label(w, textvariable=self.warning_body_var, fg="#E2E8F0", bg="#101827",
                 font=("Segoe UI", 12), wraplength=self.mon_w - 80,
                 justify="center").pack(pady=(12, 0))
        tk.Button(w, text="OKE", command=self.acknowledge_warning,
                  bg="#00d4ff", fg="#0d1117", activebackground="#00b0d6",
                  relief="flat", bd=0, font=("Segoe UI", 11, "bold"),
                  padx=30, pady=8).pack(pady=20)
        self.warning_win = w

    def acknowledge_warning(self):
        if self.warning_win and self.warning_win.winfo_exists():
            self.warning_win.destroy()
        self.warning_win = None
        self._update_bar_status()
        if self.browser_proc and self.browser_proc.poll() is None:
            resize_external_window(self.browser_proc.pid, 0, 0, self.mon_w, self.browser_h)

    # ---------------- expired overlay ----------------
    def show_expired_overlay(self):
        if self.expired_win and self.expired_win.winfo_exists():
            return
        if self.warning_win and self.warning_win.winfo_exists():
            self.warning_win.destroy()
            self.warning_win = None

        self.expired_auto_home_at = time.time() + 5

        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.configure(bg="#101827")
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.86)
        w.geometry(f"{self.mon_w}x{self.browser_h}+0+0")

        tk.Label(w, text="Waktu ujian telah berakhir", fg="white", bg="#101827",
                 font=("Segoe UI", 24, "bold")).pack(pady=(self.browser_h // 2 - 150, 0))
        tk.Label(w, text="Akses ke formulir ujian telah dihentikan.",
                 fg="#E2E8F0", bg="#101827", font=("Segoe UI", 12)).pack(pady=(10, 0))
        tk.Label(w,
                 text=("Jika tidak ditekan, browser ditutup dan profil/cookie kiosk dihapus.\n"
                       "Jawaban yang belum dikirim bisa hilang."),
                 fg="#E2E8F0", bg="#101827", font=("Segoe UI", 10),
                 wraplength=self.mon_w - 80, justify="center").pack(pady=(14, 0))
        tk.Label(w, textvariable=self.expired_countdown_var, fg="#FBBF24",
                 bg="#101827", font=("Segoe UI", 11, "bold")).pack(pady=(14, 0))
        tk.Button(w, text="Kembali", command=self.return_home_from_expiry,
                  bg="#00d4ff", fg="#0d1117", activebackground="#00b0d6",
                  relief="flat", bd=0, font=("Segoe UI", 11, "bold"),
                  padx=30, pady=8).pack(pady=14)
        self.expired_win = w

        self._tick_expired_countdown()

    def _tick_expired_countdown(self):
        if not (self.expired_win and self.expired_win.winfo_exists()):
            return
        left = max(0, int(self.expired_auto_home_at - time.time() + 0.999))
        self.expired_countdown_var.set(f"Otomatis kembali ke jadwal dalam {left} detik.")
        if left <= 0 and self.is_exam_expired and not self.is_home_resetting:
            self.execute_home_reset()
            return
        self.root.after(500, self._tick_expired_countdown)

    # ---------------- home reset ----------------
    def return_home_from_bar(self):
        if self.is_home_resetting:
            return
        if not self.is_exam_expired and (not self.bridge_active or not self.bridge_token):
            return
        if not self._confirm_home():
            return
        self.execute_home_reset()

    def return_home_from_expiry(self):
        if not self._confirm_home():
            return
        self.execute_home_reset()

    def _confirm_home(self) -> bool:
        return messagebox.askyesno(
            "CakBro - Kembali ke Home",
            "Jika dilanjutkan semua data akan dihapus dan jawaban yang belum "
            "dikirim akan hilang.\n\nAplikasi kembali ke tampilan awal. Lanjutkan?"
        )

    def execute_home_reset(self):
        if self.is_home_resetting:
            return
        self.is_home_resetting = True

        # reset state
        self.bridge_active = False
        self.bridge_base_remaining = 0
        self.bridge_base_token_wait = 0
        self.bridge_token = ""
        self.bridge_sync_tick = 0.0
        self.is_exam_expired = False
        self.warn5_shown = False
        self.warn1_shown = False
        self.bridge_last_remaining = -1
        self.bridge_last_sync_tick = 0.0
        self.bridge_last_token = ""

        if self.warning_win and self.warning_win.winfo_exists():
            self.warning_win.destroy()
            self.warning_win = None
        if self.expired_win and self.expired_win.winfo_exists():
            self.expired_win.destroy()
            self.expired_win = None
        self._update_bar_status()

        # kill browser
        if self.browser_proc:
            kill_process_tree(self.browser_proc.pid)
            try:
                self.browser_proc.wait(timeout=5)
            except Exception:
                pass
        taskkill_names(["msedge.exe", "chrome.exe", "brave.exe", "firefox.exe",
                        "msedge", "chrome", "brave", "firefox"])

        # hapus profil kiosk
        profile = self._profile_dir()
        if profile.exists():
            ok = False
            for _ in range(3):
                try:
                    shutil.rmtree(profile)
                    ok = True
                    break
                except Exception:
                    time.sleep(0.5)
            if not ok:
                self._topmost_msgbox(
                    "error",
                    "Profil kiosk tidak berhasil dihapus. Browser tidak dibuka ulang "
                    "agar sesi login lama tidak tertinggal."
                )
                self.cleanup()
                sys.exit(1)

        self.browser_proc = None
        self.browser_pid = 0
        time.sleep(0.7)

        # reload dengan nonce
        base = EXAM_URL
        sep = "&" if "?" in base else "?"
        self.exam_url = f"{base}{sep}ahkHomeReset={int(time.time()*1000)}"
        self.launch_browser()
        self.exam_url = base

        self.is_home_resetting = False
        self._update_bar_status()

    # ---------------- refresh ----------------
    def refresh_browser(self):
        if self.is_exam_expired:
            return
        if self.warning_win and self.warning_win.winfo_exists():
            return
        if not self.browser_proc or self.browser_proc.poll() is not None:
            return
        # aktifkan window browser, kirim F5 via keyboard library
        if HAS_KB:
            try:
                kb_lib.send("f5")
            except Exception:
                pass

    # ---------------- exit ----------------
    def trigger_exit(self):
        if self.is_exiting:
            return
        self.is_exiting = True
        try:
            if HAS_KB:
                kb_lib.send("space")
        except Exception:
            pass

        yes = messagebox.askyesno(
            "CakBro - Konfirmasi Keluar",
            "Apakah Anda yakin ingin keluar dari aplikasi?\n\n"
            "Tekan [Yes] untuk keluar dari ujian."
        )
        if yes:
            self.cleanup()
            sys.exit(0)
        self.is_exiting = False

    def cleanup(self, is_applying_update=False):
        try:
            if self._hotkey_handle and HAS_KB:
                try: kb_lib.unhook_all()
                except Exception: pass
        except Exception:
            pass

        if is_applying_update:
            show_taskbar()
            return

        if self.browser_proc:
            kill_process_tree(self.browser_proc.pid)
        taskkill_names(["msedge.exe", "chrome.exe", "brave.exe", "firefox.exe",
                        "msedge", "chrome", "brave", "firefox"])
        show_taskbar()

        try:
            self.root.quit()
        except Exception:
            pass

    # ---------------- helpers ----------------
    def _topmost_msgbox(self, kind: str, msg: str):
        top = tk.Toplevel(self.root)
        top.attributes("-topmost", True)
        top.withdraw()
        if kind == "error":
            messagebox.showerror("CakBro", msg, parent=top)
        else:
            messagebox.showinfo("CakBro", msg, parent=top)
        top.destroy()


# ============================================================
# ENTRY
# ============================================================
def main():
    if not HAS_PSUTIL:
        print("[WARN] psutil belum terinstall. Proses tidak akan di-kill otomatis.")
    if not HAS_REQUESTS:
        print("[WARN] requests belum terinstall. Update check memakai urllib.")
    if not HAS_KB:
        print("[WARN] 'keyboard' belum terinstall. Hotkey blocker nonaktif.")

    # signal handler untuk cleanup
    app = CakBroApp()

    def _sig(_signum, _frame):
        try:
            app.cleanup()
        except Exception:
            pass
        sys.exit(0)
    try:
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

    app.run()


if __name__ == "__main__":
    main()

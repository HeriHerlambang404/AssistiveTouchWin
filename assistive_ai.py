import sys
import os
import glob
import json
import ctypes
import winreg
import subprocess
import requests
import pyautogui

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QTextEdit, QPushButton, QMenu
)
from PyQt5.QtCore import Qt, QPoint, QThread, pyqtSignal, QRect
from PyQt5.QtGui import QColor, QFont, QPainter, QBrush, QPen, QPainterPath

# ==========================================
# KONFIGURASI FOLDER C:\ai-lokal & SERVER AI
# ==========================================
AI_FOLDER = r"C:\ai-lokal"
AI_SERVER_EXE = os.path.join(AI_FOLDER, "llama-server.exe")
AI_API_URL = "http://localhost:8080/v1/chat/completions"
AI_HEALTH_URL = "http://localhost:8080/health"
AI_MODEL = "local-model"

ai_server_process = None

def get_gguf_model_path():
    """Otomatis mencari file model .gguf pertama di dalam C:\ai-lokal"""
    gguf_files = glob.glob(os.path.join(AI_FOLDER, "*.gguf"))
    if gguf_files:
        return gguf_files[0]
    # Cek juga kalau ada di dalam subfolder di C:\ai-lokal
    gguf_sub = glob.glob(os.path.join(AI_FOLDER, "**", "*.gguf"), recursive=True)
    if gguf_sub:
        return gguf_sub[0]
    return None

def ensure_ai_server_running():
    """Mengecek apakah llama-server sudah jalan. Jika belum, nyalakan otomatis di background."""
    global ai_server_process
    try:
        res = requests.get(AI_HEALTH_URL, timeout=1.5)
        if res.status_code == 200:
            return
    except requests.exceptions.RequestException:
        pass

    model_path = get_gguf_model_path()
    if not os.path.exists(AI_SERVER_EXE) or not model_path:
        print(f"Pastikan llama-server.exe dan file .gguf ada di {AI_FOLDER}")
        return

    cmd = [
        AI_SERVER_EXE,
        "-m", model_path,
        "--port", "8080",
        "--ctx-size", "2048",
        "--threads", "4"
    ]

    try:
        CREATE_NO_WINDOW = 0x08000000
        ai_server_process = subprocess.Popen(
            cmd,
            cwd=AI_FOLDER,  # Penting agar llama.dll di C:\ai-lokal terbaca dengan benar!
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"Gagal menjalankan llama-server: {e}")

def stop_ai_server():
    """Mematikan proses llama-server saat keluar dari AssistiveTouch"""
    global ai_server_process
    if ai_server_process and ai_server_process.poll() is None:
        ai_server_process.terminate()


# ==========================================
# WINDOWS ACRYLIC / BLUR EFFECT (CTYPES)
# ==========================================
class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int)
    ]

class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t)
    ]

def apply_windows_blur(hwnd):
    try:
        accent = ACCENTPOLICY()
        accent.AccentState = 3
        accent.GradientColor = 0x401E1E28
        
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        
        ctypes.windll.user32.SetWindowCompositionAttribute(int(hwnd), ctypes.byref(data))
    except Exception:
        pass


# ==========================================
# AUTO-RUN WINDOWS STARTUP REGISTRY
# ==========================================
def set_autostart(app_name="AssistiveTouchAI", enable=True):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
            exe_path = f'"{pythonw_exe}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Auto-run setup error: {e}")


# ==========================================
# WORKER THREAD UNTUK STREAMING AI LOKAL
# ==========================================
class AIStreamWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished_stream = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, prompt):
        super().__init__()
        self.prompt = prompt

    def run(self):
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "Kamu adalah asisten AI desktop yang ringkas, cepat, dan membantu."},
                {"role": "user", "content": self.prompt}
            ],
            "stream": True,
            "temperature": 0.7
        }
        try:
            with requests.post(AI_API_URL, json=payload, stream=True, timeout=30) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line:
                        decoded = line.decode('utf-8')
                        if decoded.startswith("data: "):
                            data_str = decoded[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data_json = json.loads(data_str)
                                delta = data_json["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    self.chunk_received.emit(content)
                            except Exception:
                                pass
            self.finished_stream.emit()
        except Exception as e:
            model_detected = get_gguf_model_path() or "TIDAK DITEMUKAN (.gguf belum ada di C:\\ai-lokal)"
            self.error_occurred.emit(
                f"⏳ Server AI lokal sedang memuat model atau belum siap.\n"
                f"📂 Model terdeteksi: {os.path.basename(model_detected)}\n"
                f"Coba tekan Enter lagi dalam beberapa detik bro!\n({str(e)})"
            )


# ==========================================
# PANEL UTAMA (SEARCH BAR + QUICK ACTIONS)
# ==========================================
class GlassSearchPanel(QWidget):
    def __init__(self, parent_btn):
        super().__init__()
        self.parent_btn = parent_btn
        self.worker = None
        self.init_ui()

    def init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(480, 290)

        apply_windows_blur(self.winId())

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(12)

        # --- BARIS 1: SEARCH BAR AI ---
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("✨ Tanya AI Lokal (C:\\ai-lokal)...")
        self.search_input.setFont(QFont("Poppins", 10))
        self.search_input.returnPressed.connect(self.ask_ai)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.35);
                border-radius: 16px;
                padding: 10px 16px;
                color: #FFFFFF;
            }
            QLineEdit:focus {
                border: 1.5px solid #E6E6FA;
                background-color: rgba(255, 255, 255, 0.20);
            }
        """)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.hide_panel)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 182, 193, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 18px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(255, 182, 193, 0.5);
            }
        """)

        search_layout.addWidget(self.search_input)
        search_layout.addWidget(close_btn)
        main_layout.addLayout(search_layout)

        # --- BARIS 2: KOTAK RESPONS AI ---
        self.response_box = QTextEdit()
        self.response_box.setReadOnly(True)
        self.response_box.setFont(QFont("Poppins", 9))
        self.response_box.setPlaceholderText("Jawaban dari model GGUF di C:\\ai-lokal akan muncul di sini...")
        self.response_box.setStyleSheet("""
            QTextEdit {
                background-color: rgba(20, 20, 30, 0.35);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 14px;
                padding: 10px;
                color: #F4F0FF;
            }
        """)
        main_layout.addWidget(self.response_box)

        # --- BARIS 3: QUICK ACTIONS ---
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(10)

        actions = [
            ("📷 Snip", "#FFDAB9", self.action_screenshot),
            ("🔊 Vol +", "#B2F2BB", self.action_vol_up),
            ("🖥️ Task", "#E6E6FA", self.action_task_view),
            ("🔒 Lock", "#D8BFD8", self.action_lock)
        ]

        for label, pastel_hex, callback in actions:
            btn = QPushButton(label)
            btn.setFont(QFont("Poppins", 9, QFont.Medium))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(callback)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.12);
                    border: 1px solid rgba(255, 255, 255, 0.28);
                    border-radius: 12px;
                    padding: 8px 10px;
                    color: {pastel_hex};
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.25);
                    border: 1px solid {pastel_hex};
                }}
            """)
            actions_layout.addWidget(btn)

        main_layout.addLayout(actions_layout)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRect(2, 2, self.width() - 4, self.height() - 4)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 24, 24)

        painter.fillPath(path, QBrush(QColor(30, 28, 42, 185)))
        pen = QPen(QColor(255, 255, 255, 85), 1.5)
        painter.setPen(pen)
        painter.drawPath(path)

    def ask_ai(self):
        prompt = self.search_input.text().strip()
        if not prompt:
            return
        self.response_box.clear()
        self.response_box.setPlainText("⏳ Sedang memproses di AI lokal...\n")
        
        # Pastikan server nyala kalau sebelumnya sempat mati
        ensure_ai_server_running()
        
        self.worker = AIStreamWorker(prompt)
        self.worker.chunk_received.connect(self.on_chunk)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.start()

    def on_chunk(self, text):
        if self.response_box.toPlainText().startswith("⏳"):
            self.response_box.clear()
        self.response_box.moveCursor(self.response_box.textCursor().End)
        self.response_box.insertPlainText(text)

    def on_error(self, err_msg):
        self.response_box.setPlainText(err_msg)

    def action_screenshot(self):
        self.hide_panel()
        pyautogui.hotkey('win', 'shift', 's')

    def action_vol_up(self):
        pyautogui.press('volumeup', presses=2)

    def action_task_view(self):
        self.hide_panel()
        pyautogui.hotkey('win', 'tab')

    def action_lock(self):
        self.hide_panel()
        ctypes.windll.user32.LockWorkStation()

    def hide_panel(self):
        self.hide()
        self.parent_btn.show()


# ==========================================
# TOMBOL FLOATING ASSISTIVETOUCH
# ==========================================
class AssistiveTouchButton(QWidget):
    def __init__(self):
        super().__init__()
        self.drag_pos = QPoint()
        self.is_dragging = False
        self.panel = GlassSearchPanel(self)
        self.init_ui()

    def init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(64, 64)
        self.setCursor(Qt.PointingHandCursor)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 90, screen.height() // 2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setBrush(QBrush(QColor(40, 38, 55, 160)))
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1.5))
        painter.drawRoundedRect(4, 4, 56, 56, 18, 18)

        painter.setBrush(QBrush(QColor(230, 230, 250, 90)))
        painter.setPen(QPen(QColor(255, 255, 255, 140), 1))
        painter.drawEllipse(14, 14, 36, 36)

        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(22, 22, 20, 20)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self.is_dragging = False
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            if (event.globalPos() - self.drag_pos - self.pos()).manhattanLength() > 5:
                self.is_dragging = True
                self.move(event.globalPos() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self.is_dragging:
            self.open_search_panel()

    def open_search_panel(self):
        screen = QApplication.primaryScreen().geometry()
        panel_x = (screen.width() - self.panel.width()) // 2
        panel_y = (screen.height() - self.panel.height()) // 3
        
        self.panel.move(panel_x, panel_y)
        self.panel.show()
        self.panel.activateWindow()
        self.panel.search_input.setFocus()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setFont(QFont("Poppins", 9))
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(30, 28, 42, 230);
                border: 1px solid rgba(255, 255, 255, 80);
                border-radius: 10px;
                padding: 6px;
                color: #FFFFFF;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: rgba(230, 230, 250, 60);
                color: #FFDAB9;
            }
        """)

        restart_action = menu.addAction("🔄 Restart App")
        exit_action = menu.addAction("❌ Exit (Matikan AI & UI)")

        action = menu.exec_(self.mapToGlobal(event.pos()))

        if action == restart_action:
            QApplication.quit()
            os.execl(sys.executable, sys.executable, *sys.argv)
        elif action == exit_action:
            stop_ai_server()
            QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 1. Daftarkan ke Startup Windows (Mode Silent pythonw)
    set_autostart(enable=True)
    
    # 2. Otomatis nyalakan C:\ai-lokal\llama-server.exe di background
    ensure_ai_server_running()
    
    floating_btn = AssistiveTouchButton()
    floating_btn.show()
    
    app.aboutToQuit.connect(stop_ai_server)
    sys.exit(app.exec_())
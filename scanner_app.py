"""
RetroScan 8mm Studio - Digitalizácia 8mm a Super 8 filmov
Python + PyQt6 + OpenCV + Arduino Stepper Controller
"""

import sys
import os
import time
import glob
import cv2
import numpy as np
import serial
import serial.tools.list_ports

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QMutex, QMutexLocker, QRectF
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QSlider, QCheckBox, QGroupBox, QTabWidget, QFileDialog, QProgressBar,
    QStatusBar, QMessageBox, QTextEdit, QFrame, QSplitter
)

# Pokus o import pygrabber pre názvy kamier
try:
    from pygrabber.dshow_graph import FilterGraph
    HAS_PYGRABBER = True
except ImportError:
    HAS_PYGRABBER = False


# ==============================================================================
# 1. KONTROLÉR PRE ARDUINO A KROKOVÝ MOTOR
# ==============================================================================
class ArduinoController:
    """Spravuje komunikáciu s Arduinom cez sériový port (s podporou simulátora)."""
    def __init__(self):
        self.ser = None
        self.is_connected = False
        self.is_simulator = True
        self.port_name = "SIMULATOR"

    def get_available_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return ["SIMULATOR (Bez Arduina)"] + ports

    def connect(self, port_name, baudrate=115200):
        if port_name.startswith("SIMULATOR"):
            self.is_simulator = True
            self.is_connected = True
            self.port_name = "SIMULATOR"
            return True, "Pripojené k virtuálnemu simulátoru Arduina."
        
        try:
            self.ser = serial.Serial(port_name, baudrate, timeout=1.0)
            time.sleep(1.8) # Čas na reset Arduina po otvorení sériového portu
            self.is_simulator = False
            self.is_connected = True
            self.port_name = port_name
            return True, f"Úspešne pripojené k {port_name}."
        except Exception as e:
            self.is_connected = False
            return False, f"Chyba pripojenia: {str(e)}"

    def disconnect(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.is_connected = False
        self.ser = None

    def step_forward(self, steps=None):
        """Posunie motor dopredu."""
        if not self.is_connected:
            return False, "Motor nie je pripojený."

        if self.is_simulator:
            time.sleep(0.08) # Simulovaná dĺžka kroku motora
            return True, "OK"

        try:
            if steps is None:
                cmd = "STEP\n"
            else:
                cmd = f"STEP:{steps}\n"
            self.ser.write(cmd.encode('utf-8'))
            resp = self.ser.readline().decode('utf-8', errors='ignore').strip()
            return True, resp
        except Exception as e:
            return False, str(e)

    def step_backward(self, steps=None):
        """Posunie motor dozadu."""
        if not self.is_connected:
            return False, "Motor nie je pripojený."

        if self.is_simulator:
            time.sleep(0.08)
            return True, "OK"

        try:
            steps_val = -200 if steps is None else -abs(steps)
            cmd = f"STEP:{steps_val}\n"
            self.ser.write(cmd.encode('utf-8'))
            resp = self.ser.readline().decode('utf-8', errors='ignore').strip()
            return True, resp
        except Exception as e:
            return False, str(e)

    def set_config(self, steps_per_frame, step_delay_us):
        if self.is_simulator or not self.is_connected:
            return
        try:
            self.ser.write(f"SET_STEPS:{steps_per_frame}\n".encode('utf-8'))
            self.ser.readline()
            self.ser.write(f"SET_SPEED:{step_delay_us}\n".encode('utf-8'))
            self.ser.readline()
        except Exception:
            pass


# ==============================================================================
# 2. VLÁKNO PRE KAMERU (Live Preview Thread)
# ==============================================================================
class CameraThread(QThread):
    new_frame_signal = pyqtSignal(np.ndarray)
    error_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.cap = None
        self.is_running = False
        self.mutex = QMutex()
        self.device_index = 1
        self.req_width = 1920
        self.req_height = 1080
        self.is_capturing_active = False # Počas skenovania hlavné vlákno pauzuje stream

    def start_camera(self, device_idx=1, width=1920, height=1080):
        with QMutexLocker(self.mutex):
            self.device_index = device_idx
            self.req_width = width
            self.req_height = height
            self.is_running = True
        self.start()

    def stop_camera(self):
        with QMutexLocker(self.mutex):
            self.is_running = False
        self.wait(2000)

    def run(self):
        self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            # Fallback na MSMF
            self.cap = cv2.VideoCapture(self.device_index, cv2.CAP_MSMF)

        if not self.cap.isOpened():
            self.error_signal.emit(f"Nepodarilo sa otvoriť kameru {self.device_index}")
            return

        # Nastavenie MJPG a rozlíšenia
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.req_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.req_height)

        # Warmup flush
        for _ in range(3):
            self.cap.grab()

        while True:
            with QMutexLocker(self.mutex):
                if not self.is_running:
                    break
                capturing = self.is_capturing_active

            if not capturing:
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    self.new_frame_signal.emit(frame)
                else:
                    time.sleep(0.01)
            else:
                time.sleep(0.01)

        if self.cap:
            self.cap.release()
            self.cap = None

    def grab_single_frame(self, flush_count=2):
        """Vyprázdni starý buffer a zachytí aktuálny čerstvý snímok s plnou ostrosťou."""
        if not self.cap or not self.cap.isOpened():
            return None
        with QMutexLocker(self.mutex):
            for _ in range(flush_count):
                self.cap.grab()
            ret, frame = self.cap.read()
            return frame if ret else None

    def open_hardware_dialog(self):
        """Otvorí natívne dialógové okno ovládača kamery v DirectShow."""
        if self.cap and self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_SETTINGS, 1)


# ==============================================================================
# 3. VLÁKNO PRE AUTOMATICKÉ SKENOVANIE (Scan Worker Thread)
# ==============================================================================
class ScanWorker(QThread):
    frame_scanned_signal = pyqtSignal(int, np.ndarray, str) # frame_num, img, filename
    status_update_signal = pyqtSignal(str)
    scan_finished_signal = pyqtSignal(int)

    def __init__(self, camera_thread, arduino, save_dir, file_prefix, file_ext,
                 max_frames, settle_delay_ms, steps_per_frame, crop_box=None):
        super().__init__()
        self.cam_thread = camera_thread
        self.arduino = arduino
        self.save_dir = save_dir
        self.file_prefix = file_prefix
        self.file_ext = file_ext
        self.max_frames = max_frames
        self.settle_delay_ms = settle_delay_ms
        self.steps_per_frame = steps_per_frame
        self.crop_box = crop_box # [ymin, xmin, ymax, xmax] v percentách (0.0 až 1.0)
        self.is_paused = False
        self.is_cancelled = False

    def run(self):
        os.makedirs(self.save_dir, exist_ok=True)
        # Pozastavenie bežného živého náhľadu, aby vlákna nekolidovali na video grabber
        self.cam_thread.is_capturing_active = True
        time.sleep(0.05)

        current_frame = 0
        self.status_update_signal.emit("Skenovanie spustené...")

        while not self.is_cancelled:
            if self.max_frames > 0 and current_frame >= self.max_frames:
                break

            while self.is_paused and not self.is_cancelled:
                self.status_update_signal.emit("Skenovanie pozastavené.")
                time.sleep(0.1)

            if self.is_cancelled:
                break

            # 1. KROK MOTORA: Posun na ďalšie políčko
            success, msg = self.arduino.step_forward(self.steps_per_frame)
            if not success:
                self.status_update_signal.emit(f"Chyba motora: {msg}")
                break

            # 2. PAUZA NA USTÁLENIE: počkáme kým sa zastavia mechanické vibrácie filmu
            time.sleep(max(0.02, self.settle_delay_ms / 1000.0))

            # 3. ZACHYTENIE SNÍMKY: vyčistenie buffera a grab čerstvého políčka
            frame = self.cam_thread.grab_single_frame(flush_count=2)
            if frame is None:
                self.status_update_signal.emit("Chyba čítania záberu z kamery.")
                continue

            # Voliteľný orez (Crop)
            if self.crop_box is not None:
                h, w = frame.shape[:2]
                ymin, xmin, ymax, xmax = self.crop_box
                y1 = int(ymin * h)
                y2 = int(ymax * h)
                x1 = int(xmin * w)
                x2 = int(xmax * w)
                if y2 > y1 and x2 > x1:
                    frame = frame[y1:y2, x1:x2]

            # 4. ULOŽENIE SÚBORU
            filename = f"{self.file_prefix}_{current_frame:06d}.{self.file_ext}"
            filepath = os.path.join(self.save_dir, filename)
            
            if self.file_ext == "png":
                cv2.imwrite(filepath, frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            else:
                cv2.imwrite(filepath, frame, [cv2.IMWRITE_JPEG_QUALITY, 98])

            current_frame += 1
            self.frame_scanned_signal.emit(current_frame, frame, filepath)

        self.cam_thread.is_capturing_active = False
        self.scan_finished_signal.emit(current_frame)

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def cancel(self):
        self.is_cancelled = True


# ==============================================================================
# 4. WIDGET ŽIVÉHO OBRAZU S VODIACEJ MRIEŽKOU (Interactive Viewport)
# ==============================================================================
class VideoViewportWidget(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #12141a; border-radius: 8px; border: 1px solid #2d3748;")
        self.setMinimumSize(640, 400)
        self.current_frame = None
        self.show_guides = True
        self.show_perforation_line = True
        self.crop_enabled = False
        # Predvolené hranice orezu v % (ymin, xmin, ymax, xmax)
        self.crop_box = [0.08, 0.15, 0.92, 0.85] 

    def update_frame(self, frame):
        self.current_frame = frame
        self.update_pixmap()

    def update_pixmap(self):
        if self.current_frame is None:
            return

        frame = self.current_frame.copy()
        h, w = frame.shape[:2]

        # Konverzia BGR -> RGB
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb_frame.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg)

        # Škálovanie na veľkosť widgetu so zachovaním pomeru strán
        scaled_pix = pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        # Kreslenie vodiacich liniek
        if self.show_guides:
            painter = QPainter(scaled_pix)
            pw = scaled_pix.width()
            ph = scaled_pix.height()

            # Zelený orezový rámik
            pen_crop = QPen(QColor(16, 185, 129, 220), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen_crop)
            ymin, xmin, ymax, xmax = self.crop_box
            rx = int(xmin * pw)
            ry = int(ymin * ph)
            rw = int((xmax - xmin) * pw)
            rh = int((ymax - ymin) * ph)
            painter.drawRect(rx, ry, rw, rh)

            # Jemný stredový kríž
            pen_cross = QPen(QColor(239, 68, 68, 160), 1, Qt.PenStyle.DotLine)
            painter.setPen(pen_cross)
            painter.drawLine(pw // 2, 0, pw // 2, ph)
            painter.drawLine(0, ph // 2, pw, ph // 2)

            # Bočná vodiaca línia perforácie
            if self.show_perforation_line:
                pen_perf = QPen(QColor(245, 158, 11, 200), 2)
                painter.setPen(pen_perf)
                perf_x = int(0.12 * pw)
                painter.drawLine(perf_x, 0, perf_x, ph)

            painter.end()

        self.setPixmap(scaled_pix)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_pixmap()


# ==============================================================================
# 5. HLAVNÉ OKNO APLIKÁCIE (Main Window)
# ==============================================================================
class RetroScanWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RetroScan 8mm Studio - Digitizer Workstation")
        self.setMinimumSize(1260, 800)
        self.setStyleSheet(self.get_dark_style())

        self.arduino = ArduinoController()
        self.cam_thread = CameraThread()
        self.cam_thread.new_frame_signal.connect(self.on_new_frame)
        self.cam_thread.error_signal.connect(self.on_cam_error)

        self.scan_worker = None
        self.scan_start_time = None
        self.latest_frame = None

        self.init_ui()
        self.refresh_devices()
        self.refresh_ports()

        # Štart kamery (predvolene index 1 = HDMI USB Camera)
        QTimer.singleShot(400, self.auto_start_camera)

    def get_dark_style(self):
        return """
        QMainWindow { background-color: #0f1117; }
        QWidget { color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #2d3748;
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 14px;
            background-color: #1a1d26;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 12px;
            padding: 0 6px;
            color: #10b981;
        }
        QPushButton {
            background-color: #2b3245;
            border: 1px solid #3b4256;
            border-radius: 6px;
            padding: 6px 14px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #3b445c; border-color: #10b981; }
        QPushButton:pressed { background-color: #10b981; color: #000; }
        QPushButton#btnPrimary {
            background-color: #10b981;
            color: #042f1f;
            font-size: 14px;
            padding: 10px 18px;
            border: none;
        }
        QPushButton#btnPrimary:hover { background-color: #34d399; }
        QPushButton#btnDanger {
            background-color: #ef4444;
            color: #ffffff;
            font-size: 14px;
            padding: 10px 18px;
            border: none;
        }
        QPushButton#btnDanger:hover { background-color: #f87171; }
        QComboBox, QSpinBox, QDoubleSpinBox {
            background-color: #12141a;
            border: 1px solid #3b4256;
            border-radius: 6px;
            padding: 5px 8px;
            color: #ffffff;
        }
        QSlider::groove:horizontal {
            height: 6px;
            background: #2b3245;
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: #10b981;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #ffffff;
            border: 1px solid #10b981;
            width: 14px;
            margin-top: -4px;
            margin-bottom: -4px;
            border-radius: 7px;
        }
        QTabWidget::pane { border: 1px solid #2d3748; background-color: #1a1d26; border-radius: 8px; }
        QTabBar::tab {
            background: #12141a;
            color: #9ca3af;
            padding: 8px 18px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            margin-right: 4px;
        }
        QTabBar::tab:selected { background: #1a1d26; color: #10b981; font-weight: bold; border-bottom: 2px solid #10b981; }
        QProgressBar {
            background-color: #12141a;
            border: 1px solid #2d3748;
            border-radius: 6px;
            text-align: center;
            color: #ffffff;
            font-weight: bold;
        }
        QProgressBar::chunk { background-color: #10b981; border-radius: 5px; }
        QStatusBar { background-color: #0b0c10; color: #9ca3af; }
        """

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(14)

        # -------------------------------------------------------------
        # ĽAVÁ STRANA: Veľký náhľad kamery (Live Preview + Guides)
        # -------------------------------------------------------------
        left_panel = QVBoxLayout()
        left_panel.setSpacing(8)

        # Hlavička náhľadu
        preview_header = QHBoxLayout()
        lbl_preview_title = QLabel("🎥 ŽIVÝ NÁHĽAD (LIVE STREAM)")
        lbl_preview_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #10b981;")
        
        self.lbl_fps = QLabel("FPS: --")
        self.lbl_fps.setStyleSheet("color: #9ca3af; font-weight: bold;")
        self.chk_guides = QCheckBox("Vodiace línie (Mriežka / Orez)")
        self.chk_guides.setChecked(True)
        self.chk_guides.toggled.connect(self.toggle_guides)

        preview_header.addWidget(lbl_preview_title)
        preview_header.addStretch()
        preview_header.addWidget(self.lbl_fps)
        preview_header.addWidget(self.chk_guides)
        left_panel.addLayout(preview_header)

        # Video Canvas
        self.viewport = VideoViewportWidget()
        left_panel.addWidget(self.viewport, stretch=1)

        # Lišta pod náhľadom
        bottom_preview_bar = QHBoxLayout()
        self.lbl_stream_res = QLabel("Rozlíšenie streamu: Neaktívne")
        self.lbl_stream_res.setStyleSheet("color: #9ca3af;")

        btn_cam_hw_settings = QPushButton("⚙ Nastavenia ovládača kamery (DirectShow)")
        btn_cam_hw_settings.clicked.connect(self.open_cam_driver_dialog)

        btn_snap_single = QPushButton("📸 Uložiť 1 políčko ručne (Snapshot)")
        btn_snap_single.clicked.connect(self.manual_single_snapshot)

        bottom_preview_bar.addWidget(self.lbl_stream_res)
        bottom_preview_bar.addStretch()
        bottom_preview_bar.addWidget(btn_cam_hw_settings)
        bottom_preview_bar.addWidget(btn_snap_single)
        left_panel.addLayout(bottom_preview_bar)

        # -------------------------------------------------------------
        # PRAVÁ STRANA: Ovládací panel (TabWidget)
        # -------------------------------------------------------------
        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)

        tabs = QTabWidget()
        tabs.setFixedWidth(460)

        # TAB 1: Skenovanie (Hlavný workflow)
        tab_scan = self.create_tab_scanning()
        tabs.addTab(tab_scan, "🎞 Skenovanie")

        # TAB 2: Krokový motor & Arduino
        tab_motor = self.create_tab_motor()
        tabs.addTab(tab_motor, "⚙ Motor & Arduino")

        # TAB 3: Kamera & Obraz
        tab_camera = self.create_tab_camera()
        tabs.addTab(tab_camera, "📷 Kamera & Orez")

        right_panel.addWidget(tabs)
        main_layout.addLayout(left_panel, stretch=1)
        main_layout.addLayout(right_panel, stretch=0)

        self.setCentralWidget(main_widget)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Pripravené.")

    # -----------------------------------------------------------------
    # Vytvorenie Tabu: Skenovanie
    # -----------------------------------------------------------------
    def create_tab_scanning(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        # 1. Cieľový adresár
        grp_dir = QGroupBox("Priečinok pre digitalizáciu")
        vbox_dir = QVBoxLayout(grp_dir)
        
        self.txt_dir = QLabel(os.path.abspath("scans/cievka_01"))
        self.txt_dir.setStyleSheet("background-color: #12141a; padding: 6px; border-radius: 4px; border: 1px solid #374151;")
        self.txt_dir.setWordWrap(True)

        btn_browse = QPushButton("📁 Vybrať priečinok...")
        btn_browse.clicked.connect(self.browse_output_dir)

        vbox_dir.addWidget(self.txt_dir)
        vbox_dir.addWidget(btn_browse)
        layout.addWidget(grp_dir)

        # 2. Nastavenie súborov
        grp_files = QGroupBox("Formát a limity")
        grid_files = QGridLayout(grp_files)

        grid_files.addWidget(QLabel("Formát obrázkov:"), 0, 0)
        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["PNG (Bezztrátová maximálna kvalita)", "JPG (Vysoká kvalita 98%)"])
        grid_files.addWidget(self.cmb_format, 0, 1)

        grid_files.addWidget(QLabel("Prefix súborov:"), 1, 0)
        self.cmb_prefix = QComboBox()
        self.cmb_prefix.setEditable(True)
        self.cmb_prefix.addItems(["frame", "film8mm", "super8"])
        grid_files.addWidget(self.cmb_prefix, 1, 1)

        grid_files.addWidget(QLabel("Max počet políčok:"), 2, 0)
        self.spin_max_frames = QSpinBox()
        self.spin_max_frames.setRange(0, 100000)
        self.spin_max_frames.setValue(0) # 0 = neobmedzene (až kým nezastavíte)
        self.spin_max_frames.setSpecialValueText("Neobmedzene (Až po Stop)")
        grid_files.addWidget(self.spin_max_frames, 2, 1)

        layout.addWidget(grp_files)

        # 3. Tlačidlá Štart / Stop
        grp_control = QGroupBox("Riadenie digitalizácie")
        vbox_ctrl = QVBoxLayout(grp_control)

        self.btn_start_scan = QPushButton("▶ SPUSTIŤ AUTOMATICKÉ SKENOVANIE")
        self.btn_start_scan.setObjectName("btnPrimary")
        self.btn_start_scan.clicked.connect(self.start_scanning)

        self.btn_pause_scan = QPushButton("⏸ Pozastaviť (Pauza)")
        self.btn_pause_scan.setEnabled(False)
        self.btn_pause_scan.clicked.connect(self.pause_scanning)

        self.btn_stop_scan = QPushButton("⏹ ZASTAVIŤ SKENOVANIE")
        self.btn_stop_scan.setObjectName("btnDanger")
        self.btn_stop_scan.setEnabled(False)
        self.btn_stop_scan.clicked.connect(self.stop_scanning)

        vbox_ctrl.addWidget(self.btn_start_scan)
        vbox_ctrl.addWidget(self.btn_pause_scan)
        vbox_ctrl.addWidget(self.btn_stop_scan)
        layout.addWidget(grp_control)

        # 4. Štatistika v reálnom čase
        grp_stats = QGroupBox("Priebeh a štatistika")
        grid_stats = QGridLayout(grp_stats)

        self.lbl_count_scanned = QLabel("0")
        self.lbl_count_scanned.setStyleSheet("font-size: 20px; font-weight: bold; color: #10b981;")
        
        self.lbl_film_duration = QLabel("00:00 (pri 18 fps)")
        self.lbl_scan_speed = QLabel("0.0 políčok / sekundu")

        grid_stats.addWidget(QLabel("Nasnímané políčka:"), 0, 0)
        grid_stats.addWidget(self.lbl_count_scanned, 0, 1)

        grid_stats.addWidget(QLabel("Dĺžka výsledného videa:"), 1, 0)
        grid_stats.addWidget(self.lbl_film_duration, 1, 1)

        grid_stats.addWidget(QLabel("Rýchlosť skenovania:"), 2, 0)
        grid_stats.addWidget(self.lbl_scan_speed, 2, 1)

        layout.addWidget(grp_stats)

        # 5. Export videa
        grp_export = QGroupBox("Zostavenie finálneho videa (MP4)")
        vbox_exp = QVBoxLayout(grp_export)

        hbox_fps = QHBoxLayout()
        hbox_fps.addWidget(QLabel("Rýchlosť filmu:"))
        self.cmb_export_fps = QComboBox()
        self.cmb_export_fps.addItems(["18 FPS (Štandard Super 8)", "16 FPS (Starý Standard 8mm)", "24 FPS (Zvukový film)"])
        hbox_fps.addWidget(self.cmb_export_fps)
        vbox_exp.addLayout(hbox_fps)

        self.btn_export_mp4 = QPushButton("🎬 Zostaviť snímky do MP4 videa")
        self.btn_export_mp4.clicked.connect(self.export_to_video)
        vbox_exp.addWidget(self.btn_export_mp4)

        layout.addWidget(grp_export)
        layout.addStretch()
        return widget

    # -----------------------------------------------------------------
    # Vytvorenie Tabu: Motor & Arduino
    # -----------------------------------------------------------------
    def create_tab_motor(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        # 1. Pripojenie sériového portu
        grp_conn = QGroupBox("Sériové pripojenie (Arduino / USB)")
        vbox_conn = QVBoxLayout(grp_conn)

        hbox_port = QHBoxLayout()
        self.cmb_ports = QComboBox()
        self.btn_refresh_ports = QPushButton("🔄 Obnoviť")
        self.btn_refresh_ports.clicked.connect(self.refresh_ports)
        hbox_port.addWidget(self.cmb_ports, stretch=1)
        hbox_port.addWidget(self.btn_refresh_ports)
        vbox_conn.addLayout(hbox_port)

        self.btn_toggle_connect = QPushButton("🔌 Pripojiť k Arduinu")
        self.btn_toggle_connect.clicked.connect(self.toggle_arduino_connection)
        vbox_conn.addWidget(self.btn_toggle_connect)

        self.lbl_arduino_status = QLabel("Stav: Simulátor (Pripravené bez hardvéru)")
        self.lbl_arduino_status.setStyleSheet("color: #f59e0b; font-weight: bold;")
        vbox_conn.addWidget(self.lbl_arduino_status)
        layout.addWidget(grp_conn)

        # 2. Kalibrácia posunu políčka
        grp_calib = QGroupBox("Parametre posunu políčka")
        grid_calib = QGridLayout(grp_calib)

        grid_calib.addWidget(QLabel("Kroky na 1 políčko:"), 0, 0)
        self.spin_steps_per_frame = QSpinBox()
        self.spin_steps_per_frame.setRange(1, 10000)
        self.spin_steps_per_frame.setValue(200) # 200 krokov = 1 celá otáčka motora
        grid_calib.addWidget(self.spin_steps_per_frame, 0, 1)

        grid_calib.addWidget(QLabel("Čas na ustálenie (ms):"), 1, 0)
        self.spin_settle_delay = QSpinBox()
        self.spin_settle_delay.setRange(10, 2000)
        self.spin_settle_delay.setValue(80) # 80 ms pre zastavenie chvenia filmu
        self.spin_settle_delay.setSuffix(" ms")
        grid_calib.addWidget(self.spin_settle_delay, 1, 1)

        grid_calib.addWidget(QLabel("Pauza kroku (rýchlosť motora):"), 2, 0)
        self.spin_step_us = QSpinBox()
        self.spin_step_us.setRange(200, 5000)
        self.spin_step_us.setValue(1000)
        self.spin_step_us.setSuffix(" µs")
        grid_calib.addWidget(self.spin_step_us, 2, 1)

        layout.addWidget(grp_calib)

        # 3. Manuálne krokovanie (Jogging)
        grp_jog = QGroupBox("Manuálne ovládanie posunu filmu")
        vbox_jog = QVBoxLayout(grp_jog)

        hbox_steps = QHBoxLayout()
        btn_step_back = QPushButton("⏪ O 1 políčko späť")
        btn_step_back.clicked.connect(self.jog_backward)

        btn_step_fwd = QPushButton("⏩ O 1 políčko vpred")
        btn_step_fwd.clicked.connect(self.jog_forward)

        hbox_steps.addWidget(btn_step_back)
        hbox_steps.addWidget(btn_step_fwd)
        vbox_jog.addLayout(hbox_steps)

        # Jemné mikrokroky
        hbox_micro = QHBoxLayout()
        btn_micro_back = QPushButton("◀ Mikrokrok (-10)")
        btn_micro_back.clicked.connect(lambda: self.jog_custom(-10))
        btn_micro_fwd = QPushButton("Mikrokrok (+10) ▶")
        btn_micro_fwd.clicked.connect(lambda: self.jog_custom(10))
        hbox_micro.addWidget(btn_micro_back)
        hbox_micro.addWidget(btn_micro_fwd)
        vbox_jog.addLayout(hbox_micro)

        layout.addWidget(grp_jog)
        layout.addStretch()
        return widget

    # -----------------------------------------------------------------
    # Vytvorenie Tabu: Kamera & Obraz
    # -----------------------------------------------------------------
    def create_tab_camera(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        # Výber zariadenia a rozlíšenia
        grp_cam = QGroupBox("Kamera a rozlíšenie")
        grid_cam = QGridLayout(grp_cam)

        grid_cam.addWidget(QLabel("Zariadenie:"), 0, 0)
        self.cmb_cam_device = QComboBox()
        grid_cam.addWidget(self.cmb_cam_device, 0, 1)

        grid_cam.addWidget(QLabel("Rozlíšenie snímania:"), 1, 0)
        self.cmb_cam_res = QComboBox()
        self.cmb_cam_res.addItem("1920 x 1080 (Full HD - Odporúčané)", (1920, 1080))
        self.cmb_cam_res.addItem("3840 x 2160 (4K UHD)", (3840, 2160))
        self.cmb_cam_res.addItem("2560 x 1440 (2K QHD)", (2560, 1440))
        self.cmb_cam_res.addItem("1280 x 720 (HD)", (1280, 720))
        grid_cam.addWidget(self.cmb_cam_res, 1, 1)

        btn_apply_cam = QPushButton("🔄 Reštartovať stream s týmto nastavením")
        btn_apply_cam.clicked.connect(self.apply_camera_settings)
        grid_cam.addWidget(btn_apply_cam, 2, 0, 1, 2)

        layout.addWidget(grp_cam)

        # Nastavenie orezania (Crop)
        grp_crop = QGroupBox("Orezanie na políčko filmu (Crop)")
        vbox_crop = QVBoxLayout(grp_crop)

        self.chk_apply_crop = QCheckBox("Aktivovať orezanie pri ukladaní záberov")
        self.chk_apply_crop.setChecked(False)
        vbox_crop.addWidget(self.chk_apply_crop)

        # Slidery pre hranice orezu
        vbox_crop.addWidget(QLabel("Šírka orezu (Horizontálne okno):"))
        self.slider_crop_x = QSlider(Qt.Orientation.Horizontal)
        self.slider_crop_x.setRange(40, 95)
        self.slider_crop_x.setValue(70)
        self.slider_crop_x.valueChanged.connect(self.update_crop_geometry)
        vbox_crop.addWidget(self.slider_crop_x)

        vbox_crop.addWidget(QLabel("Výška orezu (Vertikálne okno):"))
        self.slider_crop_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_crop_y.setRange(40, 95)
        self.slider_crop_y.setValue(84)
        self.slider_crop_y.valueChanged.connect(self.update_crop_geometry)
        vbox_crop.addWidget(self.slider_crop_y)

        layout.addWidget(grp_crop)
        layout.addStretch()
        return widget

    # -----------------------------------------------------------------
    # Metódy ovládania kamery
    # -----------------------------------------------------------------
    def refresh_devices(self):
        self.cmb_cam_device.clear()
        if HAS_PYGRABBER:
            graph = FilterGraph()
            devices = graph.get_input_devices()
            for idx, name in enumerate(devices):
                self.cmb_cam_device.addItem(f"{idx}: {name}", idx)
        else:
            self.cmb_cam_device.addItem("Index 1: HDMI USB Camera", 1)
            self.cmb_cam_device.addItem("Index 0: Webkamera", 0)

        # Vyber index 1 ak existuje
        for i in range(self.cmb_cam_device.count()):
            if self.cmb_cam_device.itemData(i) == 1:
                self.cmb_cam_device.setCurrentIndex(i)
                break

    def auto_start_camera(self):
        dev_idx = self.cmb_cam_device.currentData() if self.cmb_cam_device.count() > 0 else 1
        w, h = self.cmb_cam_res.currentData()
        self.cam_thread.start_camera(dev_idx, w, h)
        self.lbl_stream_res.setText(f"Rozlíšenie streamu: {w} × {h}")

    def apply_camera_settings(self):
        self.cam_thread.stop_camera()
        dev_idx = self.cmb_cam_device.currentData()
        w, h = self.cmb_cam_res.currentData()
        self.cam_thread.start_camera(dev_idx, w, h)
        self.lbl_stream_res.setText(f"Rozlíšenie streamu: {w} × {h}")
        self.status_bar.showMessage(f"Kamera nastavená na {w}x{h}.")

    def open_cam_driver_dialog(self):
        self.cam_thread.open_hardware_dialog()

    def on_new_frame(self, frame):
        self.latest_frame = frame
        self.viewport.update_frame(frame)

    def on_cam_error(self, err_msg):
        self.status_bar.showMessage(f"Chyba kamery: {err_msg}")

    def toggle_guides(self, checked):
        self.viewport.show_guides = checked
        self.viewport.update_pixmap()

    def update_crop_geometry(self):
        # Aktualizuje percentuálne rozmery crop rámika
        half_w = (self.slider_crop_x.value() / 100.0) / 2.0
        half_h = (self.slider_crop_y.value() / 100.0) / 2.0
        cx, cy = 0.5, 0.5
        self.viewport.crop_box = [
            max(0.0, cy - half_h),
            max(0.0, cx - half_w),
            min(1.0, cy + half_h),
            min(1.0, cx + half_w)
        ]
        self.viewport.update_pixmap()

    # -----------------------------------------------------------------
    # Metódy ovládania Arduina
    # -----------------------------------------------------------------
    def refresh_ports(self):
        self.cmb_ports.clear()
        ports = self.arduino.get_available_ports()
        self.cmb_ports.addItems(ports)

    def toggle_arduino_connection(self):
        if self.arduino.is_connected:
            self.arduino.disconnect()
            self.btn_toggle_connect.setText("🔌 Pripojiť k Arduinu")
            self.lbl_arduino_status.setText("Stav: Odpojené")
            self.lbl_arduino_status.setStyleSheet("color: #ef4444; font-weight: bold;")
        else:
            selected_port = self.cmb_ports.currentText()
            ok, msg = self.arduino.connect(selected_port)
            if ok:
                self.btn_toggle_connect.setText("❌ Odpojiť")
                if self.arduino.is_simulator:
                    self.lbl_arduino_status.setText("Stav: Virtuálny simulátor (Aktívny)")
                    self.lbl_arduino_status.setStyleSheet("color: #f59e0b; font-weight: bold;")
                else:
                    self.lbl_arduino_status.setText(f"Stav: Pripojené ({selected_port})")
                    self.lbl_arduino_status.setStyleSheet("color: #10b981; font-weight: bold;")
                self.status_bar.showMessage(msg)
            else:
                QMessageBox.critical(self, "Chyba pripojenia", msg)

    def jog_forward(self):
        steps = self.spin_steps_per_frame.value()
        self.arduino.step_forward(steps)
        self.status_bar.showMessage(f"Posun vpred o {steps} krokov.")

    def jog_backward(self):
        steps = self.spin_steps_per_frame.value()
        self.arduino.step_backward(steps)
        self.status_bar.showMessage(f"Posun vzad o {steps} krokov.")

    def jog_custom(self, steps):
        if steps > 0:
            self.arduino.step_forward(steps)
        else:
            self.arduino.step_backward(abs(steps))
        self.status_bar.showMessage(f"Mikrokrok {steps} krokov.")

    # -----------------------------------------------------------------
    # Metódy digitalizácie a ukladania snímok
    # -----------------------------------------------------------------
    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Vyber priečinok pre digitalizáciu")
        if dir_path:
            self.txt_dir.setText(os.path.abspath(dir_path))

    def manual_single_snapshot(self):
        save_dir = self.txt_dir.text()
        os.makedirs(save_dir, exist_ok=True)
        frame = self.cam_thread.grab_single_frame(flush_count=2)
        if frame is not None:
            ts = int(time.time() * 1000)
            fname = os.path.join(save_dir, f"manual_snap_{ts}.png")
            cv2.imwrite(fname, frame)
            self.status_bar.showMessage(f"Ručný snímok uložený: {fname}")
        else:
            QMessageBox.warning(self, "Upozornenie", "Nepodarilo sa zachytiť snímku z kamery.")

    def start_scanning(self):
        save_dir = self.txt_dir.text()
        file_prefix = self.cmb_prefix.currentText().strip() or "frame"
        file_ext = "png" if "PNG" in self.cmb_format.currentText() else "jpg"
        max_frames = self.spin_max_frames.value()
        settle_delay_ms = self.spin_settle_delay.value()
        steps_per_frame = self.spin_steps_per_frame.value()
        crop_box = self.viewport.crop_box if self.chk_apply_crop.isChecked() else None

        self.btn_start_scan.setEnabled(False)
        self.btn_pause_scan.setEnabled(True)
        self.btn_stop_scan.setEnabled(True)
        self.scan_start_time = time.time()

        self.scan_worker = ScanWorker(
            self.cam_thread, self.arduino, save_dir, file_prefix, file_ext,
            max_frames, settle_delay_ms, steps_per_frame, crop_box
        )
        self.scan_worker.frame_scanned_signal.connect(self.on_frame_scanned)
        self.scan_worker.status_update_signal.connect(self.status_bar.showMessage)
        self.scan_worker.scan_finished_signal.connect(self.on_scan_finished)
        self.scan_worker.start()

    def pause_scanning(self):
        if not self.scan_worker:
            return
        if self.scan_worker.is_paused:
            self.scan_worker.resume()
            self.btn_pause_scan.setText("⏸ Pozastaviť (Pauza)")
        else:
            self.scan_worker.pause()
            self.btn_pause_scan.setText("▶ Pokračovať")

    def stop_scanning(self):
        if self.scan_worker:
            self.scan_worker.cancel()
            self.btn_stop_scan.setEnabled(False)
            self.btn_pause_scan.setEnabled(False)

    def on_frame_scanned(self, count, frame, filepath):
        self.lbl_count_scanned.setText(str(count))
        # Aktualizácia náhľadu na naposledy nasnímané políčko
        self.viewport.update_frame(frame)

        # Výpočet dĺžky videa pri 18 FPS
        total_seconds = count / 18.0
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        self.lbl_film_duration.setText(f"{minutes:02d}:{seconds:02d} (pri 18 fps)")

        # Rýchlosť skenovania
        elapsed = time.time() - self.scan_start_time
        if elapsed > 0:
            speed = count / elapsed
            self.lbl_scan_speed.setText(f"{speed:.1f} políčok / sek")

    def on_scan_finished(self, total_frames):
        self.btn_start_scan.setEnabled(True)
        self.btn_pause_scan.setEnabled(False)
        self.btn_pause_scan.setText("⏸ Pozastaviť (Pauza)")
        self.btn_stop_scan.setEnabled(False)
        self.status_bar.showMessage(f"Skenovanie dokončené. Celkovo nasnímaných {total_frames} políčok.")
        QMessageBox.information(self, "Hotovo", f"Skenovanie úspešne dokončené!\nCelkový počet políčok: {total_frames}")

    # -----------------------------------------------------------------
    # Export do MP4 videa
    # -----------------------------------------------------------------
    def export_to_video(self):
        save_dir = self.txt_dir.text()
        files = sorted(glob.glob(os.path.join(save_dir, "*.png")) + glob.glob(os.path.join(save_dir, "*.jpg")))
        if not files:
            QMessageBox.warning(self, "Chyba", "V zvolenom priečinku sa nenašli žiadne nasnímané snímky (PNG/JPG).")
            return

        fps_text = self.cmb_export_fps.currentText()
        if "16" in fps_text:
            fps = 16.0
        elif "24" in fps_text:
            fps = 24.0
        else:
            fps = 18.0

        out_path, _ = QFileDialog.getSaveFileName(self, "Uložiť finálne MP4 video", os.path.join(save_dir, "vysledny_film.mp4"), "Video (*.mp4)")
        if not out_path:
            return

        # Zistenie rozmerov prvého obrázka
        sample = cv2.imread(files[0])
        h, w = sample.shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(out_path, fourcc, fps, (w, h))

        progress = QProgressBar(self)
        progress.setRange(0, len(files))
        progress.setValue(0)
        self.status_bar.addWidget(progress)

        for i, f in enumerate(files):
            img = cv2.imread(f)
            if img is not None:
                if img.shape[:2] != (h, w):
                    img = cv2.resize(img, (w, h))
                out.write(img)
            progress.setValue(i + 1)
            QApplication.processEvents()

        out.release()
        self.status_bar.removeWidget(progress)
        self.status_bar.showMessage(f"Video úspešne vytvorené: {out_path}")
        QMessageBox.information(self, "Export dokončený", f"Video bolo úspešne vygenerované:\n{out_path}\n({len(files)} políčok pri {fps} FPS)")

    def closeEvent(self, event):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            self.scan_worker.wait()
        self.cam_thread.stop_camera()
        self.arduino.disconnect()
        event.accept()


# ==============================================================================
# SPUSTENIE APLIKÁCIE
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RetroScanWindow()
    window.show()
    sys.exit(app.exec())

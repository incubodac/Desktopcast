"""
Desktop Yamaha Receiver Tidal Navigator
A PyQt6 application for controlling Yamaha receivers with MusicCast/YXC API
"""

import sys
import json
import time
import socket
import threading
from collections import Counter

import requests

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QListWidget, QListWidgetItem,
    QComboBox, QMessageBox, QGroupBox, QMenu, QStyle, QStyleOptionSlider
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSize, QUrl, QSettings
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QColor, QKeySequence, QShortcut
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

# Receiver IP offered when no receiver has been remembered yet
DEFAULT_IP = "192.168.0.248"

# Enable/disable debug output
DEBUG = False

# Where remembered receivers are stored (QSettings)
SETTINGS_ORG = "Desktopcast"
SETTINGS_APP = "Desktopcast"

# Sent with every request so the receiver pushes change events to us over UDP
EVENT_APP_NAME = "MusicCast/1.0(Desktopcast)"

# Polling: fast until the receiver's push events are confirmed working, then a slow safety net
POLL_MS_WITHOUT_EVENTS = 1500
POLL_MS_WITH_EVENTS = 5000

# List item attribute bit (getListInfo): item can be added to the play queue
ATTR_ADD_PLAY_QUEUE = 1 << 25

# Volume: one click of -/+ in slider steps, and the largest jump up sent at once
# (a click near the end of the slider ramps up instead of blasting)
VOLUME_BUTTON_STEPS = 2
VOLUME_MAX_STEP_UP = 12

# Known receiver models. The R-N602 doesn't report volume in dB, so it's computed
# as db_min + volume * db_step (Yamaha's 0.5 dB steps, volume 0 = silent).
# queue_editing marks models where the undocumented queue commands were tested.
MODEL_PROFILES = {
    "R-N602": {
        "label": "Yamaha R-N602",
        "db_min": -80.5,
        "db_step": 0.5,
        "loud_db": -20.0,
        "scale_db": (-80, -60, -40, -20, 0),
        "queue_editing": True,
    },
}


def debug_print(*args, **kwargs):
    """Print debug messages if DEBUG is enabled"""
    if DEBUG:
        print(*args, **kwargs)


def resolve_profile(model_name: str, features: dict) -> dict:
    """Volume and capability profile for a receiver: a known model's profile,
    else what getFeatures says, else Yamaha's usual 0-161 = -80.5..0 dB convention"""
    zone = {}
    if features:
        zone = next((z for z in features.get("zone", []) if z.get("id") == "main"), {})
    steps = {r.get("id"): r for r in zone.get("range_step", [])}
    volume_range = steps.get("volume", {"min": 0, "max": 100})

    profile = {
        "model": model_name or "Unknown",
        "label": model_name or "Unknown model",
        "known": model_name in MODEL_PROFILES,
        "vol_min": volume_range.get("min", 0),
        "vol_max": volume_range.get("max", 100),
        "db_min": None,
        "db_step": None,
        "db_source": None,
        "loud_db": None,
        "scale_db": None,
        "queue_editing": False,
    }

    if model_name in MODEL_PROFILES:
        profile.update(MODEL_PROFILES[model_name])
        profile["db_source"] = "profile"
    elif "actual_volume_db" in steps:
        profile["db_min"] = steps["actual_volume_db"].get("min")
        profile["db_step"] = steps["actual_volume_db"].get("step")
        profile["db_source"] = "receiver"
    elif profile["vol_max"] == 161:
        profile["db_min"], profile["db_step"] = -80.5, 0.5
        profile["db_source"] = "estimate"

    if profile["db_min"] is not None and profile["scale_db"] is None:
        top = profile["db_min"] + profile["vol_max"] * profile["db_step"]
        profile["scale_db"] = tuple(range(-80, int(top) + 1, 20))
    return profile


def volume_to_db(profile: dict, volume: int):
    """Volume in dB for a raw volume value, or None if the model has no dB scale"""
    if not profile or profile.get("db_min") is None:
        return None
    return profile["db_min"] + volume * profile["db_step"]


def db_to_volume(profile: dict, db: float) -> int:
    """Raw volume value for a dB value (inverse of volume_to_db)"""
    return round((db - profile["db_min"]) / profile["db_step"])


def format_volume(profile: dict, volume: int) -> str:
    """Volume as the receiver's display would show it (dB when known)"""
    db = volume_to_db(profile, volume)
    if db is None:
        return str(volume)
    if volume <= profile.get("vol_min", 0):
        return "−∞ dB"
    return f"{db:+.1f} dB".replace("-", "−")


def format_time(seconds) -> str:
    """Seconds as m:ss (empty for unknown values)"""
    if not isinstance(seconds, int) or seconds < 0:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


# =============================================================================
# YAMAHA API CLASS
# =============================================================================

class YamahaAPI:
    """Handler for Yamaha Extended Control (YXC) API"""
    
    def __init__(self, ip: str = DEFAULT_IP):
        self.base_url = f"http://{ip}/YamahaExtendedControl/v1"
        self.ip = ip
        self.zone = "main"
        self.timeout = 5
        # One kept-alive connection pool: about half the latency of a new connection per request
        self.session = requests.Session()

    def enable_events(self, port: int):
        """Ask the receiver to push change events to a local UDP port.
        Every request renews the subscription, which otherwise lapses after 10 minutes."""
        self.session.headers.update({"X-AppName": EVENT_APP_NAME, "X-AppPort": str(port)})

    def _get(self, endpoint: str, params: dict = None, timeout: float = None) -> dict:
        """Make a GET request to the API"""
        try:
            url = f"{self.base_url}{endpoint}"
            debug_print(f"[API] GET {endpoint} params={params}")
            response = self.session.get(url, params=params, timeout=timeout or self.timeout)
            if response.status_code == 200:
                data = response.json()
                debug_print(f"[API] Response: code={data.get('response_code', 'N/A')}")
                return data
            else:
                debug_print(f"[API] HTTP {response.status_code}: {response.text[:100]}")
        except requests.exceptions.Timeout:
            debug_print(f"[API] Timeout: {endpoint}")
        except requests.exceptions.ConnectionError as e:
            debug_print(f"[API] Connection error: {endpoint} - {e}")
        except Exception as e:
            debug_print(f"[API] Error: {endpoint} - {type(e).__name__}: {e}")
        return None

    # === SYSTEM ENDPOINTS ===
    
    def get_features(self) -> dict:
        """Get device features and capabilities"""
        return self._get("/system/getFeatures")

    def get_device_info(self) -> dict:
        """Get device information"""
        return self._get("/system/getDeviceInfo")

    def get_network_status(self) -> dict:
        """Get network information (includes the receiver's room name)"""
        return self._get("/system/getNetworkStatus")

    # === ZONE ENDPOINTS ===
    
    def get_status(self) -> dict:
        """Get current zone status"""
        return self._get(f"/{self.zone}/getStatus")

    def set_power(self, power_on: bool) -> dict:
        """Set power state"""
        state = "on" if power_on else "standby"
        return self._get(f"/{self.zone}/setPower", {"power": state})

    def set_volume(self, volume: int) -> dict:
        """Set volume level"""
        return self._get(f"/{self.zone}/setVolume", {"volume": volume})

    def set_mute(self, mute: bool) -> dict:
        """Mute or unmute"""
        return self._get(f"/{self.zone}/setMute", {"enable": "true" if mute else "false"})

    def set_input(self, input_id: str) -> dict:
        """Set input source"""
        return self._get(f"/{self.zone}/setInput", {"input": input_id})

    # === NETUSB ENDPOINTS ===
    
    def get_play_info(self) -> dict:
        """Get current playback information"""
        return self._get("/netusb/getPlayInfo")

    def set_playback(self, command: str) -> dict:
        """Control playback (play, pause, stop, previous, next)"""
        return self._get("/netusb/setPlayback", {"playback": command})

    def get_service_info(self) -> dict:
        """Get available streaming services"""
        return self._get("/netusb/getServiceInfo")

    # === LIST/BROWSE ENDPOINTS ===
    
    def get_list_info(self, input_id: str = None, index: int = 0, 
                      size: int = 8, list_id: str = "main") -> dict:
        """Get list/menu information for browsing"""
        params = {
            "list_id": list_id,
            "index": index,
            "size": size,
            "lang": "en"
        }
        if input_id:
            params["input"] = input_id
        return self._get("/netusb/getListInfo", params)

    def select_list_item(self, index: int) -> dict:
        """Select an item in the current menu (navigate into folder or play)"""
        params = {
            "list_id": "main",
            "type": "select",
            "index": index,
            "zone": self.zone
        }
        return self._get("/netusb/setListControl", params)

    def play_list_item(self, index: int) -> dict:
        """Play an item from the current menu"""
        params = {
            "list_id": "main",
            "type": "play",
            "index": index,
            "zone": self.zone
        }
        return self._get("/netusb/setListControl", params)

    def return_to_parent(self) -> dict:
        """Go back to parent menu"""
        params = {
            "list_id": "main",
            "type": "return",
            "zone": self.zone
        }
        return self._get("/netusb/setListControl", params)

    # === RECENT/QUEUE/PRESETS ===
    
    def get_recent_info(self) -> dict:
        """Get recently played items"""
        return self._get("/netusb/getRecentInfo")

    def play_recent(self, index: int) -> dict:
        """Play a recent item by index"""
        params = {"index": index, "zone": self.zone}
        return self._get("/netusb/recallRecentItem", params)

    def get_play_queue(self, size: int = 80) -> dict:
        """Get current play queue"""
        return self._get("/netusb/getPlayQueue", {"index": 0, "size": size})

    # Queue editing isn't in Yamaha's public spec; these calls were verified on an RX-A602.

    def manage_play_queue(self, operation: str, index: int) -> dict:
        """Act on a play queue entry ("play" jumps to it, "remove" deletes it)"""
        params = {"type": operation, "index": index, "zone": self.zone, "timeout": 5000}
        return self._get("/netusb/managePlayQueue", params, timeout=10)

    def manage_list(self, operation: str, index: int) -> dict:
        """Queue an item of the current menu ("add_to_queue" appends it,
        "play_next" inserts it after the current track)"""
        params = {"list_id": "main", "type": operation, "index": index,
                  "zone": self.zone, "timeout": 5000}
        return self._get("/netusb/manageList", params, timeout=10)

    def get_preset_info(self) -> dict:
        """Get saved presets"""
        return self._get("/netusb/getPresetInfo")

    def recall_preset(self, num: int) -> dict:
        """Play a saved preset"""
        params = {"zone": self.zone, "num": num}
        return self._get("/netusb/recallPreset", params)

    def store_preset(self, num: int) -> dict:
        """Store current playback as a preset"""
        return self._get("/netusb/storePreset", {"num": num})

    # === SEARCH ===
    
    def send_search_text(self, search_string: str, index: int = 7) -> dict:
        """Send search text to the receiver"""
        url = f"{self.base_url}/netusb/setSearchString"
        payload = {
            "list_id": "main",
            "string": search_string,
            "index": index
        }
        try:
            response = self.session.post(url, json=payload, timeout=10)
            return response.json()
        except Exception as e:
            debug_print(f"[API] Search error: {e}")
            return {"response_code": -1, "error": str(e)}

    # === DISTRIBUTION ===
    
    def get_distribution_info(self) -> dict:
        """Get multi-room distribution info"""
        return self._get("/dist/getDistributionInfo")

    # === COMBINED POLLING ===
    
    def poll_all_status(self, input_id: str = None, fetch_index: int = 0) -> dict:
        """Poll all status endpoints at once"""
        status = self.get_status()
        play_info = self.get_play_info()
        
        # Skip list fetch if fetch_index is -1
        list_info = None
        if fetch_index >= 0:
            list_info = self.get_list_info(
                input_id=input_id, 
                list_id="main", 
                size=8, 
                index=fetch_index
            )
        
        return {
            "status": status,
            "play_info": play_info,
            "list_info": list_info,
            "fetch_index": fetch_index
        }


# =============================================================================
# WORKER THREAD
# =============================================================================

class Worker(QObject):
    """Worker for running tasks in background threads"""
    finished = pyqtSignal(object)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        # Always emit, so a failing task can't leave a button stuck in its busy state
        try:
            result = self.func(*self.args, **self.kwargs)
        except Exception as e:
            debug_print(f"[Worker] {type(e).__name__}: {e}")
            result = None
        self.finished.emit(result)


# =============================================================================
# RECEIVER EVENTS (UDP push notifications)
# =============================================================================

class EventListener:
    """Receives the change events a MusicCast receiver pushes over UDP
    (volume, power, track changes, play time...), so the app doesn't have to poll fast"""

    def __init__(self, receiver_ip: str, on_event):
        self.receiver_ip = receiver_ip
        self.on_event = on_event
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))          # any free port; the receiver is told which one
        self.sock.settimeout(1.0)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.running:
            try:
                data, (sender, _) = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if sender != self.receiver_ip:
                continue
            try:
                event = json.loads(data)
            except ValueError:
                continue
            debug_print(f"[Event] {event}")
            self.on_event(event)

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


# =============================================================================
# VOLUME WIDGETS
# =============================================================================

class VolumeSlider(QSlider):
    """Horizontal volume slider that can tell where a value sits on screen (for the scale)"""

    def value_to_x(self, value: int) -> float:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                      QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                      QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        pos = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), value, span)
        return groove.x() + handle.width() / 2 + pos


class VolumeScale(QWidget):
    """Labelled marks under the volume slider (dB for known models); the loud zone is orange"""

    def __init__(self, slider: VolumeSlider):
        super().__init__()
        self.slider = slider
        self.marks = []           # (value, label, is_loud)
        self.setFixedHeight(self.fontMetrics().height() + 6)

    def set_marks(self, marks):
        self.marks = marks
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 2))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        normal = self.palette().color(self.foregroundRole())
        normal.setAlpha(150)
        for value, label, is_loud in self.marks:
            x = self.slider.value_to_x(value)
            painter.setPen(QColor(220, 120, 0) if is_loud else normal)
            painter.drawLine(int(x), 0, int(x), 3)
            width = metrics.horizontalAdvance(label)
            left = min(max(0, x - width / 2), self.width() - width)
            painter.drawText(int(left), 4 + metrics.ascent(), label)
        painter.end()


# =============================================================================
# MAIN APPLICATION
# =============================================================================

class DesktopCastApp(QMainWindow):
    """Main application window"""
    
    # Signals for thread-safe UI updates
    update_browse_signal = pyqtSignal(object)
    update_status_signal = pyqtSignal(object)
    update_playlist_signal = pyqtSignal(object)
    receiver_event_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Desktopcast")
        self.resize(600, 800)

        # Remembered receivers (by device ID, so a new IP from the router updates the entry)
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        # State
        self.api = None
        self.profile = resolve_profile(None, None)
        self.device_id = None
        self.events = None                 # EventListener once connected
        self.events_seen = False           # True after the first pushed event arrived
        self.poll_in_flight = False
        self.power_state = "unknown"
        self.mute_state = False
        self.receiver_volume = None        # last volume the receiver reported
        self.volume_touched_at = 0.0       # when the user last moved the volume
        self.volume_sending = False
        self.volume_resend = False
        self.volume_sent = None
        self.current_input = "tidal"
        self.current_view = "browse"
        self.current_menu_layer = 0
        self.current_menu_name = ""
        self.browse_index = 0
        self.max_line = 8
        self.total_items = 0
        self.accumulated_items = []
        self.current_menu_hash = ""
        self.list_fully_loaded = False
        self.last_location_hash = ""
        self.polling_enabled = True
        self.playback_state = "stop"
        self.skip_list_update = False
        self.viewing_album_tracks = False  # True when showing album track list from play queue

        # Playlist mode: double-click queues items, the receiver's queue is shown alongside
        self.playlist_mode = False
        self.playlist_loading = False
        self.playlist_signature = None
        self.playlist_texts = None         # last queue seen, to notice when it gets wiped
        self.playlist_removes = 0          # removals sent since then (those explain shrinking)
        self.width_before_playlist = 600

        # Last thing the app asked the receiver to do (reported if the queue gets wiped)
        self.last_action = ""
        self.last_action_time = 0.0

        # Thumbnail handling
        self.thumbnail_cache = {}
        self.network_manager = QNetworkAccessManager()
        self.network_manager.finished.connect(self._on_thumbnail_loaded)
        self.pending_thumbnails = {}
        
        # Keep worker references to prevent garbage collection
        self.workers = []

        # Connect signals
        self.update_browse_signal.connect(self._update_browse_widget)
        self.update_status_signal.connect(self._on_poll_result)
        self.update_playlist_signal.connect(self._update_playlist_widget)
        self.receiver_event_signal.connect(self._on_receiver_event)

        # Build UI
        self._setup_ui()

        # Polling timer
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self._poll_status)

        # Playlist refresh timer (runs only in playlist mode)
        self.playlist_timer = QTimer()
        self.playlist_timer.timeout.connect(self._refresh_playlist)

        # Volume is sent at most every 150 ms while the slider moves
        self.volume_send_timer = QTimer()
        self.volume_send_timer.setSingleShot(True)
        self.volume_send_timer.timeout.connect(self._send_volume)

        # Start with controls disabled
        self._enable_controls(False)

        # Reconnect to the receiver used last time
        if self.settings.value("last_ip", ""):
            QTimer.singleShot(0, lambda: self._connect_device(quiet=True))

    def closeEvent(self, event):
        """Stop the event listener when the window closes"""
        if self.events:
            self.events.stop()
        super().closeEvent(event)

    def _run_in_background(self, func, on_done=None):
        """Run func in a thread; on_done(result) runs on the UI thread afterwards"""
        worker = Worker(func)
        if on_done:
            worker.finished.connect(on_done)
        worker.finished.connect(lambda _result, w=worker: self.workers.remove(w))
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    def _note_action(self, text: str):
        """Remember the last command sent, to explain a wiped queue if that happens"""
        self.last_action = text
        self.last_action_time = time.monotonic()

    def _setup_ui(self):
        """Build the user interface"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Connection Section ---
        conn_box = QGroupBox("Connection")
        conn_layout = QHBoxLayout()
        
        # Editable list of remembered receivers ("IP · name (model)"); a typed IP works too
        self.ip_input = QComboBox()
        self.ip_input.setEditable(True)
        self.ip_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.ip_input.lineEdit().setPlaceholderText("Receiver IP (e.g., 192.168.1.50)")
        self.ip_input.lineEdit().returnPressed.connect(self._connect_device)
        self._load_saved_receivers()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(lambda: self._connect_device())

        conn_layout.addWidget(QLabel("Receiver:"))
        conn_layout.addWidget(self.ip_input, 1)
        conn_layout.addWidget(self.connect_btn)
        conn_box.setLayout(conn_layout)
        layout.addWidget(conn_box)

        # --- Basic Controls ---
        control_box = QGroupBox("Basic Control")
        control_layout = QHBoxLayout()

        self.power_btn = QPushButton("Power: ?")
        self.power_btn.setCheckable(True)
        self.power_btn.clicked.connect(self._toggle_power)

        self.mute_btn = QPushButton("🔈")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip("Mute")
        self.mute_btn.setFixedWidth(44)
        self.mute_btn.clicked.connect(self._toggle_mute)

        self.vol_down_btn = QPushButton("−")
        self.vol_down_btn.setFixedWidth(32)
        self.vol_down_btn.setAutoRepeat(True)
        self.vol_down_btn.clicked.connect(lambda: self._step_volume(-VOLUME_BUTTON_STEPS))
        self.vol_up_btn = QPushButton("+")
        self.vol_up_btn.setFixedWidth(32)
        self.vol_up_btn.setAutoRepeat(True)
        self.vol_up_btn.clicked.connect(lambda: self._step_volume(VOLUME_BUTTON_STEPS))

        # The slider sends while it moves (drag, click, wheel, arrow keys), throttled
        self.vol_slider = VolumeSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setPageStep(10)
        self.vol_slider.valueChanged.connect(self._on_volume_slider_changed)
        self.vol_scale = VolumeScale(self.vol_slider)

        slider_column = QVBoxLayout()
        slider_column.setSpacing(0)
        slider_column.addWidget(self.vol_slider)
        slider_column.addWidget(self.vol_scale)

        self.vol_label = QLabel("–")
        self.vol_label.setMinimumWidth(110)
        self.vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        font = self.vol_label.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        self.vol_label.setFont(font)

        control_layout.addWidget(self.power_btn)
        control_layout.addWidget(self.mute_btn)
        control_layout.addWidget(self.vol_down_btn)
        control_layout.addLayout(slider_column, 1)
        control_layout.addWidget(self.vol_up_btn)
        control_layout.addWidget(self.vol_label)
        control_box.setLayout(control_layout)
        layout.addWidget(control_box)

        # --- Input & Navigation ---
        nav_box = QGroupBox("Input && Navigation")
        nav_layout = QVBoxLayout()

        # Input selector
        input_layout = QHBoxLayout()
        self.input_combo = QComboBox()
        default_services = ["tidal", "spotify", "deezer", "qobuz", "amazon_music", 
                          "napster", "server", "net_radio", "usb", "bluetooth"]
        for svc in default_services:
            self.input_combo.addItem(svc)
        
        self.set_input_btn = QPushButton("Set Input")
        self.set_input_btn.clicked.connect(self._change_input)
        
        input_layout.addWidget(QLabel("Input:"))
        input_layout.addWidget(self.input_combo)
        input_layout.addWidget(self.set_input_btn)

        # Navigation buttons - Row 1
        nav_btn_layout = QHBoxLayout()
        self.recent_btn = QPushButton("🕐 Recent")
        self.recent_btn.clicked.connect(self._show_recent)
        self.presets_btn = QPushButton("⭐ Presets")
        self.presets_btn.clicked.connect(self._show_presets)
        self.save_preset_btn = QPushButton("💾 Save")
        self.save_preset_btn.clicked.connect(self._save_preset)
        # The playlist panel is the queue view: it shows the receiver's queue and edits it
        self.playlist_btn = QPushButton("🎶 Playlist Mode")
        self.playlist_btn.setCheckable(True)
        self.playlist_btn.setToolTip("Show the queue beside the browser; double-click adds instead of playing")
        self.playlist_btn.toggled.connect(self._toggle_playlist_mode)

        nav_btn_layout.addWidget(self.recent_btn)
        nav_btn_layout.addWidget(self.presets_btn)
        nav_btn_layout.addWidget(self.save_preset_btn)
        nav_btn_layout.addWidget(self.playlist_btn)

        # Navigation buttons - Row 2
        browse_btn_layout = QHBoxLayout()
        self.home_btn = QPushButton("🏠 Home")
        self.home_btn.clicked.connect(self._go_home)
        self.back_btn = QPushButton("⬅️ Back")
        self.back_btn.clicked.connect(self._browse_back)
        self.back_btn.setEnabled(False)
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self._refresh_browse)

        browse_btn_layout.addWidget(self.home_btn)
        browse_btn_layout.addWidget(self.back_btn)
        browse_btn_layout.addWidget(self.refresh_btn)

        # Search
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search...")
        self.search_input.returnPressed.connect(self._do_search)
        self.search_btn = QPushButton("🔍")
        self.search_btn.clicked.connect(self._do_search)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_btn)

        # Status label, and a hint saying what double-click does right now
        self.status_label = QLabel("Not connected")
        self.mode_hint = QLabel()
        self.mode_hint.setStyleSheet("color: gray;")
        self._update_mode_hint()

        # List widget
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(64, 64))
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_click)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_browse_menu)

        nav_layout.addLayout(input_layout)
        nav_layout.addLayout(nav_btn_layout)
        nav_layout.addLayout(browse_btn_layout)
        nav_layout.addLayout(search_layout)
        nav_layout.addWidget(self.status_label)
        nav_layout.addWidget(self.mode_hint)
        nav_layout.addWidget(self.list_widget)
        nav_box.setLayout(nav_layout)

        # --- Playlist (visible in playlist mode) ---
        self.playlist_box = QGroupBox("🎶 Playlist (receiver queue)")
        playlist_layout = QVBoxLayout()

        self.playlist_info = QLabel(
            "Double-click a song or album in the browser to add it. Right-click for more."
        )
        self.playlist_info.setWordWrap(True)

        # Shown when the receiver wipes the queue, with the command that came just before
        self.playlist_warning = QLabel()
        self.playlist_warning.setWordWrap(True)
        self.playlist_warning.setStyleSheet("color: rgb(200, 100, 0);")
        self.playlist_warning.hide()

        self.playlist_widget = QListWidget()
        self.playlist_widget.itemDoubleClicked.connect(self._play_playlist_item)
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            shortcut = QShortcut(QKeySequence(key), self.playlist_widget)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self._remove_playlist_item)

        playlist_btn_layout = QHBoxLayout()
        self.playlist_play_btn = QPushButton("▶ Play")
        self.playlist_play_btn.clicked.connect(
            lambda: self._play_playlist_item(self.playlist_widget.currentItem())
        )
        self.playlist_skip_btn = QPushButton("⏭ Skip")
        self.playlist_skip_btn.clicked.connect(self._skip_playlist_track)
        self.playlist_remove_btn = QPushButton("🗑 Remove")
        self.playlist_remove_btn.clicked.connect(self._remove_playlist_item)
        self.playlist_refresh_btn = QPushButton("🔄")
        self.playlist_refresh_btn.clicked.connect(self._refresh_playlist)

        playlist_btn_layout.addWidget(self.playlist_play_btn)
        playlist_btn_layout.addWidget(self.playlist_skip_btn)
        playlist_btn_layout.addWidget(self.playlist_remove_btn)
        playlist_btn_layout.addWidget(self.playlist_refresh_btn)

        playlist_layout.addWidget(self.playlist_info)
        playlist_layout.addWidget(self.playlist_warning)
        playlist_layout.addWidget(self.playlist_widget)
        playlist_layout.addLayout(playlist_btn_layout)
        self.playlist_box.setLayout(playlist_layout)
        self.playlist_box.hide()

        # Browser and playlist side by side
        content_layout = QHBoxLayout()
        content_layout.addWidget(nav_box, 3)
        content_layout.addWidget(self.playlist_box, 2)
        layout.addLayout(content_layout)

        # --- Now Playing ---
        np_box = QGroupBox("Now Playing")
        np_layout = QVBoxLayout()
        
        self.track_label = QLabel("Track: -")
        self.artist_label = QLabel("Artist: -")
        self.album_label = QLabel("Album: -")
        self.time_label = QLabel("")
        self.play_time = None
        self.total_time = None

        trans_layout = QHBoxLayout()
        self.prev_btn = QPushButton("⏮ Prev")
        self.play_btn = QPushButton("⏯ Play")
        self.next_btn = QPushButton("⏭ Next")
        
        self.prev_btn.clicked.connect(lambda: self._transport("previous"))
        self.play_btn.clicked.connect(lambda: self._transport("play_pause"))
        self.next_btn.clicked.connect(lambda: self._transport("next"))
        
        trans_layout.addWidget(self.prev_btn)
        trans_layout.addWidget(self.play_btn)
        trans_layout.addWidget(self.next_btn)

        np_layout.addWidget(self.track_label)
        np_layout.addWidget(self.artist_label)
        np_layout.addWidget(self.album_label)
        np_layout.addWidget(self.time_label)
        np_layout.addLayout(trans_layout)
        np_box.setLayout(np_layout)
        layout.addWidget(np_box)

    def _enable_controls(self, enable: bool):
        """Enable or disable all controls"""
        controls = [
            self.power_btn, self.mute_btn, self.vol_down_btn, self.vol_slider, self.vol_up_btn,
            self.input_combo, self.set_input_btn,
            self.list_widget, self.recent_btn, self.presets_btn,
            self.save_preset_btn, self.home_btn, self.refresh_btn, self.playlist_btn,
            self.prev_btn, self.play_btn, self.next_btn, self.search_btn
        ]
        for ctrl in controls:
            ctrl.setEnabled(enable)

    def _update_mode_hint(self):
        """Tell the user what double-click does in the current mode"""
        if self.playlist_mode:
            self.mode_hint.setText("🎶 Double-click adds to the playlist · right-click for more")
        else:
            self.mode_hint.setText("Double-click plays (replaces the queue) · right-click for more")

    # =========================================================================
    # CONNECTION
    # =========================================================================

    def _saved_receivers(self) -> list:
        """Remembered receivers, most recently used first"""
        try:
            receivers = json.loads(self.settings.value("receivers", "[]"))
        except (TypeError, ValueError):
            receivers = []
        return [r for r in receivers if isinstance(r, dict) and r.get("ip")]

    @staticmethod
    def _receiver_label(receiver: dict) -> str:
        """Text shown in the receiver list: IP first, so it can be read back"""
        details = " · ".join(x for x in (receiver.get("name"), receiver.get("model")) if x)
        return f"{receiver['ip']}  ·  {details}" if details else receiver["ip"]

    def _load_saved_receivers(self):
        """Fill the receiver list from settings (falls back to the default IP)"""
        self.ip_input.clear()
        receivers = self._saved_receivers()
        for receiver in receivers:
            self.ip_input.addItem(self._receiver_label(receiver), receiver["ip"])
        if not receivers:
            self.ip_input.setEditText(DEFAULT_IP)

    def _remember_receiver(self, ip: str, device_id: str, model: str, name: str):
        """Save this receiver as the most recent one (same device ID = same entry)"""
        receivers = [r for r in self._saved_receivers()
                     if r.get("ip") != ip and not (device_id and r.get("device_id") == device_id)]
        receivers.insert(0, {"ip": ip, "device_id": device_id, "model": model, "name": name})
        self.settings.setValue("receivers", json.dumps(receivers[:10]))
        self.settings.setValue("last_ip", ip)
        self.ip_input.blockSignals(True)
        self._load_saved_receivers()
        self.ip_input.setCurrentIndex(0)
        self.ip_input.blockSignals(False)

    def _entered_ip(self) -> str:
        """IP from the receiver field (an entry reads "IP · name (model)")"""
        text = self.ip_input.currentText().strip()
        return text.split()[0] if text else ""

    def _connect_device(self, quiet: bool = False):
        """Connect to the Yamaha receiver (quiet: startup reconnect, no error dialog)"""
        ip = self._entered_ip()
        if not ip:
            QMessageBox.warning(self, "Warning", "Please enter an IP address")
            return

        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting...")
        self.status_label.setText(f"Connecting to {ip}...")

        def do_connect():
            api = YamahaAPI(ip)
            features = api.get_features()
            if not features:
                return None

            device = api.get_device_info() or {}
            network = api.get_network_status() or {}
            services = api.get_service_info()
            status = api.get_status()
            current_input = "tidal"
            if status and status.get('response_code') == 0:
                current_input = status.get('input', 'tidal')

            list_info = api.get_list_info(input_id=current_input, list_id="main", size=8)
            return (api, features, device, network, services, status, list_info, current_input)

        def on_connected(result):
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("Connect")

            if not result or not result[1] or result[1].get('response_code') != 0:
                self.status_label.setText(f"❌ Could not reach a receiver at {ip}")
                if not quiet:
                    QMessageBox.critical(self, "Error",
                        "Could not connect to Yamaha receiver.\n"
                        "Check IP address and network.")
                return

            api, features, device, network, services, status, list_info, current_input = result
            if self.events:
                self.events.stop()
            self.api = api
            self._enable_controls(True)

            # Model profile (volume scale in dB, tested features)
            model = device.get('model_name', '')
            name = network.get('network_name', '')
            self.device_id = device.get('device_id', '')
            self.profile = resolve_profile(model, features)
            self._apply_volume_profile()
            self._remember_receiver(ip, self.device_id, model, name)
            self.setWindowTitle(f"Desktopcast · {name or model or ip}")

            # Ask the receiver to push changes; keep fast polling until events show up
            self.events_seen = False
            try:
                self.events = EventListener(ip, self.receiver_event_signal.emit)
                api.enable_events(self.events.port)
            except OSError as e:
                debug_print(f"[Event] Listener not started: {e}")
                self.events = None

            # Update input combo if we got service info
            if services and services.get('response_code') == 0:
                service_list = services.get('service_list', [])
                if service_list:
                    self.input_combo.clear()
                    for svc in service_list:
                        if isinstance(svc, dict):
                            if svc.get('enable', True):
                                service_id = svc.get('id') or svc.get('input', '')
                                if service_id:
                                    self.input_combo.addItem(service_id)
                        elif isinstance(svc, str):
                            self.input_combo.addItem(svc)

            # Set current input
            self.current_input = current_input
            idx = self.input_combo.findText(current_input)
            if idx >= 0:
                self.input_combo.setCurrentIndex(idx)

            # Show power/volume right away, then the browse list
            self._apply_status_update({"status": status})
            if list_info and list_info.get('response_code') == 0:
                self._update_browse_widget(list_info)

            # Start polling
            self.poll_timer.start(POLL_MS_WITHOUT_EVENTS)
            known = "" if self.profile["known"] else " (no profile for this model yet)"
            self.status_label.setText(f"✅ Connected: {name or ip} · {self.profile['label']}{known}")

        self._run_in_background(do_connect, on_connected)

    # =========================================================================
    # BASIC CONTROLS
    # =========================================================================

    def _toggle_power(self):
        """Toggle power state"""
        if not self.api:
            return
        turn_on = self.power_state != "on"
        self.power_btn.setText("Power: ...")
        self._run_in_background(lambda: self.api.set_power(turn_on),
                                lambda _: QTimer.singleShot(500, self._poll_status))

    # --- Volume ---

    def _apply_volume_profile(self):
        """Set the slider range and the scale marks from the receiver's profile"""
        profile = self.profile
        self.vol_slider.blockSignals(True)
        self.vol_slider.setRange(profile["vol_min"], profile["vol_max"])
        self.vol_slider.blockSignals(False)

        marks = []
        if profile.get("db_min") is not None:
            loud = profile.get("loud_db")
            for db in profile.get("scale_db") or ():
                value = db_to_volume(profile, db)
                if profile["vol_min"] <= value <= profile["vol_max"]:
                    label = f"{db}" if db < 0 else f"{db} dB"
                    marks.append((value, label.replace("-", "−"), loud is not None and db >= loud))
            source = {
                "profile": f"{profile['label']} profile",
                "receiver": "reported by the receiver",
                "estimate": "estimated (Yamaha's usual 0.5 dB steps)",
            }[profile["db_source"]]
            self.vol_label.setToolTip(
                f"dB = {profile['db_min']} + volume × {profile['db_step']} ({source})"
            )
        else:
            span = profile["vol_max"] - profile["vol_min"]
            for fraction in (0, 0.25, 0.5, 0.75, 1):
                value = profile["vol_min"] + round(span * fraction)
                marks.append((value, str(value), False))
            self.vol_label.setToolTip("This model reports no dB scale")
        self.vol_scale.set_marks(marks)

    def _update_volume_label(self, value: int):
        """Show the volume as the receiver displays it"""
        text = format_volume(self.profile, value)
        if self.mute_state:
            text = f"🔇 {text}"
        self.vol_label.setText(text)
        loud = self.profile.get("loud_db")
        db = volume_to_db(self.profile, value)
        is_loud = loud is not None and db is not None and db >= loud
        self.vol_label.setStyleSheet("color: rgb(220, 120, 0);" if is_loud else "")

    def _show_receiver_volume(self, value: int):
        """Show a volume the receiver reported, unless the user is moving the slider"""
        self.receiver_volume = value
        if self.volume_sent is None:
            self.volume_sent = value
        user_busy = (self.vol_slider.isSliderDown()
                     or time.monotonic() - self.volume_touched_at < 2.0
                     or self.volume_sending or self.volume_send_timer.isActive())
        if user_busy:
            return
        self.volume_sent = value
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(value)
        self.vol_slider.blockSignals(False)
        self._update_volume_label(value)

    def _on_volume_slider_changed(self, value: int):
        """User moved the volume (drag, click, wheel, keys): update the label, send soon"""
        self._update_volume_label(value)
        self.volume_touched_at = time.monotonic()
        if not self.volume_send_timer.isActive():
            self.volume_send_timer.start(150)

    def _step_volume(self, steps: int):
        """-/+ buttons"""
        self.vol_slider.setValue(self.vol_slider.value() + steps)

    def _send_volume(self):
        """Send the slider's volume; one request at a time, and big jumps up are ramped"""
        if not self.api:
            return
        if self.volume_sending:
            self.volume_resend = True
            return

        target = self.vol_slider.value()
        value = target
        if self.volume_sent is not None and target > self.volume_sent + VOLUME_MAX_STEP_UP:
            value = self.volume_sent + VOLUME_MAX_STEP_UP
        if value == self.volume_sent:
            return

        self.volume_sending = True
        self.volume_sent = value

        def on_sent(_result):
            self.volume_sending = False
            self.volume_touched_at = time.monotonic()
            if self.volume_resend or self.vol_slider.value() != self.volume_sent:
                self.volume_resend = False
                self.volume_send_timer.start(150)

        self._run_in_background(lambda: self.api.set_volume(value), on_sent)

    def _toggle_mute(self):
        """Mute button"""
        if not self.api:
            return
        mute = self.mute_btn.isChecked()
        self._set_mute_display(mute)
        self._run_in_background(lambda: self.api.set_mute(mute))

    def _set_mute_display(self, mute: bool):
        """Show the mute state on the button and the volume label"""
        self.mute_state = mute
        self.mute_btn.setChecked(mute)
        self.mute_btn.setText("🔇" if mute else "🔈")
        self._update_volume_label(self.vol_slider.value())

    def _change_input(self):
        """Change input source"""
        if not self.api:
            return
        
        input_id = self.input_combo.currentText()
        if not input_id:
            return

        # Reset state for new input
        self.accumulated_items = []
        self.current_menu_hash = ""
        self.list_fully_loaded = False
        self.current_input = input_id
        
        self.set_input_btn.setEnabled(False)
        self.set_input_btn.setText("Switching...")
        self._note_action(f"switched input to {input_id}")

        def switch_input():
            self.api.set_input(input_id)
            time.sleep(1.5)
            return self.api.get_list_info(input_id=input_id, list_id="main", size=8)

        def on_switched(list_info):
            self.set_input_btn.setEnabled(True)
            self.set_input_btn.setText("Set Input")
            if list_info and list_info.get('response_code') == 0:
                self.last_location_hash = ""
                self._update_browse_widget(list_info)

        self._run_in_background(switch_input, on_switched)

    def _transport(self, cmd: str):
        """Transport control"""
        if not self.api:
            return
        if cmd == "play_pause":
            cmd = "pause" if self.playback_state == "play" else "play"
            # Show the new state at once, so a quick second press does the right thing
            self._show_playback_state("pause" if cmd == "pause" else "play")
        self._run_in_background(lambda: self.api.set_playback(cmd),
                                lambda _: QTimer.singleShot(300, self._poll_status))

    def _show_playback_state(self, playback: str):
        """Remember the playback state and label the play button accordingly"""
        self.playback_state = playback
        self.play_btn.setText("⏸ Pause" if playback == "play" else "▶ Play")

    # =========================================================================
    # CONTENT VIEWS (Recent, Queue, Presets)
    # =========================================================================

    def _show_recent(self):
        """Show recently played items"""
        if not self.api:
            return
        self.status_label.setText("View: 🕐 Recent Items")
        self.current_view = "recent"

        def fetch():
            return self.api.get_recent_info()

        def update(data):
            self.list_widget.clear()
            if not data or data.get('response_code') != 0:
                self.list_widget.addItem("❌ Could not load recent items")
                return
            
            items = data.get('recent_info', [])
            for i, item in enumerate(items):
                text = item.get('text', '???')
                input_type = item.get('input', '').lower()
                icon = "🌊" if "tidal" in input_type else "🎵"
                
                w_item = QListWidgetItem(f"{icon} {text}")
                w_item.setData(Qt.ItemDataRole.UserRole, {
                    "type": "recent", "index": i, "data": item
                })
                self.list_widget.addItem(w_item)
            
            if not items:
                self.list_widget.addItem("📭 No recent items")

        self._run_in_background(fetch, update)

    def _show_presets(self):
        """Show saved presets"""
        if not self.api:
            return
        self.status_label.setText("View: ⭐ Presets")
        self.current_view = "presets"

        def fetch():
            return self.api.get_preset_info()

        def update(data):
            self.list_widget.clear()
            if not data or data.get('response_code') != 0:
                self.list_widget.addItem("❌ Could not load presets")
                return
            
            items = data.get('preset_info', [])
            has_presets = False
            
            for i, item in enumerate(items):
                text = item.get('text', '')
                if text:
                    has_presets = True
                    input_type = item.get('input', '').lower()
                    icon = "🌊" if "tidal" in input_type else "⭐"
                    
                    w_item = QListWidgetItem(f"{icon} [{i+1}] {text}")
                    w_item.setData(Qt.ItemDataRole.UserRole, {
                        "type": "preset", "num": i + 1, "data": item
                    })
                    self.list_widget.addItem(w_item)
            
            if not has_presets:
                self.list_widget.addItem("📭 No presets saved")
                self.list_widget.addItem("💡 Play something and click Save")

        self._run_in_background(fetch, update)

    def _save_preset(self):
        """Save current playback as preset"""
        if not self.api:
            return

        self.status_label.setText("💾 Saving current track to presets...")

        def do_save():
            # First empty slot; never overwrite a saved preset
            info = self.api.get_preset_info()
            if not info or info.get('response_code') != 0:
                return None
            for i, p in enumerate(info.get('preset_info', [])):
                if not p.get('text'):
                    return i + 1, self.api.store_preset(i + 1)
            return "full"

        def on_saved(result):
            if result == "full":
                self.status_label.setText("❌ All presets are in use; nothing was overwritten")
            elif result and result[1] and result[1].get('response_code') == 0:
                self.status_label.setText(f"💾 Saved as preset {result[0]}")
            else:
                self.status_label.setText("❌ Could not save the preset")

        self._run_in_background(do_save, on_saved)

    # =========================================================================
    # BROWSE NAVIGATION
    # =========================================================================

    def _go_home(self):
        """Go to home/root menu"""
        if not self.api:
            return
        self.current_view = "browse"
        self.accumulated_items = []
        self.current_menu_hash = ""
        self.list_fully_loaded = False
        self.viewing_album_tracks = False
        self.skip_list_update = False

        def do_home():
            for _ in range(10):
                data = self.api.get_list_info(
                    input_id=self.current_input, list_id="main", size=8
                )
                if not data or data.get('response_code') != 0:
                    return data
                if data.get('menu_layer', 0) == 0:
                    return data
                self.api.return_to_parent()
                time.sleep(0.2)
            return self.api.get_list_info(
                input_id=self.current_input, list_id="main", size=8
            )

        def on_home(data):
            if data and data.get('response_code') == 0:
                self.last_location_hash = ""
                self._update_browse_widget(data)

        self._run_in_background(do_home, on_home)

    def _browse_back(self):
        """Navigate back to parent menu"""
        if not self.api or (self.current_menu_layer <= 0 and not self.viewing_album_tracks):
            return

        # Disable polling during navigation
        self.polling_enabled = False
        self.accumulated_items = []
        self.current_menu_hash = ""
        self.list_fully_loaded = False
        self.last_location_hash = ""
        self.viewing_album_tracks = False
        self.skip_list_update = False
        
        target_layer = self.current_menu_layer - 1
        self.status_label.setText(f"Going back to layer {target_layer}...")

        def do_back():
            # Send return command
            result = self.api.return_to_parent()
            debug_print(f"return_to_parent result: {result}")
            
            # Wait for receiver to process
            time.sleep(0.5)
            
            # Get the new list
            list_info = self.api.get_list_info(
                input_id=self.current_input, list_id="main", size=8
            )
            debug_print(f"After back: layer={list_info.get('menu_layer') if list_info else 'N/A'}")
            return list_info

        def on_back(data):
            self.polling_enabled = True
            if data and data.get('response_code') == 0:
                self._update_browse_widget(data)
            else:
                self.status_label.setText("Failed to go back")

        self._run_in_background(do_back, on_back)

    def _refresh_browse(self):
        """Force refresh - fetches current state including play queue for album tracks"""
        if not self.api:
            return
        
        self.current_view = "browse"
        self.status_label.setText("🔄 Refreshing...")
        
        # Reset all state flags
        self.accumulated_items = []
        self.current_menu_hash = ""
        self.list_fully_loaded = False
        self.last_location_hash = ""
        self.skip_list_update = False
        self.viewing_album_tracks = False
        self.polling_enabled = True
        
        def do_refresh():
            # The queue lives in the playlist panel; refresh only reloads the browser
            return self.api.get_list_info(
                input_id=self.current_input,
                list_id="main",
                size=8,
                index=0
            )

        def on_refreshed(list_info):
            if list_info and list_info.get('response_code') == 0:
                self._update_browse_widget(list_info)
            else:
                self.status_label.setText("❌ Refresh failed")

        self._run_in_background(do_refresh, on_refreshed)
        self._refresh_playlist()

    def _sync_receiver_state(self):
        """Alias for refresh - kept for compatibility"""
        self._refresh_browse()

    def _do_search(self):
        """Perform search"""
        if not self.api:
            return
        
        search_text = self.search_input.text().strip()
        if not search_text:
            return

        self.current_view = "browse"
        self.search_btn.setEnabled(False)
        self.search_btn.setText("...")
        self._note_action(f"searched for '{search_text}'")

        def search():
            # Go to root first
            data = None
            for _ in range(10):
                data = self.api.get_list_info(input_id=self.current_input)
                if not data or data.get('response_code') != 0:
                    return {"error": "Could not get menu"}
                if data.get('menu_layer', 0) == 0:
                    break
                self.api.return_to_parent()
                time.sleep(0.3)

            # Find search item
            items = data.get('list_info', [])
            search_idx = None
            for i, item in enumerate(items):
                if item.get('attribute') == 10:
                    search_idx = i
                    break

            if search_idx is None:
                return {"error": "Search not available"}

            # Send search
            result = self.api.send_search_text(search_text, index=search_idx)
            if result.get('response_code') != 0:
                return {"error": f"Search failed: {result}"}

            time.sleep(0.5)
            return self.api.get_list_info(input_id=self.current_input)

        def on_search(result):
            self.search_btn.setEnabled(True)
            self.search_btn.setText("🔍")
            
            if isinstance(result, dict) and result.get('error'):
                self.status_label.setText(f"Search error: {result['error']}")
            else:
                self.accumulated_items = []
                self.current_menu_hash = ""
                self.list_fully_loaded = False
                self.last_location_hash = ""
                self._update_browse_widget(result)

        self._run_in_background(search, on_search)

    # =========================================================================
    # BROWSE LIST DISPLAY
    # =========================================================================

    def _update_browse_widget(self, data: dict, preserve_scroll: bool = False):
        """Update the browse list widget"""
        scroll_pos = 0
        if preserve_scroll:
            scroll_pos = self.list_widget.verticalScrollBar().value()

        self.pending_thumbnails.clear()
        self.list_widget.clear()

        if not data or data.get('response_code') != 0:
            self.list_widget.addItem("❌ Could not load menu")
            self.status_label.setText("Error loading menu")
            return

        # Detect response format: 
        # - Browse list uses 'list_info' 
        # - Album/queue content uses 'track_info' with type='system'
        data_type = data.get('type', 'browse')
        is_track_list = data_type == 'system' or 'track_info' in data
        
        if is_track_list:
            # Album/queue track list format
            items = data.get('track_info', [])
            self.current_menu_layer = 4  # Treat as deep level (album tracks)
            # Try to get album name from first track
            if items and items[0].get('album'):
                self.current_menu_name = items[0].get('album', 'Album Tracks')
            else:
                self.current_menu_name = "Album Tracks"
            self.max_line = data.get('max_line', len(items))
            self.browse_index = 0  # Track list always starts at 0
            playing_idx = data.get('playing_index', -1)
            debug_print(f"[Browse] Track list format: {len(items)} tracks, playing_index={playing_idx}, album='{self.current_menu_name}'")
        else:
            # Standard browse list format
            items = data.get('list_info', [])
            self.current_menu_layer = data.get('menu_layer', 0)
            self.current_menu_name = data.get('menu_name', 'Menu')
            self.max_line = data.get('max_line', 8)
            self.browse_index = data.get('index', 0)
            playing_idx = data.get('playing_index', -1)

        # Update status label
        count = len(items)
        total = self.total_items if self.total_items > 0 else count
        progress = f"{count}/{total}" if count < total else f"{count}"
        
        if is_track_list:
            album_indicator = " (album view)" if self.viewing_album_tracks else ""
            self.status_label.setText(f"🎵 {self.current_menu_name}{album_indicator} | {progress} tracks")
        else:
            self.status_label.setText(
                f"📂 {self.current_menu_name} | Layer: {self.current_menu_layer} | {progress} items"
            )
        
        # Debug: print all unique attributes found
        attrs_found = set(item.get('attribute', 0) for item in items)
        debug_print(f"[Browse] Layer {self.current_menu_layer}, menu='{self.current_menu_name}', "
                   f"is_track_list={is_track_list}, attributes found: {attrs_found}")

        # Enable/disable back button
        self.back_btn.setEnabled(self.current_menu_layer > 0 or is_track_list or self.viewing_album_tracks)

        # Populate list
        for i, item in enumerate(items):
            # Track list items use 'track' field, browse items use 'text'
            if is_track_list:
                text = item.get('track', item.get('text', '???'))
                # For track list, create subtexts from artist/album
                artist = item.get('artist', '')
                album = item.get('album', '')
                subtexts = []
                if artist:
                    subtexts.append(artist)
                thumbnail = item.get('albumart_url', item.get('thumbnail', ''))
            else:
                text = item.get('text', '???')
                subtexts = item.get('subtexts', [])
                thumbnail = item.get('thumbnail', '')
            attr = item.get('attribute', 0)
            
            # Check for additional properties that might indicate item type
            item_playable = item.get('playable', None)
            item_enterable = item.get('enterable', None)
            
            # Debug: log first few items' full structure
            if i < 3:
                debug_print(f"  Item {i}: text='{text}' attr={attr} playable={item_playable} enterable={item_enterable}")

            # Attribute meanings in YXC API (bitmask values):
            # Bit 0 (1) = playable
            # Bit 1 (2) = container/folder (can navigate into)
            # Simple values:
            # 0 = unplayable/unavailable
            # 1 = playable item (track)
            # 2 = folder/container
            # 10 = search
            # 17 = track in album/queue (from track_info)
            # 66 = artist (container)
            # 82 = album (container)
            # Large numbers (e.g., 125829190) = bitmask with multiple capabilities
            
            # Known container attributes (simple values)
            container_attrs = (2, 10, 66, 82)
            # Known track attributes (simple values)
            track_attrs = (1, 17)
            
            # Determine if item is a container or playable track
            if is_track_list:
                # In track list (album contents), everything is a playable track
                is_container = False
                is_playable = True
            elif attr in container_attrs:
                is_container = True
                is_playable = False
            elif attr in track_attrs:
                # Explicitly marked as playable track
                is_container = False
                is_playable = True
            elif attr == 0:
                # Unplayable/unavailable
                is_container = False
                is_playable = False
            elif attr > 1000:
                # Large attribute = bitmask. Check context to determine type.
                # If we're in an "Albums" menu, these are albums (containers)
                # If we're in a playlist/tracks menu, these are tracks
                menu_lower = self.current_menu_name.lower()
                if 'album' in menu_lower or 'discography' in menu_lower:
                    # In albums list - items are album containers
                    is_container = True
                    is_playable = False
                elif 'track' in menu_lower or 'song' in menu_lower or self.current_menu_layer >= 4:
                    # In tracks list - items are playable
                    is_container = False
                    is_playable = True
                elif (attr & 2) != 0 and self.current_menu_layer <= 3:
                    # Has container bit set and not deep in menu - treat as container
                    is_container = True
                    is_playable = False
                else:
                    # Default: treat as playable at deeper levels
                    is_container = False
                    is_playable = True
            else:
                # Unknown attribute - treat based on menu level
                if self.current_menu_layer >= 4:
                    is_container = False
                    is_playable = True
                else:
                    is_container = True
                    is_playable = False

            # Build display text
            display = text
            if subtexts:
                display = f"{text}\n  {' | '.join(subtexts)}"

            # Icon based on type
            if attr == 10:
                icon = "🔍"
            elif attr == 66:
                icon = "👤"
            elif attr == 82:
                icon = "💿"
            elif attr == 2:
                icon = "📁"
            elif is_container and 'album' in self.current_menu_name.lower():
                icon = "💿"  # Album in albums list
            elif is_container:
                icon = "📁"
            elif is_playable:
                icon = "🎵"
            else:
                icon = "⚪"

            # Mark currently playing
            actual_idx = self.browse_index + i
            # For track lists, playing_index is 0-based; for browse, it matches actual_idx
            if is_track_list and i == playing_idx:
                icon = "▶️"
            elif not is_track_list and actual_idx == playing_idx:
                icon = "▶️"

            w_item = QListWidgetItem(f"{icon} {display}")
            w_item.setData(Qt.ItemDataRole.UserRole, {
                "type": "track" if is_track_list else "browse",
                "index": i if is_track_list else actual_idx,  # Track list uses 0-based index
                "is_container": is_container,
                "is_playable": is_playable,
                "is_track_list": is_track_list,
                "text": text,
                "thumbnail": thumbnail,
                "attribute": attr
            })

            if thumbnail:
                self._load_thumbnail(thumbnail, w_item)

            self.list_widget.addItem(w_item)

        if not items:
            self.list_widget.addItem("📭 Menu is empty")

        if preserve_scroll and scroll_pos > 0:
            self.list_widget.verticalScrollBar().setValue(scroll_pos)

    def _load_thumbnail(self, url: str, item: QListWidgetItem):
        """Load thumbnail asynchronously"""
        if url in self.thumbnail_cache:
            item.setIcon(self.thumbnail_cache[url])
            return

        try:
            request = QNetworkRequest(QUrl(url))
            reply = self.network_manager.get(request)
            self.pending_thumbnails[reply] = (item, url)
        except Exception as e:
            debug_print(f"Thumbnail error: {e}")

    def _on_thumbnail_loaded(self, reply):
        """Handle thumbnail download completion"""
        try:
            if reply not in self.pending_thumbnails:
                return

            item, url = self.pending_thumbnails.pop(reply)

            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = reply.readAll()
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        64, 64,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    icon = QIcon(scaled)
                    self.thumbnail_cache[url] = icon
                    try:
                        if item and self.list_widget.row(item) >= 0:
                            item.setIcon(icon)
                    except RuntimeError:
                        pass
        except Exception as e:
            debug_print(f"Thumbnail load error: {e}")
        finally:
            try:
                reply.deleteLater()
            except:
                pass

    # =========================================================================
    # ITEM INTERACTION
    # =========================================================================

    def _on_item_double_click(self, item):
        """Handle double-click on list item"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        item_type = data.get("type", "")
        
        if item_type == "recent":
            idx = data.get("index", 0)
            text = data.get("data", {}).get("text", "")
            if not self._confirm_replace_queue(text):
                return
            debug_print(f"Playing recent item {idx}")
            self._note_action(f"played recent item '{text}'")
            self._run_in_background(lambda: self.api.play_recent(idx))

        elif item_type == "preset":
            num = data.get("num", 1)
            text = data.get("data", {}).get("text", "")
            if not self._confirm_replace_queue(text):
                return
            debug_print(f"Playing preset {num}")
            self._note_action(f"played preset {num} '{text}'")
            self._run_in_background(lambda: self.api.recall_preset(num))

        elif item_type == "track":
            # Track from track_info (album contents / play queue)
            idx = data.get("index", 0)
            text = data.get("text", "")
            debug_print(f"Playing track from track list: '{text}' idx={idx}")
            self._play_queue_track(idx, text)

        elif item_type == "browse":
            idx = data.get("index", 0)
            attr = data.get("attribute", 0)
            text = data.get("text", "")
            is_container = data.get("is_container", False)
            is_playable = data.get("is_playable", False)
            is_track_list = data.get("is_track_list", False)
            
            debug_print(f"Browse item: '{text}' idx={idx} attr={attr} layer={self.current_menu_layer} "  
                       f"is_container={is_container} is_playable={is_playable} is_track_list={is_track_list}")
            
            # If this is from a track list (even if marked as browse), play it
            if is_track_list:
                debug_print(f"  -> Track from track_list, playing via queue")
                self._play_queue_track(idx, text)
                return

            # In playlist mode, songs, albums and playlists go to the queue instead of playing
            if self.playlist_mode and self._is_queueable(data):
                debug_print(f"  -> Playlist mode, adding to queue")
                self._add_to_playlist(idx, text)
                return

            # Attribute meanings in YXC API:
            # 0 = unplayable/unavailable
            # 1 = playable item (track)
            # 2 = folder/container (navigate into)
            # 10 = search
            # 17 = track in album (from track_info)
            # 66 = artist (container)
            # 82 = album (container - can navigate into to see tracks)
            # Large numbers (>1000) = bitmask with multiple capability bits
            
            # Container attributes - ALWAYS navigate into these
            container_attrs = (2, 10, 66, 82)
            # Track attributes - playable
            track_attrs = (1, 17)
            
            if attr in container_attrs:
                # Definite containers: folder, search, artist, album
                debug_print(f"  -> Container (attr={attr}), navigating into")
                self._navigate_into_item(idx, text)
            elif is_container:
                # Detected as container by _update_browse_widget (e.g., album in Albums menu)
                debug_print(f"  -> Container (is_container=True), navigating into")
                self._navigate_into_item(idx, text)
            elif attr in track_attrs or is_playable:
                # Playable track - play it
                debug_print(f"  -> Playable track (attr={attr}), playing")
                self._play_track(idx, text)
            elif attr > 1000 and is_playable:
                # Bitmask marked as playable
                debug_print(f"  -> Bitmask playable (attr={attr}), playing")
                self._play_track(idx, text)
            elif attr > 0 and not is_container and not is_playable:
                # Unknown positive attribute - use context
                debug_print(f"  -> Unknown attr={attr}, trying navigate")
                self._navigate_into_item(idx, text)
            else:
                # attr == 0 - unplayable/unavailable
                debug_print(f"  -> Unplayable item (attr={attr})")
                self.status_label.setText(f"Cannot play: {text}")

    def _play_track(self, index: int, text: str):
        """Play a track from the current list"""
        if not self.api:
            return
        
        # Temporarily pause polling to avoid resetting view
        self.polling_enabled = False
        self.skip_list_update = True

        self._note_action(f"played '{text}' from the browser")
        self.status_label.setText(f"▶️ Playing: {text}")

        def do_play():
            # Use play_list_item (type="play") to play the track
            result = self.api.play_list_item(index)
            debug_print(f"play_list_item result: {result}")
            # Wait a moment for playback to start
            time.sleep(1.0)
            return result

        def on_played(result):
            # Re-enable polling but keep skip_list_update True
            # to prevent the list from resetting
            self.polling_enabled = True
            # Keep skip_list_update True for a few more poll cycles
            # to let playback stabilize
            QTimer.singleShot(3000, self._allow_list_updates)

        self._run_in_background(do_play, on_played)

    def _play_queue_track(self, index: int, text: str):
        """Play a specific track from the play queue / album track list"""
        if not self.api:
            return
        
        # Keep skip_list_update true to preserve the track list view
        # Don't disable polling - we want status updates
        self.skip_list_update = True
        
        self.status_label.setText(f"▶️ Playing: {text}")
        debug_print(f"Playing queue track index={index} text='{text}'")

        def do_play():
            # Jump to the track in the play queue
            # Index should be 0-based as received from the list
            result = self.api.manage_play_queue("play", index)
            debug_print(f"manage_play_queue('play', {index}) result: {result}")
            time.sleep(0.5)
            return result

        def on_played(result):
            # Keep viewing album tracks mode - don't reset
            # User can use Back or Home to leave album view
            if result and result.get('response_code') == 0:
                debug_print(f"Track {index} playback started successfully")
            else:
                debug_print(f"Track playback may have failed: {result}")

        self._run_in_background(do_play, on_played)

    def _allow_list_updates(self):
        """Re-allow list updates after playback has started"""
        self.skip_list_update = False
        debug_print("List updates re-enabled")

    def _navigate_into_item(self, index: int, text: str):
        """Navigate into a container item (folder, album, artist, etc.)"""
        if not self.api:
            return
        
        # Temporarily stop polling to avoid race conditions
        self.polling_enabled = False
        self.skip_list_update = True  # Prevent polling from overriding our view
        
        # Reset state for new menu
        self.accumulated_items = []
        self.current_menu_hash = ""
        self.list_fully_loaded = False
        self.last_location_hash = ""
        self.viewing_album_tracks = False
        
        # Store current layer to detect if we actually navigated
        original_layer = self.current_menu_layer
        self._note_action(f"opened '{text}'")

        # Show loading state
        self.status_label.setText(f"Opening: {text}...")
        self.list_widget.clear()
        self.list_widget.addItem("⏳ Loading...")

        def do_navigate():
            # Get current queue state before selecting (to detect change)
            old_queue = self.api.get_play_queue()
            old_track_count = len(old_queue.get('track_info', [])) if old_queue else 0
            old_first_track = ""
            if old_queue and old_queue.get('track_info'):
                old_first_track = old_queue['track_info'][0].get('text', '')
            
            # Select the item to navigate into it
            result = self.api.select_list_item(index)
            debug_print(f"select_list_item({index}) result: {result}")
            
            # Wait for receiver to process the navigation/playback
            time.sleep(2.0)  # Longer wait for album to load
            
            # Fetch the browse list
            list_info = self.api.get_list_info(
                input_id=self.current_input, 
                list_id="main", 
                size=8
            )
            
            new_layer = list_info.get('menu_layer', 0) if list_info else 0
            debug_print(f"After select: layer={new_layer}, original_layer={original_layer}, "
                       f"menu_name={list_info.get('menu_name') if list_info else 'N/A'}")
            
            # Check if layer increased (navigated into folder) or stayed/decreased (album played)
            queue_info = None
            navigated_into_folder = new_layer > original_layer
            
            if not navigated_into_folder:
                # Layer didn't increase - the item was likely played (album selected)
                # Try to get play queue which should have the album tracks
                debug_print(f"Layer unchanged/decreased ({original_layer} -> {new_layer}), fetching play queue...")
                
                # Try multiple times with increasing delays
                for attempt in range(8):
                    time.sleep(0.5 + attempt * 0.2)  # Increasing delay
                    queue_info = self.api.get_play_queue()
                    
                    if queue_info and queue_info.get('response_code') == 0:
                        track_info = queue_info.get('track_info', [])
                        track_count = len(track_info)
                        
                        # Check if this is a NEW queue (different from before)
                        new_first_track = track_info[0].get('text', '') if track_info else ""
                        queue_changed = (track_count != old_track_count or 
                                        new_first_track != old_first_track)
                        
                        debug_print(f"Play queue attempt {attempt+1}: {track_count} tracks, "
                                   f"changed={queue_changed}, first='{new_first_track[:30]}...'")
                        
                        if track_count > 0 and queue_changed:
                            debug_print(f"Got new album queue with {track_count} tracks")
                            break
                        elif track_count > 0 and attempt >= 4:
                            # After several attempts, accept what we have
                            debug_print(f"Accepting queue after {attempt+1} attempts")
                            break
            
            return list_info, queue_info, original_layer, navigated_into_folder

        def on_navigated(result):
            list_info, queue_info, orig_layer, navigated_into_folder = result
            
            # First check if we got a play queue with tracks
            if queue_info and queue_info.get('response_code') == 0:
                track_info = queue_info.get('track_info', [])
                if track_info and len(track_info) > 0:
                    debug_print(f"SUCCESS: Showing {len(track_info)} tracks from album")
                    # Mark that we're viewing album tracks - prevents polling from overriding
                    self.viewing_album_tracks = True
                    self.skip_list_update = True  # Keep this true to protect the view
                    self.list_fully_loaded = True  # Prevent progressive loading from interfering
                    self._update_browse_widget(queue_info)
                    # Re-enable polling for status updates only
                    self.polling_enabled = True
                    return
            
            # Check browse list
            if list_info and list_info.get('response_code') == 0:
                new_layer = list_info.get('menu_layer', 0)
                debug_print(f"Navigated to: {list_info.get('menu_name')} layer={new_layer}")
                
                # Show the browse list (we navigated into a folder)
                debug_print(f"Showing browse list (layer {orig_layer} -> {new_layer})")
                self.viewing_album_tracks = False
                self.skip_list_update = False
                self.polling_enabled = True
                self._update_browse_widget(list_info)
            else:
                self.status_label.setText("Failed to open folder")
                self.list_widget.clear()
                self.list_widget.addItem("❌ Could not load contents")
                self.viewing_album_tracks = False
                self.skip_list_update = False
                self.polling_enabled = True

        self._run_in_background(do_navigate, on_navigated)

    # =========================================================================
    # PLAYLIST MODE
    # =========================================================================

    def _toggle_playlist_mode(self, enabled: bool):
        """Show or hide the playlist panel; in playlist mode double-click queues items"""
        self.playlist_mode = enabled
        self.playlist_box.setVisible(enabled)
        self._update_mode_hint()

        if enabled:
            self.width_before_playlist = self.width()
            self.resize(max(self.width(), 1000), self.height())
            self.playlist_signature = None
            self.playlist_texts = None
            self.playlist_removes = 0
            self._set_playlist_warning("")
            self._refresh_playlist()
            # With push events the panel also refreshes on track changes; this is the safety net
            self.playlist_timer.start(10000 if self.events_seen else 4000)
        else:
            self.playlist_timer.stop()
            self.resize(self.width_before_playlist, self.height())

    @staticmethod
    def _is_queueable(data: dict) -> bool:
        """True for navigator items the receiver can add to the queue (tracks, albums, playlists)"""
        return data.get("is_playable", False) or bool(data.get("attribute", 0) & ATTR_ADD_PLAY_QUEUE)

    def _confirm_replace_queue(self, text: str) -> bool:
        """In playlist mode, ask before starting something that replaces the receiver's queue"""
        if not self.playlist_mode or not self.playlist_texts:
            return True
        answer = QMessageBox.question(
            self, "Replace the playlist?",
            f"This starts “{text}” on the receiver, which replaces the playlist "
            f"({len(self.playlist_texts)} tracks).\n\nTo keep the playlist, right-click a song "
            f"in the browser and choose “Play now, keep the playlist”.\n\nStart it anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.playlist_texts = None          # the user chose this; don't report it as a wipe
        return True

    def _show_browse_menu(self, pos):
        """Right-click menu for navigator items"""
        item = self.list_widget.itemAt(pos)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data or data.get("type") != "browse" or data.get("is_track_list"):
            return

        idx = data.get("index", 0)
        text = data.get("text", "")
        menu = QMenu(self)

        if self._is_queueable(data):
            menu.addAction("➕ Add to playlist").triggered.connect(
                lambda: self._add_to_playlist(idx, text))
            menu.addAction("⏭ Play next").triggered.connect(
                lambda: self._add_to_playlist(idx, text, play_next=True))
            menu.addAction("▶ Play now, keep the playlist").triggered.connect(
                lambda: self._play_now_keep_queue(idx, text))
        if data.get("is_playable"):
            menu.addAction("▶ Play (replaces the queue)").triggered.connect(
                lambda: self._confirm_replace_queue(text) and self._play_track(idx, text))
        if data.get("is_container"):
            menu.addSeparator()
            menu.addAction("📂 Open").triggered.connect(
                lambda: self._navigate_into_item(idx, text))

        if menu.actions():
            menu.exec(self.list_widget.viewport().mapToGlobal(pos))

    def _add_to_playlist(self, index: int, text: str, play_next: bool = False):
        """Add a navigator item to the receiver's play queue"""
        if not self.api:
            return

        self.status_label.setText(f"➕ Adding: {text}...")
        self._note_action(f"queued '{text}' to play next" if play_next else f"added '{text}'")
        self._set_playlist_warning("")

        def on_added(result):
            if result and result.get('response_code') == 0:
                self.status_label.setText(f"{'⏭ Plays next' if play_next else '✅ Added'}: {text}")
                QTimer.singleShot(1500, self._refresh_playlist)
            else:
                self.status_label.setText(f"❌ Could not add: {text}")

        operation = "play_next" if play_next else "add_to_queue"
        self._run_in_background(lambda: self.api.manage_list(operation, index), on_added)

    def _play_now_keep_queue(self, index: int, text: str):
        """Play an item right away without losing the playlist:
        insert it after the current track, then jump to it"""
        if not self.api:
            return

        self.status_label.setText(f"▶️ Starting: {text}...")
        self._note_action(f"started '{text}' (keeping the playlist)")
        self._set_playlist_warning("")

        def do_play():
            queue = self.api.get_play_queue(size=200) or {}
            playing = queue.get("playing_index", -1)
            count = len(queue.get("track_info", []))
            if not isinstance(playing, int) or playing < 0:
                result, target = self.api.manage_list("add_to_queue", index), count
            else:
                result, target = self.api.manage_list("play_next", index), playing + 1
            if not result or result.get("response_code") != 0:
                return result
            time.sleep(0.5)
            return self.api.manage_play_queue("play", target)

        def on_done(result):
            if result and result.get("response_code") == 0:
                self.status_label.setText(f"▶️ Playing: {text}")
            else:
                self.status_label.setText(f"❌ Could not start: {text}")
            QTimer.singleShot(800, self._poll_status)
            QTimer.singleShot(1500, self._refresh_playlist)

        self._run_in_background(do_play, on_done)

    def _refresh_playlist(self):
        """Fetch the play queue for the playlist panel"""
        if not self.api or not self.playlist_mode or self.playlist_loading:
            return
        self.playlist_loading = True
        api = self.api

        threading.Thread(
            target=lambda: self.update_playlist_signal.emit(api.get_play_queue(size=200)),
            daemon=True
        ).start()

    def _set_playlist_warning(self, text: str):
        """Show (or clear) the wiped-queue warning above the playlist"""
        self.playlist_warning.setText(text)
        self.playlist_warning.setVisible(bool(text))

    def _check_queue_wiped(self, texts: list) -> str:
        """Compare with the last queue seen. If tracks vanished that this app didn't remove,
        the receiver replaced or cleared the queue: say so, and what was done just before."""
        previous, self.playlist_texts = self.playlist_texts, list(texts)
        if not previous:
            return ""

        vanished = sum((Counter(previous) - Counter(texts)).values())
        explained = min(vanished, self.playlist_removes)
        self.playlist_removes -= explained
        vanished -= explained
        if vanished == 0 or (vanished == 1 and texts):
            return ""

        ago = int(time.monotonic() - self.last_action_time)
        if self.last_action and ago < 120:
            cause = f"Last command from this app: {self.last_action} ({ago} s before)."
        else:
            cause = "No command from this app just before: another app or the receiver did it."
        what = "replaced the playlist" if texts else "cleared the playlist"
        debug_print(f"[Playlist] Receiver {what}: {vanished} tracks gone. {cause}")
        return f"⚠️ The receiver {what} ({vanished} tracks gone). {cause}"

    def _update_playlist_widget(self, data: dict):
        """Show the play queue in the playlist panel, keeping selection and scroll"""
        self.playlist_loading = False
        if not data or data.get('response_code') != 0:
            self.playlist_info.setText("❌ Could not load the queue (receiver busy?), retrying...")
            return

        texts = [t.get('track', t.get('text', '???')) for t in data.get('track_info', [])]
        playing_idx = data.get('playing_index', -1)

        warning = self._check_queue_wiped(texts)
        if warning:
            self._set_playlist_warning(warning)

        info = f"{len(texts)} tracks · double-click a song or album in the browser to add it"
        if not self.profile.get("queue_editing"):
            info += " · queue editing is untested on this model"
        self.playlist_info.setText(info)

        # Rebuild only when something changed, so the selection isn't lost every refresh
        signature = (tuple(texts), playing_idx)
        if signature == self.playlist_signature:
            return
        self.playlist_signature = signature

        selected_row = self.playlist_widget.currentRow()
        scroll_pos = self.playlist_widget.verticalScrollBar().value()
        self.playlist_widget.clear()

        for i, text in enumerate(texts):
            prefix = "▶️" if i == playing_idx else f"{i + 1}."
            w_item = QListWidgetItem(f"{prefix} {text}")
            w_item.setData(Qt.ItemDataRole.UserRole, {"index": i, "text": text})
            if i == playing_idx:
                font = w_item.font()
                font.setBold(True)
                w_item.setFont(font)
            self.playlist_widget.addItem(w_item)

        if not texts:
            self.playlist_widget.addItem("📭 Empty: add songs from the browser")
        elif selected_row >= 0:
            self.playlist_widget.setCurrentRow(min(selected_row, len(texts) - 1))
        self.playlist_widget.verticalScrollBar().setValue(scroll_pos)

    def _play_playlist_item(self, item):
        """Jump playback to a playlist entry"""
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        self._note_action(f"jumped to '{data['text']}'")
        self._run_queue_command("play", data["index"], f"▶️ Playing: {data['text']}")

    def _remove_playlist_item(self):
        """Remove the selected entry from the playlist"""
        item = self.playlist_widget.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        self._note_action(f"removed '{data['text']}'")
        self.playlist_removes += 1
        self._run_queue_command("remove", data["index"], f"🗑 Removed: {data['text']}")

    def _skip_playlist_track(self):
        """Skip to the next track of the playlist"""
        self._transport("next")
        QTimer.singleShot(1500, self._refresh_playlist)

    def _run_queue_command(self, operation: str, index: int, message: str):
        """Run a play queue command in the background, then refresh the playlist"""
        if not self.api:
            return

        def on_done(result):
            if result and result.get('response_code') == 0:
                self.status_label.setText(message)
            else:
                self.status_label.setText(f"❌ Queue command failed: {operation}")
                if operation == "remove":
                    self.playlist_removes = max(0, self.playlist_removes - 1)
            QTimer.singleShot(1500, self._refresh_playlist)

        self._run_in_background(lambda: self.api.manage_play_queue(operation, index), on_done)

    # =========================================================================
    # POLLING AND RECEIVER EVENTS
    # =========================================================================

    def _poll_status(self, list_only: bool = False):
        """Poll the receiver. Only one poll runs at a time, so a slow receiver isn't flooded
        with requests that then delay the user's own commands."""
        if not self.api or not self.polling_enabled or self.poll_in_flight:
            return
        self.poll_in_flight = True

        api = self.api
        input_id = self.current_input
        if self.skip_list_update or self.list_fully_loaded:
            fetch_idx = -1
        else:
            fetch_idx = len(self.accumulated_items)

        def fetch():
            try:
                if list_only and fetch_idx >= 0:
                    result = {"list_info": api.get_list_info(input_id=input_id, index=fetch_idx),
                              "fetch_index": fetch_idx}
                else:
                    result = api.poll_all_status(input_id=input_id, fetch_index=fetch_idx)
            except Exception as e:
                debug_print(f"Poll error: {e}")
                result = {}
            self.update_status_signal.emit(result)

        threading.Thread(target=fetch, daemon=True).start()

    def _on_poll_result(self, data: dict):
        """A poll finished: allow the next one, then apply what it brought"""
        self.poll_in_flight = False
        self._apply_status_update(data)

    def _on_receiver_event(self, event: dict):
        """Apply a change the receiver pushed over UDP (runs on the UI thread)"""
        if not self.api:
            return
        if not self.events_seen:
            # Events work: polling becomes a slow safety net
            self.events_seen = True
            self.poll_timer.setInterval(POLL_MS_WITH_EVENTS)
            if self.playlist_timer.isActive():
                self.playlist_timer.setInterval(10000)
            debug_print("[Event] Push events work, polling slowed down")

        main = event.get("main") or {}
        status = {key: main[key] for key in ("power", "volume", "mute", "input") if key in main}
        if status:
            status["response_code"] = 0
            self._apply_status_update({"status": status})

        netusb = event.get("netusb") or {}
        if "play_time" in netusb:
            self.play_time = netusb["play_time"]
            self._update_time_label()
        if main.get("status_updated") or netusb.get("play_info_updated"):
            self._poll_status()
        if netusb.get("play_info_updated") or netusb.get("play_queue"):
            QTimer.singleShot(500, self._refresh_playlist)

    def _update_time_label(self):
        """Elapsed / total time of the current track"""
        elapsed, total = format_time(self.play_time), format_time(self.total_time)
        if elapsed and total and self.total_time > 0:
            self.time_label.setText(f"⏱ {elapsed} / {total}")
        elif elapsed and self.playback_state != "stop":
            self.time_label.setText(f"⏱ {elapsed}")
        else:
            self.time_label.setText("")

    def _apply_status_update(self, data: dict):
        """Apply polled (or pushed) status updates to UI; any field may be missing"""
        try:
            if not data:
                return

            status = data.get("status")
            play_info = data.get("play_info")
            list_info = data.get("list_info")

            # Update power/volume/mute
            if status and status.get('response_code') == 0:
                if 'power' in status:
                    self.power_state = status['power']
                    self.power_btn.setText(f"Power: {self.power_state.upper()}")
                    self.power_btn.setChecked(self.power_state == "on")

                if isinstance(status.get('volume'), int):
                    self._show_receiver_volume(status['volume'])

                if isinstance(status.get('mute'), bool) and status['mute'] != self.mute_state:
                    self._set_mute_display(status['mute'])

                # Track input changes
                current = status.get('input', '')
                if current and current != self.current_input:
                    self.current_input = current
                    idx = self.input_combo.findText(current)
                    if idx >= 0:
                        self.input_combo.blockSignals(True)
                        self.input_combo.setCurrentIndex(idx)
                        self.input_combo.blockSignals(False)

            # Update now playing
            if play_info and play_info.get('response_code') == 0:
                track = play_info.get('track') or '-'
                artist = play_info.get('artist') or '-'
                album = play_info.get('album') or '-'
                playback = play_info.get('playback', 'stop')
                self._show_playback_state(playback)

                prefix = "▶️" if playback == "play" else "⏸️" if playback == "pause" else ""
                self.track_label.setText(f"{prefix} Track: {track}")
                self.artist_label.setText(f"Artist: {artist}")
                self.album_label.setText(f"Album: {album}")
                self.play_time = play_info.get('play_time')
                self.total_time = play_info.get('total_time')
                self._update_time_label()

            # Update browse list
            if list_info and list_info.get('response_code') == 0:
                if self.skip_list_update:
                    return

                menu_hash = f"{list_info.get('menu_layer')}_{list_info.get('menu_name')}"
                fetch_idx = data.get('fetch_index', 0)

                if menu_hash != self.current_menu_hash:
                    self.current_menu_hash = menu_hash
                    self.accumulated_items = []
                    self.total_items = list_info.get('max_line', 0)
                    self.list_fully_loaded = False

                new_items = list_info.get('list_info', [])

                if fetch_idx == 0:
                    self.accumulated_items = new_items
                else:
                    if fetch_idx >= len(self.accumulated_items):
                        self.accumulated_items.extend(new_items)

                self.total_items = list_info.get('max_line', len(self.accumulated_items))

                if len(self.accumulated_items) >= self.total_items:
                    if self.list_fully_loaded:
                        return
                    self.list_fully_loaded = True
                elif new_items:
                    # More pages to come (the receiver sends 8 items at a time): fetch the
                    # next one right away instead of waiting for the next poll
                    QTimer.singleShot(50, lambda: self._poll_status(list_only=True))

                display_hash = f"{menu_hash}_{len(self.accumulated_items)}"

                if display_hash != self.last_location_hash:
                    is_progressive = (
                        menu_hash == self.current_menu_hash and 
                        len(self.accumulated_items) > 8
                    )
                    self.last_location_hash = display_hash

                    accumulated_data = dict(list_info)
                    accumulated_data['list_info'] = self.accumulated_items
                    accumulated_data['index'] = 0
                    self._update_browse_widget(accumulated_data, preserve_scroll=is_progressive)

        except Exception as e:
            debug_print(f"Status update error: {e}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DesktopCastApp()
    window.show()
    sys.exit(app.exec())

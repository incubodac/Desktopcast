"""
Desktop Yamaha Receiver Tidal Navigator
A PyQt6 application for controlling Yamaha receivers with MusicCast/YXC API
"""

import sys
import time
import threading
import requests

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QListWidget, QListWidgetItem,
    QComboBox, QMessageBox, QGroupBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSize, QUrl
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

# Default receiver IP address
DEFAULT_IP = "192.168.0.248"

# Enable/disable debug output
DEBUG = False


def debug_print(*args, **kwargs):
    """Print debug messages if DEBUG is enabled"""
    if DEBUG:
        print(*args, **kwargs)


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

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """Make a GET request to the API"""
        try:
            url = f"{self.base_url}{endpoint}"
            debug_print(f"[API] GET {endpoint} params={params}")
            response = requests.get(url, params=params, timeout=self.timeout)
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

    def manage_play(self, operation: str, index: int = -1) -> dict:
        """Manage playback queue"""
        params = {"type": operation, "index": index, "zone": self.zone}
        return self._get("/netusb/managePlay", params)

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
            response = requests.post(url, json=payload, timeout=10)
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
        result = self.func(*self.args, **self.kwargs)
        self.finished.emit(result)


# =============================================================================
# MAIN APPLICATION
# =============================================================================

class MusicCastApp(QMainWindow):
    """Main application window"""
    
    # Signals for thread-safe UI updates
    update_browse_signal = pyqtSignal(object)
    update_status_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Desktopcast")
        self.resize(600, 800)
        
        # State
        self.api = None
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
        
        # Thumbnail handling
        self.thumbnail_cache = {}
        self.network_manager = QNetworkAccessManager()
        self.network_manager.finished.connect(self._on_thumbnail_loaded)
        self.pending_thumbnails = {}
        
        # Keep worker references to prevent garbage collection
        self.workers = []

        # Connect signals
        self.update_browse_signal.connect(self._update_browse_widget)
        self.update_status_signal.connect(self._apply_status_update)

        # Build UI
        self._setup_ui()

        # Polling timer
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self._poll_status)

        # Start with controls disabled
        self._enable_controls(False)

    def _setup_ui(self):
        """Build the user interface"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Connection Section ---
        conn_box = QGroupBox("Connection")
        conn_layout = QHBoxLayout()
        
        self.ip_input = QLineEdit(DEFAULT_IP)
        self.ip_input.setPlaceholderText("Receiver IP (e.g., 192.168.1.50)")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._connect_device)
        
        conn_layout.addWidget(QLabel("IP:"))
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.connect_btn)
        conn_box.setLayout(conn_layout)
        layout.addWidget(conn_box)

        # --- Basic Controls ---
        control_box = QGroupBox("Basic Control")
        control_layout = QHBoxLayout()
        
        self.power_btn = QPushButton("Power: ?")
        self.power_btn.setCheckable(True)
        self.power_btn.clicked.connect(self._toggle_power)
        
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.sliderReleased.connect(self._change_volume)
        
        control_layout.addWidget(self.power_btn)
        control_layout.addWidget(QLabel("Vol:"))
        control_layout.addWidget(self.vol_slider)
        control_box.setLayout(control_layout)
        layout.addWidget(control_box)

        # --- Input & Navigation ---
        nav_box = QGroupBox("Input & Navigation")
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
        self.queue_btn = QPushButton("📋 Queue")
        self.queue_btn.clicked.connect(self._show_queue)
        self.presets_btn = QPushButton("⭐ Presets")
        self.presets_btn.clicked.connect(self._show_presets)
        self.save_preset_btn = QPushButton("💾 Save")
        self.save_preset_btn.clicked.connect(self._save_preset)
        
        nav_btn_layout.addWidget(self.recent_btn)
        nav_btn_layout.addWidget(self.queue_btn)
        nav_btn_layout.addWidget(self.presets_btn)
        nav_btn_layout.addWidget(self.save_preset_btn)

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

        # Status label
        self.status_label = QLabel("Not connected")

        # List widget
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(64, 64))
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_click)

        nav_layout.addLayout(input_layout)
        nav_layout.addLayout(nav_btn_layout)
        nav_layout.addLayout(browse_btn_layout)
        nav_layout.addLayout(search_layout)
        nav_layout.addWidget(self.status_label)
        nav_layout.addWidget(self.list_widget)
        nav_box.setLayout(nav_layout)
        layout.addWidget(nav_box)

        # --- Now Playing ---
        np_box = QGroupBox("Now Playing")
        np_layout = QVBoxLayout()
        
        self.track_label = QLabel("Track: -")
        self.artist_label = QLabel("Artist: -")
        self.album_label = QLabel("Album: -")

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
        np_layout.addLayout(trans_layout)
        np_box.setLayout(np_layout)
        layout.addWidget(np_box)

    def _enable_controls(self, enable: bool):
        """Enable or disable all controls"""
        controls = [
            self.power_btn, self.vol_slider, self.input_combo, self.set_input_btn,
            self.list_widget, self.recent_btn, self.queue_btn, self.presets_btn,
            self.save_preset_btn, self.home_btn, self.refresh_btn,
            self.prev_btn, self.play_btn, self.next_btn, self.search_btn
        ]
        for ctrl in controls:
            ctrl.setEnabled(enable)

    # =========================================================================
    # CONNECTION
    # =========================================================================

    def _connect_device(self):
        """Connect to the Yamaha receiver"""
        ip = self.ip_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "Warning", "Please enter an IP address")
            return

        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Connecting...")

        def do_connect():
            api = YamahaAPI(ip)
            features = api.get_features()
            if not features:
                return (None, None, None, None, None, None)
            
            services = api.get_service_info()
            status = api.get_status()
            current_input = "tidal"
            if status and status.get('response_code') == 0:
                current_input = status.get('input', 'tidal')
            
            list_info = api.get_list_info(input_id=current_input, list_id="main", size=8)
            return (api, features, services, status, list_info, current_input)

        def on_connected(result):
            api, features, services, status, list_info, current_input = result
            
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("Connect")

            # Check if connection was successful
            if features and features.get('response_code') == 0:
                self.api = api
                self._enable_controls(True)
                
                # Update input combo if we got service info
                if services and services.get('response_code') == 0:
                    service_list = services.get('service_list', [])
                    if service_list:
                        self.input_combo.clear()
                        for svc in service_list:
                            if isinstance(svc, dict):
                                if svc.get('enable', True):
                                    name = svc.get('id') or svc.get('input', '')
                                    if name:
                                        self.input_combo.addItem(name)
                            elif isinstance(svc, str):
                                self.input_combo.addItem(svc)

                # Set current input
                self.current_input = current_input
                idx = self.input_combo.findText(current_input)
                if idx >= 0:
                    self.input_combo.setCurrentIndex(idx)

                # Update browse list
                if list_info and list_info.get('response_code') == 0:
                    self._update_browse_widget(list_info)

                # Start polling
                self.poll_timer.start(1000)
                
                system_id = features.get('system', {}).get('id', 'Unknown')
                QMessageBox.information(self, "Connected", 
                    f"Connected to Yamaha receiver\nSystem: {system_id}")
            else:
                QMessageBox.critical(self, "Error", 
                    "Could not connect to Yamaha receiver.\n"
                    "Check IP address and network.")

        worker = Worker(do_connect)
        worker.finished.connect(on_connected)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    # =========================================================================
    # BASIC CONTROLS
    # =========================================================================

    def _toggle_power(self):
        """Toggle power state"""
        if not self.api:
            return
        is_on = self.power_btn.text() == "Power: ON"
        self.power_btn.setText("Power: ...")
        threading.Thread(
            target=lambda: self.api.set_power(not is_on), 
            daemon=True
        ).start()

    def _change_volume(self):
        """Change volume"""
        if not self.api:
            return
        val = self.vol_slider.value()
        threading.Thread(
            target=lambda: self.api.set_volume(val), 
            daemon=True
        ).start()

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

        worker = Worker(switch_input)
        worker.finished.connect(on_switched)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    def _transport(self, cmd: str):
        """Transport control"""
        if not self.api:
            return
        if cmd == "play_pause":
            cmd = "pause" if self.playback_state == "play" else "play"
        threading.Thread(
            target=lambda: self.api.set_playback(cmd), 
            daemon=True
        ).start()

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

        worker = Worker(fetch)
        worker.finished.connect(update)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    def _show_queue(self):
        """Show play queue"""
        if not self.api:
            return
        self.status_label.setText("View: 📋 Play Queue")
        self.current_view = "queue"

        def fetch():
            return self.api.get_play_queue()

        def update(data):
            self.list_widget.clear()
            if not data or data.get('response_code') != 0:
                self.list_widget.addItem("❌ Could not load play queue")
                return
            
            items = data.get('track_info', [])
            playing_idx = data.get('playing_index', -1)
            
            for i, item in enumerate(items):
                track = item.get('track', '???')
                artist = item.get('artist', '')
                prefix = "▶️ " if i == playing_idx else "   "
                display = f"{prefix}{track}"
                if artist:
                    display += f" - {artist}"
                
                w_item = QListWidgetItem(display)
                w_item.setData(Qt.ItemDataRole.UserRole, {
                    "type": "queue", "index": i, "data": item
                })
                self.list_widget.addItem(w_item)
            
            if not items:
                self.list_widget.addItem("📭 Queue is empty")

        worker = Worker(fetch)
        worker.finished.connect(update)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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

        worker = Worker(fetch)
        worker.finished.connect(update)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    def _save_preset(self):
        """Save current playback as preset"""
        if not self.api:
            return

        def do_save():
            info = self.api.get_preset_info()
            if info and info.get('response_code') == 0:
                presets = info.get('preset_info', [])
                for i, p in enumerate(presets):
                    if not p.get('text'):
                        return self.api.store_preset(i + 1)
                return self.api.store_preset(40)
            return None

        threading.Thread(target=do_save, daemon=True).start()
        QMessageBox.information(self, "Preset", "Saving current track to presets...")

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

        worker = Worker(do_home)
        worker.finished.connect(on_home)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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

        worker = Worker(do_back)
        worker.finished.connect(on_back)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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
            # Fetch both browse list and play queue
            list_info = self.api.get_list_info(
                input_id=self.current_input, 
                list_id="main", 
                size=8,
                index=0
            )
            queue_info = self.api.get_play_queue()
            return list_info, queue_info
        
        def on_refreshed(result):
            list_info, queue_info = result
            
            # If we have a play queue with tracks (album contents), prefer showing that
            if queue_info and queue_info.get('response_code') == 0:
                track_info = queue_info.get('track_info', [])
                if track_info and len(track_info) > 0:
                    debug_print(f"[Refresh] Showing play queue with {len(track_info)} tracks")
                    self._update_browse_widget(queue_info)
                    return
            
            # Otherwise show the browse list
            if list_info and list_info.get('response_code') == 0:
                self._update_browse_widget(list_info)
            else:
                self.status_label.setText("❌ Refresh failed")
        
        worker = Worker(do_refresh)
        worker.finished.connect(on_refreshed)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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

        worker = Worker(search)
        worker.finished.connect(on_search)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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
            debug_print(f"Playing recent item {idx}")
            threading.Thread(
                target=lambda: self.api.play_recent(idx), 
                daemon=True
            ).start()

        elif item_type == "queue":
            idx = data.get("index", 0)
            debug_print(f"Playing queue item {idx}")
            threading.Thread(
                target=lambda: self.api.manage_play("play", idx), 
                daemon=True
            ).start()

        elif item_type == "preset":
            num = data.get("num", 1)
            debug_print(f"Playing preset {num}")
            threading.Thread(
                target=lambda: self.api.recall_preset(num), 
                daemon=True
            ).start()

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

        worker = Worker(do_play)
        worker.finished.connect(on_played)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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
            # Use manage_play with type="play" and index to play specific track
            # Index should be 0-based as received from the list
            result = self.api.manage_play("play", index)
            debug_print(f"manage_play('play', {index}) result: {result}")
            time.sleep(0.5)
            return result

        def on_played(result):
            # Keep viewing album tracks mode - don't reset
            # User can use Back or Home to leave album view
            if result and result.get('response_code') == 0:
                debug_print(f"Track {index} playback started successfully")
            else:
                debug_print(f"Track playback may have failed: {result}")

        worker = Worker(do_play)
        worker.finished.connect(on_played)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

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
                old_first_track = old_queue['track_info'][0].get('track', '')
            
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
                        new_first_track = track_info[0].get('track', '') if track_info else ""
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

        worker = Worker(do_navigate)
        worker.finished.connect(on_navigated)
        self.workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    # =========================================================================
    # POLLING
    # =========================================================================

    def _poll_status(self):
        """Poll all status endpoints"""
        if not self.api or not self.polling_enabled:
            return

        def fetch():
            # If skipping list updates or list is fully loaded, don't fetch list
            if self.skip_list_update or self.list_fully_loaded:
                return self.api.poll_all_status(
                    input_id=self.current_input, fetch_index=-1
                )
            
            fetch_idx = len(self.accumulated_items) if self.accumulated_items else 0
            return self.api.poll_all_status(
                input_id=self.current_input, fetch_index=fetch_idx
            )

        threading.Thread(
            target=lambda: self.update_status_signal.emit(fetch()), 
            daemon=True
        ).start()

    def _apply_status_update(self, data: dict):
        """Apply polled status updates to UI"""
        try:
            if not data:
                return

            status = data.get("status")
            play_info = data.get("play_info")
            list_info = data.get("list_info")

            # Update power/volume
            if status and status.get('response_code') == 0:
                power = status.get('power', 'unknown')
                self.power_btn.setText(f"Power: {power.upper()}")
                self.power_btn.setChecked(power == "on")

                vol = status.get('volume')
                if isinstance(vol, int) and not self.vol_slider.isSliderDown():
                    self.vol_slider.setValue(vol)

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
                track = play_info.get('track', '-')
                artist = play_info.get('artist', '-')
                album = play_info.get('album', '-')
                playback = play_info.get('playback', 'stop')
                self.playback_state = playback

                prefix = "▶️" if playback == "play" else "⏸️" if playback == "pause" else ""
                self.track_label.setText(f"{prefix} Track: {track}")
                self.artist_label.setText(f"Artist: {artist}")
                self.album_label.setText(f"Album: {album}")

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
    window = MusicCastApp()
    window.show()
    sys.exit(app.exec())

import sys
import os
from PIL import Image, ImageOps
from PySide6.QtWidgets import QListWidgetItem
import threading
from PySide6.QtWidgets import QScrollArea
# Attach signal handler for SIGTRAP
import pynput.keyboard as keyboard
from PySide6.QtWidgets import QMessageBox
# At the top of your file
from contextlib import contextmanager
import json
@contextmanager
def suppress_stderr():
    """Temporarily suppress stderr (e.g., FFmpeg logs from QMediaPlayer)."""
    with open(os.devnull, "w") as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr

from pathlib import Path
import sys

def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        app_root = Path(sys.executable).resolve().parent.parent
        base_path = app_root / "Resources"
    else:
        base_path = Path(__file__).resolve().parent

    full_path = base_path / relative_path

    print("RESOURCE DEBUG:")
    print("BASE:", base_path)
    print("LOOKING FOR:", full_path)
    print("EXISTS:", full_path.exists())

    return str(full_path)
import random
from typing import TYPE_CHECKING, Optional

# ---------------- Global media keys ----------------
try:
    import pynput.keyboard as _keyboard  # runtime import only
    keyboard_available = True
except ImportError:
    _keyboard = None      # define runtime alias even if import fails
    keyboard_available = False
    print("Warning: pynput not installed. Global media keys disabled.")

if TYPE_CHECKING:
    import pynput.keyboard as kb
    keyboard: Optional[kb]

# ---------------- PySide6 imports ----------------
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QLabel, QListWidget, QLineEdit,
    QTabWidget, QMenu, QInputDialog, QDialog, QCheckBox,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.multimedia.*=false"
os.environ["QT_MEDIA_LOGGING"] = "0"

from PySide6.QtCore import Qt, QUrl, QSettings, QTimer
from PySide6.QtGui import QPixmap, QIcon  # <- QPixmap for images, QIcon for window/app icon
from PySide6.QtWidgets import QMainWindow


class MusicPlayer(QMainWindow):
    def update_remove_playlist_btn_state(self):
        tabs = self.safe_getattr("tabs", None)
        if tabs:
            self.remove_playlist_btn.setEnabled(tabs.count() > 1)

    def manage_hidden_playlists(self):
        """Show a dialog for managing hidden playlists."""
        hidden_tabs = self.safe_getattr("_hidden_tabs", {})
        if not hidden_tabs:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Hidden Playlists")
        layout = QVBoxLayout(dialog)

        items = []  # List of checkboxes for hidden playlists

        # Populate the checkboxes for each hidden playlist
        for playlist_name, (widget, name) in self._hidden_tabs.items():
            checkbox = QCheckBox(name, dialog)
            items.append((playlist_name, checkbox))

        restore_button = QPushButton("Restore Selected", dialog)
        layout.addWidget(restore_button)

        # Pass the items to the restore_hidden_playlists method
        restore_button.clicked.connect(lambda: self.restore_hidden_playlists(items))

        dialog.exec()

    def restore_hidden_playlists(self, items):
        """Restore hidden playlists based on the checked checkboxes."""
        to_restore = [name for name, cb in items if cb.isChecked()]

        for name in to_restore:
            restored_widget, restored_name = self._hidden_tabs.pop(name, (None, None))
            if restored_widget:
                self.tabs.addTab(restored_widget, restored_name)

        # Save the updated hidden tabs in settings
        self.settings.setValue("hidden_tabs", self._hidden_tabs)

    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer(self)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.main_layout = QVBoxLayout()
        self.central_widget.setLayout(self.main_layout)
        from PySide6.QtWidgets import QProgressBar
        from PySide6.QtCore import QTimer

        # Initialize a read-only progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)  # 0% to 100%
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.main_layout.addWidget(self.progress_bar)

        # Timer to update progress
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self.safe_update_progress_bar)
        self._progress_timer.start(200)  # update every 200ms

        # Track current song duration
        self._current_duration = 0

        # ---------------- Central Widget & Layout ----------------
        self.setWindowIcon(QIcon(resource_path("assets/icon.png")))
        self.slider_timer = None
        # ---------------- Core Attributes ----------------
        self.playlists: dict[str, list[dict]] = getattr(self, "playlists", {})
        self._hidden_tabs: dict[str, tuple[QWidget, str]] = getattr(self, "_hidden_tabs", {})
        self.now_playing_queue: list[int] = []
        self.added_songs: list[dict] = []  # Stores song info (path, name, etc.)
        self.current_index: int = -1
        self.queue_position: int = -1
        self.is_playing: bool = False
        self.queue_mode: str = "normal"
        self.key_bindings: dict[str, str] = {}
        self.listener: keyboard.Listener | None = None
        self.listener_thread: threading.Thread | None = None
        self._closing: bool = False

        # ---------------- Media Player ----------------

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)
        self.player.durationChanged.connect(self.set_duration)
        self.setup_player_signals()
        # ---------------- UI Elements ----------------
        self.cover = QLabel()
        self.cover.setPixmap(QPixmap(resource_path("assets/default_cover.png")))
        self.cover_name_label = QLabel("Album")
        self.cover_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_name_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #333;")

        print("ICON EXISTS:", os.path.exists(resource_path("assets/icon.png")))
        self.cover.setFixedSize(300, 300)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet("border: 1px solid gray;")

        self.song_label = QLabel("No song loaded")
        self.song_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.slider_is_pressed = False


        self.song_search = QLineEdit()
        self.song_search.setPlaceholderText("Search songs in playlist...")
        self.song_search.textChanged.connect(self.filter_songs)

        self.playlist_search = QLineEdit()
        self.playlist_search.setPlaceholderText("Search playlists...")
        self.playlist_search.textChanged.connect(self.filter_playlists)

        # ---------------- Buttons ----------------
        self.add_btn = QPushButton("Add Songs")
        self.remove_btn = QPushButton("Remove Song")
        self.remove_btn.setEnabled(False)
        self.play_btn = QPushButton("Play")
        self.prev_btn = QPushButton("Previous")
        self.next_btn = QPushButton("Next")
        self.bind_keys_btn = QPushButton("Set Global Keys")
        self.add_playlist_btn = QPushButton("New Playlist")
        self.remove_playlist_btn = QPushButton("Remove Playlist")
        self.remove_playlist_btn.setEnabled(False)
        self.clear_data_btn = QPushButton("Clear All Data")
        self.clear_playlist_btn = QPushButton("Clear Playlist")
        self.shuffle_btn = QPushButton("Shuffle Order")
        self.help_btn = QPushButton("How to Use")
        if sys.platform == "darwin":
            self.bind_keys_btn.setEnabled(False)
            self.bind_keys_btn.setToolTip(
                "Global media keys are disabled on macOS."
            )

        self.rename_song_btn = QPushButton("Rename Song")
        self.rename_playlist_btn = QPushButton("Rename Playlist")
        self.replace_cover_btn = QPushButton("Replace Cover")

        # ---------------- Tabs ----------------
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.update_remove_playlist_btn_state)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)

        # ---------------- Added Songs & Now Playing ----------------
        self.added_songs_label = QLabel("Added Songs")
        self.added_songs_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.added_songs_widget = QListWidget()
        self.added_songs_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.added_songs_widget.setMinimumHeight(250)

        self.queue_label = QLabel("Now Playing")
        self.queue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_widget = QListWidget()
        self.queue_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.queue_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.queue_widget.setMinimumHeight(250)

        # ---------------- Settings ----------------
        self.settings = QSettings("YourName", "AdvancedMusicPlayer")

        raw_bindings = self.settings.value(
            "key_bindings",
            {"play_pause": "f8", "next": "f9", "prev": "f7", "shuffle": "f10"}
        )
        if not isinstance(raw_bindings, dict):
            raw_bindings = {"play_pause": "f8", "next": "f9", "prev": "f7", "shuffle": "f10"}
        self.key_bindings = raw_bindings

        raw_playlists = self.settings.value("playlists", "{}")
        if isinstance(raw_playlists, str):
            try:
                self.playlists = json.loads(raw_playlists)
            except json.JSONDecodeError:
                self.playlists = {}
        elif isinstance(raw_playlists, dict):
            self.playlists = raw_playlists
        else:
            self.playlists = {}
        self._last_known_position = 0
        # ---------------- Add Playlists to Tabs ----------------
        if not self.playlists:
            self.add_playlist("Default")
        else:
            for name in self.playlists.keys():
                self.add_playlist(name)
        self._position_update_timer = None
        # ---------------- Layouts ----------------
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.cover_name_label)
        top_layout.addWidget(self.cover)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.song_label)
        right_layout.addWidget(self.playlist_search)
        right_layout.addWidget(self.song_search)

        playlist_buttons_layout = QVBoxLayout()

        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        row3 = QHBoxLayout()

        for btn in [
            self.add_btn,
            self.remove_btn,
            self.add_playlist_btn,
            self.remove_playlist_btn
        ]:
            row1.addWidget(btn)

        for btn in [
            self.rename_song_btn,
            self.rename_playlist_btn,
            self.replace_cover_btn
        ]:
            row2.addWidget(btn)

        for btn in [
            self.clear_data_btn,
            self.clear_playlist_btn,
            self.shuffle_btn
        ]:
            row3.addWidget(btn)

        playlist_buttons_layout.addLayout(row1)
        playlist_buttons_layout.addLayout(row2)
        playlist_buttons_layout.addLayout(row3)
        # ---------------- Add Up/Down Buttons ----------------


        right_layout.addLayout(playlist_buttons_layout)
        lists_layout = QHBoxLayout()

        # Left side - Now Playing
        queue_layout = QVBoxLayout()
        queue_layout.addWidget(self.queue_label)
        queue_layout.addWidget(self.queue_widget)

        # Right side - Added Songs
        songs_layout = QVBoxLayout()
        songs_layout.addWidget(self.added_songs_label)
        songs_layout.addWidget(self.added_songs_widget)

        lists_layout.addLayout(queue_layout)
        lists_layout.addLayout(songs_layout)

        lists_layout.setStretch(0, 1)
        lists_layout.setStretch(1, 1)

        right_layout.addLayout(lists_layout)
        self.added_songs_widget.currentRowChanged.connect(
            lambda row: self.remove_btn.setEnabled(row >= 0)
        )
        controls_layout = QHBoxLayout()
        for btn in [self.prev_btn, self.play_btn, self.next_btn, self.bind_keys_btn, self.help_btn]:
            controls_layout.addWidget(btn)
        right_layout.addLayout(controls_layout)

        top_layout.addLayout(right_layout)
        self.main_layout.addLayout(top_layout)
        self.main_layout.addWidget(self.tabs)

        # ---------------- Signals ----------------
        self.add_btn.clicked.connect(self.add_songs)
        self.remove_btn.clicked.connect(self.remove_selected_song)
        self.play_btn.clicked.connect(self.play_pause)
        self.next_btn.clicked.connect(self.next_song)
        self.prev_btn.clicked.connect(self.prev_song)
        self.bind_keys_btn.clicked.connect(self.set_global_keys)
        self.add_playlist_btn.clicked.connect(self.create_new_playlist)
        self.remove_playlist_btn.clicked.connect(self.remove_current_playlist)
        self.clear_data_btn.clicked.connect(self.clear_all_data)
        self.clear_playlist_btn.clicked.connect(self.clear_current_playlist)
        self.shuffle_btn.clicked.connect(self.shuffle_now_playing)
        self.help_btn.clicked.connect(self.show_instructions)
        self.rename_song_btn.clicked.connect(self.rename_selected_song)
        self.rename_playlist_btn.clicked.connect(self.rename_current_playlist)
        self.replace_cover_btn.clicked.connect(self.replace_song_cover)

        # ---------------- Tab Context Menu ----------------
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.tab_menu)

        # ---------------- Optional: Start Global Listener ----------------
        self.start_global_listener()


    # ---------------- Slider Handlers ----------------
    def set_global_keys(self):
        if sys.platform == "darwin":
            QMessageBox.information(
                self,
                "Global Keys",
                "Global keyboard shortcuts are disabled on macOS to prevent crashes.\n\n"
                "Windows and Linux versions can use global media keys."
            )
        else:
            QMessageBox.information(
                self,
                "Global Keys",
                "Global keyboard shortcuts are currently enabled."
            )
    def on_tab_changed(self, index):
        self.refresh_added_songs()

        playlist = self.current_playlist_data()

        if not playlist:
            self.now_playing_queue = []
            self.current_index = -1
            self.queue_position = -1
            self.song_label.setText("No song loaded")
            return

        self.now_playing_queue = list(range(len(playlist)))

        self.queue_position = 0
        self.current_index = self.now_playing_queue[0]

        self.load_song()

    def fix_image_orientation(self, path):
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img.save(path)
        except Exception as e:
            print("Image fix error:", e)

    def on_duration_changed(self, duration):
        """Optional: reset progress when a new song is loaded."""
        self.progress_bar.setValue(0)

    def safe_set_progress(self, value: int):
        pb = getattr(self, "progress_bar", None)
        if pb is not None and not pb.signalsBlocked() and pb.parent() is not None:
            try:
                pb.setValue(value)
            except RuntimeError:
                pass

    def safe_getattr(self, name, default=None):
        return getattr(self, name, default)

    def set_cover(self, cover_path):
        """Set the cover image and display its name."""
        if os.path.exists(cover_path):
            pixmap = QPixmap(cover_path)
            self.cover.setPixmap(pixmap.scaled(300, 300, Qt.AspectRatioMode.KeepAspectRatio))

            # Get the name of the cover (you could also extract the album name, if you want)
            cover_name = os.path.basename(cover_path)  # Or any custom logic to get the album name
            self.cover_name_label.setText(cover_name)  # Update cover name label
        else:
            self.cover.setPixmap(QPixmap())  # Clear cover if not available
            self.cover_name_label.setText("Album")  # Default text


    # ---------------- Tab visibility helper ----------------
    def set_tab_visible(self, index: int, visible: bool):
        """
        Show or hide a tab by index.
        Compatible with Qt < 6.4 and >= 6.4.
        """
        if hasattr(self.tabs, "setTabVisible"):
            self.tabs.setTabVisible(index, visible)
            return

        # Qt < 6.4 fallback
        if visible:
            # cannot restore reliably by index anymore
            # must be handled via restore_hidden_playlists
            return
        else:
            self.hide_playlist(index)



    def clear_queue(self):
        self.now_playing_queue.clear()
        self.queue_mode = "normal"
        self.refresh_queue_ui()

    def build_shuffle_queue(self):
        """Build a shuffle queue from the current playlist safely."""
        try:
            playlist = self.current_playlist_data()
            if not playlist:
                self.now_playing_queue = []
                return

            indices = list(range(len(playlist)))
            random.shuffle(indices)
            self.now_playing_queue = indices

            self.refresh_queue_ui()
        except Exception as e:
            print("Error in build_shuffle_queue:", e)

    def show_instructions(self):
        instructions = """
        🎵 Advanced Music Player v1.0 — How to Use
        App Co-Created using ChatGPT
        ────────────────────────────
        ▶ PLAYING MUSIC
        ────────────────────────────
        • Click "Add Songs" to import audio files (MP3, WAV, OGG, FLAC)
        • Select a playlist and press Play to play the songs under Now Playing
        • Use Next / Previous to navigate through songs
        • The progress bar shows playback progress
        • Use "Remove Song" to remove the selected song under Added Songs
        • Rename songs under added songs using the "Rename Song" button

        ────────────────────────────
        📁 PLAYLISTS
        ────────────────────────────
        • Create playlists using "New Playlist"
        • Switch between playlists using tabs
        • Drag and drop songs under Added Songs to reorder them
        • Rename your currently selected playlist using the "Rename Playlist Button"
        • Remove playlists using "Remove Playlist"
        • Clear playlists using "Clear Playlist"

        ────────────────────────────
        🎶 NOW PLAYING QUEUE
        ────────────────────────────
        • Songs are automatically added to the queue
        • Shuffle creates a randomized playback order
        • Playback continues automatically through the queue
        • When the last song ends, playback loops to the first song

        ────────────────────────────
        🔍 SEARCH
        ────────────────────────────
        • Search songs in the current playlist
        • Search playlists by name

        ────────────────────────────
        🎹 CONTROLS
        ────────────────────────────
        • Play / Pause → Toggle playback
        • Next / Previous → Skip tracks
        • Shuffle → Randomize queue

        ────────────────────────────
        🖼️ COVER ART
        ────────────────────────────
        • Each song can have custom cover art
        • Click a song → "Replace Cover" button
        • Default cover is used if none is set

        ────────────────────────────
        🌎 GLOBAL KEYS
        ────────────────────────────
        • Global keys are used to access the play/pause, next, previous, and shuffle.
        • Due to issues on macOS, this feature is only available for Windows/Linux users. 
                
        ────────────────────────────
        💾 DATA SAVING/DELETING
        ────────────────────────────
        • Use the "Clear All Data Button" to remove all playlists and songs
        • Playlists and settings are saved automatically
        • Changes persist when you reopen the app

        Enjoy your music 🎶
        """

        QInputDialog.getMultiLineText(
            self,
            "How to Use Advanced Music Player",
            "Instructions:",
            instructions
        )

    def move_song(self, direction: int):
        row = self.added_songs_widget.currentRow()
        if row < 0:
            return

        name = self.current_playlist_name()
        playlist = self.playlists.get(name, [])

        new_row = row + direction
        if 0 <= new_row < len(playlist):
            playlist[row], playlist[new_row] = playlist[new_row], playlist[row]
            self.refresh_added_songs()
            self.added_songs_widget.setCurrentRow(new_row)

    def clear_current_playlist(self):
        name = self.current_playlist_name()
        text, ok = QInputDialog.getText(
            self, "Clear Playlist",
            f"Type CLEAR to empty playlist '{name}':"
        )
        if not ok or text.strip().upper() != "CLEAR":
            return

        self.playlists[name].clear()

        self.current_playlist_widget().clear()
        self.added_songs_widget.clear()
        self.added_songs = []

        self.queue_widget.clear()
        self.now_playing_queue = []

        self.player.stop()
        self.current_index = -1
        self.queue_position = -1
        self.song_label.setText("No song loaded")

        self.settings.setValue(
            "playlists",
            json.dumps(self.playlists)
        )

    def clear_all_data(self):
        text, ok = QInputDialog.getText(
            self,
            "Clear All Data",
            "This will permanently delete all playlists, songs, and saved settings.\n\n"
            "Type CLEAR to continue:"
        )

        if not ok or text.strip().upper() != "CLEAR":
            return

        try:
            # Reset playlists
            self.playlists = {"Default": []}

            self.tabs.blockSignals(True)
            self.tabs.clear()
            self.tabs.blockSignals(False)

            self.add_playlist("Default")
            self.tabs.setCurrentIndex(0)

            # Stop playback state
            self.player.stop()
            self.player.setSource(QUrl())

            self.current_index = -1
            self.queue_position = -1
            self.now_playing_queue = []
            self.is_playing = False
            self.play_btn.setText("Play")

            # Reset UI
            self.song_label.setText("No song loaded")
            self.cover.setPixmap(
                QPixmap(resource_path("assets/default_cover.png"))
            )

            # Reset progress bar
            if hasattr(self, "progress_bar"):
                self.progress_bar.setValue(0)

            # Reset lists
            self.added_songs_widget.clear()
            self.queue_widget.clear()

            self.added_songs = []

            # Save clean state
            self.settings.setValue(
                "playlists",
                json.dumps(self.playlists)
            )

        except Exception as e:
            print("Error in clear_all_data:", e)
    def set_duration(self, duration_ms):
        """No-op, progress bar is 0–100% scale"""
        self.progress_bar.setValue(0)

    def safe_update_progress_bar(self):
        """
        Update progress bar.
        If the progress bar is gone, STOP THE TIMER FOREVER.
        """
        pb = getattr(self, "progress_bar", None)

        # Progress bar no longer exists → kill timer
        if pb is None:
            if hasattr(self, "_progress_timer") and self._progress_timer:
                self._progress_timer.stop()
            return

        try:
            duration = self.player.duration()
            if duration <= 0:
                pb.setValue(0)
                return

            position = self.player.position()
            percent = int((position / duration) * 100)
            pb.setValue(percent)

        except RuntimeError:
            # C++ object is gone → STOP TIMER PERMANENTLY
            if hasattr(self, "_progress_timer") and self._progress_timer:
                self._progress_timer.stop()
            self.progress_bar = None

    def play_selected(self, item):
        print("DEBUG: play_selected fired for:", item.text())  # confirms signal

        playlist = self.current_playlist_data()
        if not playlist:
            print("No playlist found")
            return

        # Find the song by its path instead of just title for duplicates
        try:
            song_path = next(
                s["path"] for s in playlist if s["title"] == item.text()
            )
            index = next(i for i, s in enumerate(playlist) if s["path"] == song_path)
        except StopIteration:
            print("Song not found in playlist")
            return

        # Set the current song index
        self.current_index = index
        self.is_playing = False  # ensures play_pause starts playback

        # Update cover image if available
        current_song = playlist[index]
        if current_song.get("image"):
            try:
                self.update_cover()
            except Exception as e:
                print("Error updating cover:", e)

        # Update song label
        self.song_label.setText(current_song["title"])

        # Start playback
        self.play_pause()

        # Optional: refresh now playing widget to highlight current song
        self.update_now_playing_widget()

    # ---------------- Remove Playlist ----------------
    def remove_current_playlist(self):
        try:
            idx = self.tabs.currentIndex()
            if idx == -1:
                return
            name = self.tabs.tabText(idx)
            text, ok = QInputDialog.getText(self, "Confirm Removal", f"Type DELETE to remove playlist '{name}':")
            if not ok or text.strip().upper() != "DELETE":
                return

            if name in self.playlists:
                del self.playlists[name]
            self.tabs.removeTab(idx)
            if hasattr(self, "now_playing_queue"):
                self.now_playing_queue.clear()
            self.queue_widget.clear()
            self.current_index = -1

            if self.tabs.count() > 0:
                new_index = min(idx, self.tabs.count() - 1)
                self.tabs.setCurrentIndex(new_index)

                self.refresh_added_songs()

                playlist = self.current_playlist_data()

                self.now_playing_queue = list(range(len(playlist)))

                self.queue_position = 0 if playlist else -1
                self.current_index = 0 if playlist else -1

                if playlist:
                    self.load_song()
            self.update_remove_playlist_btn_state()
            settings = self.safe_getattr("settings")
            if settings:
                try:
                    settings.setValue("playlists", json.dumps(self.playlists))
                except Exception as e:
                    print("Failed to save settings:", e)

            self.song_label.setText("No song loaded")
        except Exception as e:
            print("Error in remove_current_playlist:", e)

    def load_song(self):
        playlist = self.current_playlist_data()

        # Safety checks
        if not playlist:
            return

        if self.current_index < 0:
            self.current_index = 0

        if self.current_index >= len(playlist):
            return

        song = playlist[self.current_index]
        path = song.get("path")

        if not path or not os.path.exists(path):
            print("Invalid or missing file:", path)
            return

        # Stop anything currently playing
        self.player.stop()

        # Qt 6 / PySide6 way (NO QMediaContent)
        with suppress_stderr():
            self.player.setSource(QUrl.fromLocalFile(path))
        # UI updates
        self.song_label.setText(os.path.basename(path))
        self.update_cover()

        with suppress_stderr():
            self.player.play()
        self.is_playing = True
        self.play_btn.setText("Pause")

    # ---------------- Playback ----------------
    def update_cover(self):
        if self.current_index < 0:
            self.cover.setPixmap(QPixmap(resource_path("assets/default_cover.png")))
            return

        song_data = self.current_playlist_data()[self.current_index]
        image_path = song_data.get("image")

        if not image_path or not os.path.isfile(image_path):
            self.cover.setPixmap(QPixmap(resource_path("assets/default_cover.png")))
        else:
            pixmap = QPixmap(image_path)
            self.cover.setPixmap(
                pixmap.scaled(
                    300,
                    300,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )

    def auto_next(self, status):
        # Disabled: replaced by on_media_status_changed system
        pass

    def filter_playlist(self, text: str):
        widget = self.current_playlist_widget()
        for i in range(widget.count()):
            item = widget.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    # ---------------- Search ----------------

    def closeEvent(self, event):
        """Clean shutdown of the application to prevent crashes and SIGTRAP errors."""
        self._closing = True  # flag for internal logic if needed

        # ---------------- Stop progress timer ----------------
        if hasattr(self, "_progress_timer") and self._progress_timer:
            self._progress_timer.stop()
            self._progress_timer.deleteLater()
            self._progress_timer = None

        # ---------------- Stop media player ----------------
        try:
            if hasattr(self, "player") and self.player:
                self.player.stop()
                self.player.deleteLater()
                self.player = None
        except Exception as e:
            print("Error stopping player:", e)

        # ---------------- Stop global keyboard listener ----------------
        if hasattr(self, "listener") and self.listener:
            try:
                self.listener.stop()
                self.listener = None
            except Exception as e:
                print("Error stopping keyboard listener:", e)

        # ---------------- Clear UI references ----------------
        # This prevents Qt from trying to access deleted widgets
        for attr in ["progress_bar", "added_songs_widget", "queue_widget", "song_label"]:
            if hasattr(self, attr):
                try:
                    widget = getattr(self, attr)
                    if widget:
                        widget.deleteLater()
                        setattr(self, attr, None)
                except Exception:
                    pass

        # ---------------- Call the parent closeEvent ----------------
        try:
            super().closeEvent(event)
        except Exception:
            event.accept()  # fallback in case parent crashes

    # ---------------- Persistence ----------------

    def on_key_press(self, key):
        try:
            # Ignore Caps Lock entirely (macOS SIGTRAP killer)
            if key == keyboard.Key.caps_lock:
                return

            try:
                k = key.char.lower()
            except AttributeError:
                k = getattr(key, "name", None)
                if k:
                    k = k.lower()

            if not k:
                return

            # Dispatch safely to Qt thread
            QTimer.singleShot(0, lambda: self.process_key_press(k))

        except Exception as e:
            print(f"Error handling key press: {e}")

    def shuffle_now_playing(self):
        import random

        if not self.now_playing_queue:
            return

        random.shuffle(self.now_playing_queue)

        self.queue_position = 0
        self.current_index = self.now_playing_queue[0]

        self.update_now_playing_widget()

        self.load_song()

    def process_key_press(self, key):
        try:
            if not hasattr(self, "key_bindings"):
                return
            if key == self.key_bindings.get("play_pause"):
                self.play_pause()
            elif key == self.key_bindings.get("next"):
                self.next_song()
            elif key == self.key_bindings.get("prev"):
                self.prev_song()
            elif key == self.key_bindings.get("shuffle"):
                self.shuffle_now_playing()
        except Exception as e:
            print(f"Error in process_key_press: {e}")

    # ---------------- Global Keyboard Listener ----------------

    def start_global_listener(self):
        # macOS safety: disable listener unless explicitly allowed
        if sys.platform == "darwin":
            print("Global keyboard listener disabled on macOS to prevent SIGTRAP.")
            return

        if self.listener:
            return

        try:
            import pynput.keyboard as keyboard
            self.listener = keyboard.Listener(on_press=self.on_key_press)
            self.listener.start()
            print("Global keyboard listener started.")
        except Exception as e:
            print(f"Failed to start global listener: {e}")
            self.listener = None

    # ---------------- Playback Controls ----------------
    def play_pause(self):
        """Toggle playback of the current song safely."""
        if not self.now_playing_queue:
            return

        if self.current_index < 0:
            self.current_index = 0

        if self.player.source().isEmpty():
            self.load_song()
            return

        # Toggle play/pause
        try:
            if self.is_playing:
                self.player.pause()
                if hasattr(self, "play_btn") and self.play_btn:
                    self.play_btn.setText("Play")
                self.is_playing = False
            else:
                with suppress_stderr():
                    self.player.play()
                if hasattr(self, "play_btn") and self.play_btn:
                    self.play_btn.setText("Pause")
                self.is_playing = True
        except Exception as e:
            print(f"Playback error: {e}")

        # Update progress bar safely
        try:
            if hasattr(self, "progress_bar") and self.progress_bar:
                duration = self.player.duration() if hasattr(self.player, "duration") else 0
                if duration > 0:
                    position = self.player.position() if hasattr(self.player, "position") else 0
                    percent = int(position / duration * 100)
                    self.progress_bar.setValue(percent)
                else:
                    self.progress_bar.setValue(0)
        except RuntimeError:
            # Progress bar was deleted, ignore
            pass
        except Exception as e:
            print(f"Error updating progress bar: {e}")

        # Update current song label safely
        try:
            if hasattr(self, "song_label") and self.song_label:
                playlist = self.current_playlist_data()

                if 0 <= self.current_index < len(playlist):
                    self.song_label.setText(playlist[self.current_index].get("title", "Unknown"))
        except Exception as e:
            print(f"Error updating song label: {e}")

    def next_song(self):
        if not self.now_playing_queue:
            return

        if self.queue_position < len(self.now_playing_queue) - 1:
            self.queue_position += 1
        else:
            # End of queue → stop playback
            self.player.stop()
            self.is_playing = False
            self.play_btn.setText("Play")
            return

        self.current_index = self.now_playing_queue[self.queue_position]

        self.load_song()

    def prev_song(self):
        if not self.now_playing_queue:
            return

        if self.queue_position > 0:
            self.queue_position -= 1
        else:
            return

        self.current_index = self.now_playing_queue[self.queue_position]

        self.load_song()

    def create_new_playlist(self):
        name, ok = QInputDialog.getText(self, "New Playlist", "Enter playlist name:")
        if not ok or not name or name in self.playlists:
            return

        self.add_playlist(name)
        self.tabs.setCurrentIndex(self.tabs.count() - 1)

        self.settings.setValue("playlists", json.dumps(self.playlists))

        playlist = self.current_playlist_data()

        self.now_playing_queue = list(range(len(playlist)))

        if not self.now_playing_queue:
            self.current_index = -1
            self.song_label.setText("No song loaded")
            return

        self.current_index = 0
        self.is_playing = False
        self.play_pause()

    def create_default_playlist(self):
        default_name = "Default"
        if default_name not in self.playlists:
            self.add_playlist(default_name)
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
            print(f"Default playlist '{default_name}' created.")

    def refresh_added_songs(self):
        """Update the added_songs_widget and sync internal added_songs list."""
        self.added_songs = self.current_playlist_data()  # always match playlist
        self.added_songs_widget.clear()

        for song in self.added_songs:
            self.added_songs_widget.addItem(song["title"])

        self.remove_btn.setEnabled(bool(self.added_songs))

        # Ensure now_playing_queue covers all songs if empty
        if not self.now_playing_queue and self.added_songs:
            self.now_playing_queue = list(range(len(self.added_songs)))
            self.queue_position = 0
            self.current_index = self.now_playing_queue[0]

        self.update_now_playing_widget()

    def refresh_queue_ui(self):
        """Update the queue widget from now_playing_queue safely."""
        try:
            queue_widget = getattr(self, "queue_widget", None)
            now_playing_queue = getattr(self, "now_playing_queue", [])
            playlist = self.current_playlist_data() if hasattr(self, "current_playlist_data") else []

            if not queue_widget or not playlist or not now_playing_queue:
                print("No Songs in Playlist or UI not initialized")
                return

            queue_widget.clear()

            for i in now_playing_queue:
                if 0 <= i < len(playlist):
                    try:
                        filename = os.path.basename(playlist[i].get("path", "Unknown"))
                        queue_widget.addItem(filename)
                    except RuntimeError:
                        continue  # widget may be deleted

        except Exception as e:
            print("Error in refresh_queue_ui:", e)

    def sync_playlist_order(self):
        try:
            widget = self.current_playlist_widget()
            playlist = self.current_playlist_data()
            if not widget or not playlist:
                return

            new_order = []
            for i in range(widget.count()):
                item_text = widget.item(i).text()
                for song in playlist:
                    if os.path.basename(song.get("path", "")) == item_text:
                        new_order.append(song)
                        break

            self.playlists[self.current_playlist_name()] = new_order

            now_playing_queue = self.safe_getattr("now_playing_queue")
            playlist = self.current_playlist_data()
            self.now_playing_queue = list(range(len(playlist)))
            self.refresh_queue_ui()

            self.refresh_queue_ui()
        except Exception as e:
            print("Error in sync_playlist_order:", e)

    # ---------------- Filter Songs ----------------
    def update_now_playing_widget(self):
        """Update the queue widget safely to match now_playing_queue."""
        try:
            if not hasattr(self, "queue_widget") or not self.queue_widget:
                return

            self.queue_widget.clear()

            for idx in getattr(self, "now_playing_queue", []):
                if 0 <= idx < len(getattr(self, "added_songs", [])):
                    song = self.added_songs[idx]
                    try:
                        item = QListWidgetItem(song.get("title", "Unknown"))
                        self.queue_widget.addItem(item)
                    except RuntimeError:
                        continue  # skip if the widget was deleted mid-loop

            # Highlight current song safely
            if 0 <= getattr(self, "current_index", -1) < self.queue_widget.count():
                try:
                    self.queue_widget.setCurrentRow(self.current_index)
                except RuntimeError:
                    pass

        except Exception as e:
            print("Error in update_now_playing_widget:", e)

    def filter_songs(self, text: str):
        """Filter songs in Added Songs list."""
        try:
            text_lower = text.lower()

            widget = self.added_songs_widget
            if not widget:
                return

            for i in range(widget.count()):
                item = widget.item(i)
                if not item:
                    continue

                item.setHidden(text_lower not in item.text().lower())

        except Exception as e:
            print("Error in filter_songs:", e)



    # ---------------- Filter Playlists ----------------
    def filter_playlists(self, text: str):
        try:
            tabs = self.safe_getattr("tabs", None)
            hidden_tabs = self.safe_getattr("_hidden_tabs", {})

            all_indices = []

            if tabs:
                all_indices = list(range(tabs.count()))

            # DO NOT mix hidden tab keys with indices (they are now names)
            hidden_tabs = self.safe_getattr("_hidden_tabs", {})
            hidden_names = list(hidden_tabs.keys()) if hidden_tabs else []

            text_lower = text.lower()

            # visible tabs
            for i in all_indices:
                tab_name = tabs.tabText(i)
                self.set_tab_visible(i, text_lower in tab_name.lower())

            # hidden tabs (by name match only, no indices)
            # hidden tabs (filter by name)
            for i in range(tabs.count()):
                tab_name = tabs.tabText(i)
                if tab_name in hidden_names:
                    visible = text_lower in tab_name.lower()
                    self.set_tab_visible(i, visible)
        except Exception as e:
            print("Error in filter_playlists:", e)

    # ---------------- Rename Selected Song ----------------
    def rename_selected_song(self):
        """Rename the selected song from Added Songs."""
        item = self.added_songs_widget.currentItem()

        if not item:
            QMessageBox.warning(
                self,
                "No Song Selected",
                "Select a song in Added Songs first."
            )
            return

        index = self.added_songs_widget.row(item)

        playlist = self.current_playlist_data()

        if index < 0 or index >= len(playlist):
            return

        old_name = playlist[index]["title"]

        new_name, ok = QInputDialog.getText(
            self,
            "Rename Song",
            f"Enter new name for '{old_name}':",
            text=old_name
        )

        if not ok or not new_name.strip():
            return

        playlist[index]["title"] = new_name.strip()

        self.refresh_added_songs()
        self.update_now_playing_widget()

        if self.current_index == index:
            self.song_label.setText(new_name.strip())

        self.settings.setValue(
            "playlists",
            json.dumps(self.playlists)
        )

    # ---------------- Rename Current Playlist ----------------
    def rename_current_playlist(self):
        """Rename the current playlist tab and update underlying data."""
        current_index = self.tabs.currentIndex()
        if current_index == -1:
            return

        old_name = self.tabs.tabText(current_index)
        new_name, ok = QInputDialog.getText(
            self, "Rename Playlist", f"Enter new name for '{old_name}':"
        )
        if ok and new_name.strip():
            new_name = new_name.strip()
            if new_name in self.playlists:
                QMessageBox.warning(self, "Error", f"Playlist '{new_name}' already exists!")
                return

            self.playlists[new_name] = self.playlists.pop(old_name)
            self.tabs.setTabText(current_index, new_name)
            settings = self.safe_getattr("settings")
            self.update_remove_playlist_btn_state()
            if settings:
                try:
                    settings.setValue("playlists", json.dumps(self.playlists))
                except Exception as e:
                    print("Failed to save settings:", e)

    # ---------------- Replace Song Cover ----------------
    def replace_song_cover(self):
        try:
            item = self.added_songs_widget.currentItem()
            if not item:
                QMessageBox.warning(self, "No Song Selected", "Select a song in Added Songs first.")
                return

            index = self.added_songs_widget.row(item)
            image_path, _ = QFileDialog.getOpenFileName(
                self, "Select Song Image", "", "Images (*.png *.jpg *.jpeg)"
            )

            if image_path and os.path.isfile(image_path):
                self.fix_image_orientation(image_path)
                self.current_playlist_data()[index]["image"] = image_path

                self.settings.setValue(
                    "playlists",
                    json.dumps(self.playlists)
                )
                if getattr(self, "current_index", -1) == index:
                    try:
                        if hasattr(self, "update_cover") and callable(self.update_cover):
                            self.update_cover()
                    except RuntimeError:
                        pass
            else:
                print("No valid image selected.")

        except Exception as e:
            print("Error in replace_song_cover:", e)

    # ---------------- Playlist Context Menu ----------------
    def playlist_menu(self, pos):
        try:
            widget = self.current_playlist_widget()
            if widget is None:
                return

            item = widget.itemAt(pos)
            if item is None:
                return

            index = widget.row(item)
            playlist = self.current_playlist_data()
            if index < 0 or index >= len(playlist):
                return

            menu = QMenu()
            set_image = menu.addAction("Set Song Image")
            rename_song = menu.addAction("Rename Song")
            remove_song = menu.addAction("Remove Song")

            action = menu.exec(widget.mapToGlobal(pos))
            if action is None:
                return

            if action == set_image:
                image, _ = QFileDialog.getOpenFileName(
                    self, "Select Image", "", "Images (*.png *.jpg *.jpeg)"
                )
                if image and os.path.exists(image):
                    playlist[index]["image"] = str(image)
                    if index == self.current_index:
                        self.update_cover()

            elif action == rename_song:
                text, ok = QInputDialog.getText(
                    self, "Rename Song", "Enter new song name:", text=item.text()
                )
                if ok and text.strip():
                    item.setText(text.strip())
                    # Optional: update underlying data path

            elif action == remove_song:
                self.current_index = index
                self.remove_selected_song()

        except Exception as e:
            print("Error in playlist_menu:", e)

    # ---------------- Tab Context Menu (Rename Playlist) ----------------
    def tab_menu(self, pos):
        try:
            tab_bar = self.tabs.tabBar()
            if tab_bar is None:
                return

            index = tab_bar.tabAt(pos)
            menu = QMenu()

            hide_action = menu.addAction("Hide Playlist")
            rename_action = menu.addAction("Rename Playlist") if index != -1 else None
            hidden_action = menu.addAction("Manage Hidden Playlists") if getattr(self, "_hidden_tabs", None) else None

            action = menu.exec(tab_bar.mapToGlobal(pos))
            if action is None:
                return

            if action == hide_action and index != -1:
                self.hide_playlist(index)

            elif action == rename_action and index != -1:
                old_name = self.tabs.tabText(index)
                new_name, ok = QInputDialog.getText(
                    self, "Rename Playlist", "Enter new name:", text=old_name
                )
                if ok and new_name.strip() and new_name != old_name:
                    new_name = new_name.strip()
                    if new_name in self.playlists:
                        QMessageBox.warning(self, "Error", f"Playlist '{new_name}' already exists!")
                        return

                    self.playlists[new_name] = self.playlists.pop(old_name)
                    self.tabs.setTabText(index, new_name)
                    settings = self.safe_getattr("settings")
                    if settings:
                        try:
                            settings.setValue("playlists", json.dumps(self.playlists))
                        except Exception as e:
                            print("Failed to save settings:", e)


            elif action == hidden_action:
                self.manage_hidden_playlists()

        except Exception as e:
            print("Error in tab_menu:", e)

    def setup_player_signals(self):
        try:
            if hasattr(self, "player") and self.player:
                self.player.mediaStatusChanged.connect(self.on_media_status_changed)
        except Exception as e:
            print(f"Error connecting media signals: {e}")

    def on_media_status_changed(self, status):
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            try:
                self.play_next_in_queue()
            except Exception as e:
                print("Error in play_next_in_queue:", e)

    def play_next_in_queue(self):
        if not self.now_playing_queue:
            return

        if self.queue_position < len(self.now_playing_queue) - 1:
            self.queue_position += 1
        else:
            # loop back to first song
            self.queue_position = 0

        self.current_index = self.now_playing_queue[self.queue_position]

        self.load_song()

    # ---------------- Progress Bar ----------------
    # ---------------- Progress Bar ----------------


    # ---------------- Safe Hide Playlist ----------------
    def hide_playlist(self, index: int):
        try:
            tabs = self.safe_getattr("tabs", None)
            self.safe_getattr("_hidden_tabs", {})

            # Validate index
            if tabs is None or index is None or index < 0 or index >= tabs.count():
                print(f"Warning: Invalid tab index {index}, cannot hide playlist")
                return

            # Get playlist name and widget safely
            playlist_name = tabs.tabText(index)
            widget = tabs.widget(index)

            if widget is None or not playlist_name:
                print(f"Warning: Tab at index {index} has no widget or name")
                return

            if not hasattr(self, "_hidden_tabs"):
                self._hidden_tabs = {}

            # use playlist name as stable key (NOT index)
            self._hidden_tabs[playlist_name] = (widget, playlist_name)

            # Remove tab from UI
            tabs.removeTab(index)

            # Update settings
            settings = self.safe_getattr("settings", None)
            if settings:
                settings.setValue(
                    "hidden_tabs",
                    json.dumps({k: v[1] for k, v in self._hidden_tabs.items()})
                )
            else:
                print("Warning: Settings object not found, hidden state not saved")

            print(f"Playlist '{playlist_name}' hidden successfully.")

        except Exception as e:
            print(f"Error in hide_playlist: {e}")

    # ---------------- Playlist Management ----------------
    def add_playlist(self, name: str):
        name = name.strip()
        if not name:
            print("Cannot add playlist with empty name.")
            return
        if name in self.playlists and any(
                self.tabs.tabText(i) == name for i in range(self.tabs.count())
        ):
            print(f"Playlist '{name}' already has a tab.")
            return

        if name not in self.playlists:
            self.playlists[name] = []
        widget = QListWidget()
        widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(self.playlist_menu)
        #widget.itemSelectionChanged.connect(self.update_remove_btn_state)
        widget.model().rowsMoved.connect(lambda source, start, end, destination, row: self.sync_playlist_order())

        if self.tabs:
            self.tabs.addTab(widget, name)

        # Save hidden tabs safely
        settings = self.safe_getattr("settings")
        if settings:
            settings.setValue(
                "hidden_tabs",
                json.dumps({k: v[1] for k, v in self._hidden_tabs.items()})
            )
        self.update_remove_playlist_btn_state()
    def current_playlist_name(self):
        index = self.tabs.currentIndex()
        if index < 0:
            return None
        return self.tabs.tabText(index)

    def current_playlist_widget(self) -> QListWidget | None:
        widget = self.tabs.currentWidget() if self.tabs else None
        if isinstance(widget, QListWidget):
            return widget
        return None

    def current_playlist_data(self) -> list[dict]:
        name = self.current_playlist_name()

        if not name:
            name = "Default"

        if name not in self.playlists:
            self.playlists[name] = []

        return self.playlists[name]

    # ---------------- UI Actions ----------------
    def add_songs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Songs",
            "",
            "Audio Files (*.mp3 *.wav *.ogg *.flac)"
        )
        if not files:
            return

        playlist_name = self.current_playlist_name()
        if not playlist_name:
            return

        playlist = self.playlists.setdefault(playlist_name, [])

        for path in files:
            song = {
                "path": path,
                "title": os.path.basename(path),
                "image": None
            }
            playlist.append(song)

        # Update internal song list for widget
        self.added_songs = playlist.copy()
        self.refresh_added_songs()

        # Always update the now playing queue
        playlist = self.current_playlist_data()

        self.now_playing_queue = list(range(len(playlist)))
        self.queue_position = 0
        if not self.now_playing_queue:
            self.current_index = -1
            return

        if self.current_index < 0 or self.current_index >= len(self.now_playing_queue):
            self.current_index = 0

        self.update_now_playing_widget()

        self.settings.setValue(
            "playlists",
            json.dumps(self.playlists)
        )

    def remove_selected_song(self):
        row = self.added_songs_widget.currentRow()

        if row < 0:
            QMessageBox.warning(
                self,
                "Invalid Action",
                "You can only remove songs from Added Songs."
            )
            return

        playlist = self.current_playlist_data()

        if row >= len(playlist):
            return

        was_current = (row == self.current_index)

        # 1. Remove from playlist (source of truth)
        playlist.pop(row)

        # 2. Fix queue WITHOUT changing order
        new_queue = []
        index_map = {}

        # Build mapping: old index → new index
        for old_i in range(len(playlist) + 1):
            if old_i < row:
                index_map[old_i] = old_i
            elif old_i > row:
                index_map[old_i] = old_i - 1

        for i in self.now_playing_queue:
            if i == row:
                continue  # remove deleted song
            new_queue.append(index_map.get(i, i))

        self.now_playing_queue = new_queue

        # 3. Fix current index safely
        if was_current:
            self.player.stop()
            self.is_playing = False
            self.current_index = -1
            self.queue_position = -1
            self.song_label.setText("No song loaded")
        else:
            # adjust current_index if needed
            if self.current_index > row:
                self.current_index -= 1

            # fix queue_position safely
            if self.current_index in self.now_playing_queue:
                self.queue_position = self.now_playing_queue.index(self.current_index)
            else:
                self.queue_position = -1

        # 4. Refresh UI ONLY (no logic changes)
        self.refresh_added_songs()
        self.update_now_playing_widget()

        # 5. Persist
        self.settings.setValue("playlists", json.dumps(self.playlists))
    # ---------------- Enable Horizontal Scroll ----------------
    def enable_horizontal_scroll(self):
        if hasattr(self, "_scroll_area"):
            return  # Already enabled

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        central = getattr(self, "central_widget", None)
        if not central:
            print("Central widget not initialized, cannot add scroll")
            return

        # Move current layout into scroll_area
        container = QWidget()
        container.setLayout(self.main_layout)
        scroll_area.setWidget(container)

        # Replace main layout with scroll_area
        new_layout = QVBoxLayout()
        new_layout.addWidget(scroll_area)
        self.central_widget.setLayout(new_layout)
        self._scroll_area = scroll_area

# ---------------- Run App ----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MusicPlayer()
    window.show()  # Make sure the window shows up
    sys.exit(app.exec())  # Start the app and exit properly

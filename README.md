# 🎵 Desktopcast

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PyQt6-Desktop-green?style=for-the-badge&logo=qt&logoColor=white" alt="PyQt6">
  <img src="https://img.shields.io/badge/Yamaha-MusicCast-purple?style=for-the-badge" alt="MusicCast">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License">
</p>

<p align="center">
  <strong>A beautiful desktop application for controlling Yamaha MusicCast receivers</strong>
</p>

<p align="center">
  Browse Tidal, Spotify, and more directly from your desktop. Control volume, playback, and navigate your music library with ease.
</p>

---

## 💡 Why Desktopcast?

While Tidal and other streaming apps offer "Connect" features for Yamaha receivers, **many older MusicCast receivers are no longer supported** — leaving users without a proper desktop app to browse and control their music.

Some third-party solutions exist, but they're often paid, closed-source, or limited in functionality. **I believe there should be a free, open-source option available to everyone.**

Desktopcast is my answer to this problem: a lightweight, open-source desktop controller that works with any Yamaha receiver supporting the MusicCast/YXC API — no matter how old.

---

## ✨ Features

- 🎛️ **Full Receiver Control** - Power, mute, and input selection
- 🔊 **Volume in dB** - Live slider with a dB scale (as on the receiver's display), −/+ buttons, and a ramp that stops a stray click from jumping to full volume
- 🎵 **Music Browsing** - Navigate Tidal, Spotify, Deezer, Qobuz, and more
- 🔍 **Search** - Search your streaming services directly
- 🎶 **Playlist Mode** - The queue beside the browser: double-click songs, albums or playlists to add them; play, skip or remove entries; "Play now" without losing the playlist
- 🕐 **Recent Items** - Quick access to recently played content
- ⭐ **Presets** - Save and recall your favorite stations/playlists
- 🖼️ **Album Art** - Beautiful thumbnail display for your music
- 📡 **Remembered Receivers** - Reconnects to the last receiver at startup; each receiver is listed by room name and model
- 🔄 **Live Updates** - The receiver pushes changes (volume, track, play time) to the app, with polling as a fallback

## 📸 Screenshots

![Main Window](screenshots/main.png)

## ⚠️ Current Status

> **Note:** This project is in active development.

- ✅ **Tested on:** Yamaha R-N602 (has a model profile: dB volume scale and tested queue editing)
- ⚠️ **Streaming services:** Tidal is fully functional; other services (Spotify, Deezer, Qobuz, etc.) may have limited functionality
- 🔄 **Coming soon:** Support for additional Yamaha models and potentially other receiver brands

If you have a different Yamaha model and want to help test, please [open an issue](https://github.com/incubodac/Desktopcast/issues) with your model name and any compatibility notes!

## 🚀 Installation

### Prerequisites

- Python 3.9 or higher
- A Yamaha receiver with MusicCast/YXC support
- Your receiver's IP address (find it in your router or receiver's network settings)

### Option 1: Install from source

```bash
# Clone the repository
git clone https://github.com/incubodac/Desktopcast.git
cd Desktopcast

# Create a virtual environment (recommended)
python -m venv venv

# Activate it (use the line for your shell)
source venv/bin/activate           # bash / zsh
# source venv/bin/activate.fish    # fish
# source venv/bin/activate.csh     # csh / tcsh
# venv\Scripts\activate            # Windows

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

> **Tip:** If you'd rather not activate the venv, call its interpreter directly:
> `./venv/bin/pip install -r requirements.txt` and `./venv/bin/python main.py`
> (on Windows: `venv\Scripts\pip` and `venv\Scripts\python`).

### Option 2: Download release (coming soon)

Pre-built binaries for Windows, macOS, and Linux will be available in the [Releases](https://github.com/incubodac/Desktopcast/releases) section.

## 🎮 Usage

1. **Connect to your receiver**
   - Enter your Yamaha receiver's IP address
   - Click "Connect"
   - Next time the app reconnects by itself; other receivers you used are in the dropdown

2. **Control playback**
   - Use the transport controls (play, pause, previous, next)
   - Adjust volume with the slider (drag, click, mouse wheel or arrow keys), the −/+ buttons, or mute

3. **Browse your music**
   - Select an input source (Tidal, Spotify, etc.)
   - Navigate through menus by clicking items
   - Use the search function to find specific content
   - Right-click an item for more: add to playlist, play next, play now, open

4. **Manage your queue**
   - Click "Playlist Mode" to show the queue beside the browser
   - Double-click a song or album to add it; double-click a queue entry to play it
   - Select an entry and press Delete (or "Remove") to take it out
   - If the receiver wipes the queue, the panel says so and shows the last command sent before it
   - Use "Recent" for recently played items
   - Save favorites to presets (the first empty slot; existing presets are never overwritten)

## 🔧 Configuration

The first time, the receiver field suggests `192.168.0.248`. After a successful connection the receiver is remembered (by its device ID, so a new IP from your router updates the same entry).

The app asks the receiver to push changes to it over UDP. If your firewall asks whether Python may accept incoming connections, allowing it makes updates instant; if you deny it, the app falls back to polling.

**Model profiles:** `MODEL_PROFILES` in `main.py` holds per-model settings (dB scale, loud zone, tested features). The R-N602 doesn't report volume in dB, so the app shows `−80.5 + 0.5 × volume` (volume 92 = −34.5 dB). Other models use the dB range the receiver reports, or the same Yamaha convention when the volume goes 0–161.

To enable debug output, set `DEBUG = True` in `main.py`.

## 📡 Supported Devices

This application works with Yamaha receivers that support the **Yamaha Extended Control (YXC) API**, including:

- Yamaha RX-V series
- Yamaha RX-A series  
- Yamaha WXA/WXC series
- Other MusicCast-enabled devices

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## 📋 Roadmap

- [ ] Multi-room/zone support
- [ ] System tray integration
- [ ] Keyboard shortcuts
- [ ] Dark/light theme toggle
- [ ] Pre-built binaries for all platforms
- [ ] Auto-discovery of receivers on network

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Yamaha for the MusicCast API
- The PyQt team for the excellent GUI framework
- All contributors and users of this project

## 📬 Support

- 🐛 Found a bug? [Open an issue](https://github.com/incubodac/Desktopcast/issues)
- 💡 Have an idea? [Start a discussion](https://github.com/incubodac/Desktopcast/discussions)
- ⭐ Like this project? Give it a star!

---

<p align="center">
  Made with ❤️ for the MusicCast community
</p>

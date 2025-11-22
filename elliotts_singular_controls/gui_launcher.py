"""
GUI Launcher for Elliott's Singular Controls with system tray support.
"""
import os
import sys
import time
import threading
import webbrowser
import logging
import tkinter as tk
from tkinter import scrolledtext, messagebox
from pathlib import Path
import socket
import psutil
import pystray
from PIL import Image, ImageDraw
from io import StringIO

# Set Windows app ID for taskbar icon (must be done before tkinter)
try:
    import ctypes
    myappid = 'elliott.singularcontrols.esc.1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except:
    pass  # Not on Windows or ctypes not available

# Import needed functions
from elliotts_singular_controls.core import effective_port, _runtime_version


def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def kill_process_on_port(port: int) -> bool:
    """Kill any process using the specified port."""
    killed = False
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for conn in proc.connections():
                if conn.laddr.port == port:
                    print(f"Killing process {proc.info['name']} (PID: {proc.info['pid']}) on port {port}")
                    proc.kill()
                    killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return killed


class SingularTweaksGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Elliott's Singular Controls - v{_runtime_version()}")
        self.root.geometry("750x600")
        self.root.resizable(False, False)  # Fixed size window

        # Modern dark theme colors (dark gray, not pure black)
        self.bg_dark = "#1a1a1a"
        self.bg_medium = "#252525"
        self.bg_card = "#2d2d2d"
        self.accent_teal = "#00bcd4"
        self.accent_teal_dark = "#0097a7"
        self.text_light = "#ffffff"
        self.text_gray = "#888888"
        self.button_blue = "#2196f3"
        self.button_green = "#4caf50"
        self.button_red = "#ff5252"
        self.button_gray = "#3d3d3d"  # Lighter gray for visibility
        self.button_orange = "#e67e22"  # Muted orange for restart
        self.button_red_dark = "#c0392b"  # Darker red for quit

        self.root.configure(bg=self.bg_dark)

        # Icon for system tray
        self.icon = None
        self.server_thread = None
        self.server_running = False
        self.console_visible = False
        self.console_text = None
        self.console_window = None
        self.log_handler = None

        # Runtime tracking
        self.start_time = time.time()
        self.pulse_angle = 0

        # Set window icon
        self._set_window_icon()

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _set_window_icon(self):
        """Set the window icon from file or generate it."""
        try:
            # Try to load from static folder
            icon_path = Path(__file__).parent.parent / "static" / "esc_icon.ico"
            if icon_path.exists():
                self.root.iconbitmap(str(icon_path))
            else:
                # Try relative to exe location
                from elliotts_singular_controls.core import _app_root
                icon_path = _app_root() / "static" / "esc_icon.ico"
                if icon_path.exists():
                    self.root.iconbitmap(str(icon_path))
        except Exception as e:
            pass  # Icon not critical

    def create_icon_image(self):
        """Create ESC icon for the system tray matching the new logo design."""
        size = 64
        image = Image.new('RGBA', (size, size), (26, 26, 26, 255))  # Dark background
        dc = ImageDraw.Draw(image)

        cx, cy = size // 2, size // 2
        color = (0, 188, 212, 255)  # #00bcd4
        line_width = max(2, size // 32)

        # Draw concentric circles
        for radius_factor in [0.35, 0.24, 0.13]:
            r = int(size * radius_factor)
            dc.ellipse([cx-r, cy-r, cx+r, cy+r], outline=color, width=line_width)

        # Draw lines to letters
        outer_r = int(size * 0.35)

        # Line to E (top)
        dc.line([(cx, cy - outer_r), (cx, 2)], fill=color, width=line_width)

        # Line to S (bottom-left)
        dc.line([(cx - 4, cy + outer_r - 2), (8, size - 4)], fill=color, width=line_width)

        # Line to C (right)
        dc.line([(cx + outer_r, cy), (size - 2, cy)], fill=color, width=line_width)

        # Draw small checkmark in center
        check_size = int(size * 0.08)
        dc.line([(cx - check_size, cy), (cx - 2, cy + check_size//2)], fill=color, width=line_width)
        dc.line([(cx - 2, cy + check_size//2), (cx + check_size, cy - check_size//2)], fill=color, width=line_width)

        return image

    def create_rounded_rectangle(self, canvas, x1, y1, x2, y2, radius=15, **kwargs):
        """Draw a rounded rectangle on a canvas."""
        points = [
            x1+radius, y1,
            x1+radius, y1,
            x2-radius, y1,
            x2-radius, y1,
            x2, y1,
            x2, y1+radius,
            x2, y1+radius,
            x2, y2-radius,
            x2, y2-radius,
            x2, y2,
            x2-radius, y2,
            x2-radius, y2,
            x1+radius, y2,
            x1+radius, y2,
            x1, y2,
            x1, y2-radius,
            x1, y2-radius,
            x1, y1+radius,
            x1, y1+radius,
            x1, y1
        ]
        return canvas.create_polygon(points, **kwargs, smooth=True)

    def create_rounded_button(self, parent, text, command, bg_color, width=180, height=50, state=tk.NORMAL):
        """Create a modern rounded button using canvas with smooth edges."""
        canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=self.bg_dark,
            highlightthickness=0,
            bd=0
        )

        # Draw rounded rectangle with smoother edges using polygon
        radius = 10
        self._draw_smooth_rounded_rect(canvas, 0, 0, width, height, radius, bg_color)

        # Add text
        text_id = canvas.create_text(
            width/2, height/2,
            text=text,
            fill=self.text_light if state == tk.NORMAL else self.text_gray,
            font=("Arial", 11, "bold")
        )

        # Bind click event
        if state == tk.NORMAL:
            canvas.bind("<Button-1>", lambda e: command())
            canvas.bind("<Enter>", lambda e: canvas.configure(cursor="hand2"))
            canvas.bind("<Leave>", lambda e: canvas.configure(cursor=""))

        canvas.button_state = state
        canvas.bg_color = bg_color
        return canvas

    def _draw_smooth_rounded_rect(self, canvas, x1, y1, x2, y2, radius, fill):
        """Draw a smooth rounded rectangle using arcs and rectangles."""
        # Use a combination approach for smoother corners
        # Draw the main body rectangles first
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline=fill)
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline=fill)

        # Draw corner circles with matching outline for smoother appearance
        canvas.create_oval(x1, y1, x1 + radius*2, y1 + radius*2, fill=fill, outline=fill)
        canvas.create_oval(x2 - radius*2, y1, x2, y1 + radius*2, fill=fill, outline=fill)
        canvas.create_oval(x1, y2 - radius*2, x1 + radius*2, y2, fill=fill, outline=fill)
        canvas.create_oval(x2 - radius*2, y2 - radius*2, x2, y2, fill=fill, outline=fill)

    def setup_ui(self):
        """Setup the main UI with modern dark theme."""
        # Top section with branding
        top_frame = tk.Frame(self.root, bg=self.bg_dark, height=70)
        top_frame.pack(fill=tk.X, padx=40, pady=(30, 0))
        top_frame.pack_propagate(False)

        # Branding text - centered with version
        brand_frame = tk.Frame(top_frame, bg=self.bg_dark)
        brand_frame.pack(expand=True)

        brand_label = tk.Label(
            brand_frame,
            text="Elliott's Singular Controls",
            font=("Arial", 24, "bold"),
            bg=self.bg_dark,
            fg=self.text_light
        )
        brand_label.pack()

        version_label = tk.Label(
            brand_frame,
            text=f"Version {_runtime_version()}",
            font=("Arial", 10),
            bg=self.bg_dark,
            fg=self.text_gray
        )
        version_label.pack()

        # Main content area
        content_frame = tk.Frame(self.root, bg=self.bg_dark)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=(20, 30))

        # Port card with rounded corners using Canvas
        port_card_canvas = tk.Canvas(
            content_frame,
            width=670,
            height=140,
            bg=self.bg_dark,
            highlightthickness=0
        )
        port_card_canvas.pack(pady=(0, 20))

        # Draw rounded rectangle for card background
        self._draw_rounded_rect(port_card_canvas, 0, 0, 670, 140, 20, self.bg_card)

        # SERVER PORT label
        port_card_canvas.create_text(335, 25, text="SERVER PORT", fill=self.text_gray, font=("Arial", 10, "bold"))

        # Port number with teal background (rounded)
        self._draw_rounded_rect(port_card_canvas, 235, 40, 435, 95, 12, self.accent_teal)
        self.port_text_id = port_card_canvas.create_text(335, 67, text=str(effective_port()), fill=self.text_light, font=("Arial", 32, "bold"))

        # Change port button (small, rounded)
        self._draw_rounded_rect(port_card_canvas, 275, 105, 395, 132, 14, self.bg_medium)
        port_card_canvas.create_text(335, 118, text="Change Port", fill=self.text_gray, font=("Arial", 9))

        # Bind click on change port area
        port_card_canvas.tag_bind("change_port", "<Button-1>", lambda e: self.change_port())
        port_card_canvas.addtag_overlapping("change_port", 275, 105, 395, 132)
        port_card_canvas.bind("<Button-1>", self._handle_port_card_click)
        port_card_canvas.bind("<Enter>", lambda e: port_card_canvas.configure(cursor="hand2"))
        port_card_canvas.bind("<Leave>", lambda e: port_card_canvas.configure(cursor=""))

        self.port_card_canvas = port_card_canvas

        # Status frame with pulse indicator and runtime
        status_frame = tk.Frame(content_frame, bg=self.bg_dark)
        status_frame.pack(pady=(0, 5))

        # Pulse indicator canvas
        self.pulse_canvas = tk.Canvas(
            status_frame,
            width=20,
            height=20,
            bg=self.bg_dark,
            highlightthickness=0
        )
        self.pulse_canvas.pack(side=tk.LEFT, padx=(0, 8))
        self.pulse_circle = self.pulse_canvas.create_oval(4, 4, 16, 16, fill=self.text_gray, outline="")

        # Status message
        self.status_label = tk.Label(
            status_frame,
            text="Starting server...",
            font=("Arial", 11),
            bg=self.bg_dark,
            fg=self.text_gray
        )
        self.status_label.pack(side=tk.LEFT)

        # Runtime label
        self.runtime_label = tk.Label(
            status_frame,
            text="",
            font=("Arial", 10),
            bg=self.bg_dark,
            fg=self.text_gray
        )
        self.runtime_label.pack(side=tk.LEFT, padx=(15, 0))

        # URL label
        self.url_label = tk.Label(
            content_frame,
            text=f"http://127.0.0.1:{effective_port()}/",
            font=("Arial", 10),
            bg=self.bg_dark,
            fg=self.text_gray
        )
        self.url_label.pack(pady=(0, 25))

        # Action buttons (3 rows now with restart button)
        btn_container = tk.Frame(content_frame, bg=self.bg_dark)
        btn_container.pack()

        # Row 1
        row1 = tk.Frame(btn_container, bg=self.bg_dark)
        row1.pack(pady=6)

        self.launch_btn = self.create_rounded_button(
            row1, "Open Web GUI", self.launch_browser,
            self.button_blue, width=290, height=50, state=tk.DISABLED
        )
        self.launch_btn.pack(side=tk.LEFT, padx=6)

        self.console_toggle_btn = self.create_rounded_button(
            row1, "Open Console", self.toggle_console,
            self.button_gray, width=290, height=50
        )
        self.console_toggle_btn.pack(side=tk.LEFT, padx=6)

        # Row 2
        row2 = tk.Frame(btn_container, bg=self.bg_dark)
        row2.pack(pady=6)

        self.restart_btn = self.create_rounded_button(
            row2, "Restart Server", self.restart_application,
            self.button_orange, width=290, height=50
        )
        self.restart_btn.pack(side=tk.LEFT, padx=6)

        self.hide_btn = self.create_rounded_button(
            row2, "Hide to Tray", self.minimize_to_tray,
            self.button_gray, width=290, height=50
        )
        self.hide_btn.pack(side=tk.LEFT, padx=6)

        # Row 3 (Quit centered or full width)
        row3 = tk.Frame(btn_container, bg=self.bg_dark)
        row3.pack(pady=6)

        self.quit_btn = self.create_rounded_button(
            row3, "Quit Server", self.on_closing,
            self.button_red_dark, width=596, height=50
        )
        self.quit_btn.pack()

        # Start pulse animation and runtime update
        self._update_pulse()
        self._update_runtime()

        # Auto-start server on launch
        self.root.after(500, self.start_server)

    def _draw_rounded_rect(self, canvas, x1, y1, x2, y2, radius, fill):
        """Draw a rounded rectangle on canvas."""
        canvas.create_oval(x1, y1, x1 + radius*2, y1 + radius*2, fill=fill, outline="")
        canvas.create_oval(x2 - radius*2, y1, x2, y1 + radius*2, fill=fill, outline="")
        canvas.create_oval(x1, y2 - radius*2, x1 + radius*2, y2, fill=fill, outline="")
        canvas.create_oval(x2 - radius*2, y2 - radius*2, x2, y2, fill=fill, outline="")
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="")
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="")

    def _handle_port_card_click(self, event):
        """Handle clicks on the port card canvas."""
        # Check if click is in the "Change Port" button area
        if 275 <= event.x <= 395 and 105 <= event.y <= 132:
            self.change_port()

    def _update_pulse(self):
        """Update the pulse indicator animation."""
        if self.server_running:
            # Pulsing animation - oscillate between bright and dim
            self.pulse_angle = (self.pulse_angle + 10) % 360
            import math
            brightness = int(127 + 127 * math.sin(math.radians(self.pulse_angle)))
            # Interpolate between dim teal and bright teal
            r = int(0 + (0 - 0) * brightness / 255)
            g = int(100 + (188 - 100) * brightness / 255)
            b = int(120 + (212 - 120) * brightness / 255)
            color = f"#{r:02x}{g:02x}{b:02x}"
            self.pulse_canvas.itemconfig(self.pulse_circle, fill=color)
        else:
            self.pulse_canvas.itemconfig(self.pulse_circle, fill=self.text_gray)
        self.root.after(50, self._update_pulse)

    def _update_runtime(self):
        """Update the runtime display."""
        if self.server_running:
            elapsed = int(time.time() - self.start_time)
            hours, remainder = divmod(elapsed, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                runtime_str = f"Runtime: {hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                runtime_str = f"Runtime: {minutes}m {seconds}s"
            else:
                runtime_str = f"Runtime: {seconds}s"
            self.runtime_label.config(text=runtime_str)
        self.root.after(1000, self._update_runtime)

    def change_port(self):
        """Open dialog to change port."""
        from tkinter import simpledialog
        new_port = simpledialog.askinteger(
            "Change Port",
            "Enter new port number:",
            initialvalue=effective_port(),
            minvalue=1024,
            maxvalue=65535
        )
        if new_port and new_port != effective_port():
            # Update config
            from elliotts_singular_controls.core import CONFIG, save_config
            CONFIG.port = new_port
            save_config(CONFIG)

            # Update UI
            self.port_label.config(text=str(new_port))
            self.url_label.config(text=f"e.g. http://127.0.0.1:{new_port}/")

            messagebox.showinfo(
                "Port Changed",
                f"Port changed to {new_port}. Please restart the application for changes to take effect."
            )

    def update_button_text(self, canvas, new_text):
        """Update text on a canvas button."""
        for item in canvas.find_all():
            if canvas.type(item) == "text":
                canvas.itemconfig(item, text=new_text)
                break

    def toggle_console(self):
        """Toggle console window visibility."""
        try:
            window_exists = self.console_window is not None and self.console_window.winfo_exists()
        except:
            window_exists = False

        if not window_exists:
            # Create console window
            self.console_window = tk.Toplevel(self.root)
            self.console_window.title("Console Output")
            self.console_window.geometry("800x400")
            self.console_window.configure(bg=self.bg_dark)

            # Handle window close via X button
            self.console_window.protocol("WM_DELETE_WINDOW", self._on_console_close)

            # Console output
            self.console_text = scrolledtext.ScrolledText(
                self.console_window,
                bg="#1e1e1e",
                fg="#d4d4d4",
                font=("Consolas", 9),
                relief=tk.FLAT,
                wrap=tk.WORD
            )
            self.console_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            # Add initial status message
            port = effective_port()
            self.console_text.insert(tk.END, f"Elliott's Singular Controls v{_runtime_version()}\n")
            self.console_text.insert(tk.END, "=" * 60 + "\n")
            if self.server_running:
                self.console_text.insert(tk.END, f"✓ Server running on http://0.0.0.0:{port}\n")
                self.console_text.insert(tk.END, f"  Access at: http://localhost:{port}\n")
            else:
                self.console_text.insert(tk.END, "⚠ Server not running\n")
            self.console_text.insert(tk.END, "=" * 60 + "\n\n")
            self.console_text.insert(tk.END, "Console output will appear here...\n\n")

            # Redirect stdout to console
            sys.stdout = ConsoleRedirector(self.console_text)
            sys.stderr = ConsoleRedirector(self.console_text)

            # Set up logging handler for the root logger
            self.log_handler = TkinterLogHandler(self.console_text)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
            self.log_handler.setFormatter(formatter)
            logging.getLogger().addHandler(self.log_handler)
            logging.getLogger().setLevel(logging.INFO)

            # Write a test message
            print(f"[Console] Console window opened at {time.strftime('%H:%M:%S')}")

            self._update_console_button(True)
            self.console_visible = True
        else:
            self._close_console()

    def _on_console_close(self):
        """Handle console window being closed via X button."""
        self._close_console()

    def _close_console(self):
        """Close the console window and clean up."""
        if self.log_handler:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler = None
        if self.console_window:
            try:
                self.console_window.destroy()
            except:
                pass
        self.console_window = None
        self.console_text = None
        self._update_console_button(False)
        self.console_visible = False

    def _update_console_button(self, is_open):
        """Update the console button text and color based on state."""
        if is_open:
            self.update_button_text(self.console_toggle_btn, "Close Console")
            self._redraw_button(self.console_toggle_btn, "Close Console", self.button_blue)
        else:
            self.update_button_text(self.console_toggle_btn, "Open Console")
            self._redraw_button(self.console_toggle_btn, "Open Console", self.button_gray)

    def _redraw_button(self, canvas, text, bg_color):
        """Redraw a button canvas with new color."""
        canvas.delete("all")
        width = int(canvas['width'])
        height = int(canvas['height'])
        self._draw_smooth_rounded_rect(canvas, 0, 0, width, height, 10, bg_color)
        canvas.create_text(width/2, height/2, text=text, fill=self.text_light, font=("Arial", 11, "bold"))

    def start_server(self):
        """Start the server in a background thread."""
        port = effective_port()

        # Automatically kill any existing instance on the port
        if is_port_in_use(port):
            print(f"[Server] Port {port} in use, closing existing instance...")
            kill_process_on_port(port)
            # Wait a moment for the port to be released
            import time
            time.sleep(0.5)

        self.status_label.config(text=f"Starting server on port {port}...")

        # Start server in background thread
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

        # Update UI after short delay
        self.root.after(2000, self._server_started)

    def _run_server(self):
        """Run the server (called in background thread)."""
        try:
            import uvicorn
            import logging
            from elliotts_singular_controls.core import app, effective_port

            # Configure basic logging to avoid uvicorn formatter errors
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s',
                datefmt='%H:%M:%S',
                force=True
            )

            # Configure uvicorn with custom logging and access log enabled
            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=effective_port(),
                log_level="info",
                access_log=True,  # Enable access log to see HTTP requests
                log_config=None  # Disable default log config
            )
            server = uvicorn.Server(config)
            print(f"[Server] Starting uvicorn server on port {effective_port()}")
            server.run()
        except Exception as e:
            print(f"[Server] Error starting server: {e}")
            import traceback
            traceback.print_exc()

    def enable_canvas_button(self, canvas, bg_color):
        """Enable a canvas button."""
        canvas.button_state = tk.NORMAL
        # Recreate the button with proper colors
        canvas.delete("all")
        width = int(canvas['width'])
        height = int(canvas['height'])
        self._draw_smooth_rounded_rect(canvas, 0, 0, width, height, 10, bg_color)
        canvas.create_text(width/2, height/2, text="Open Web GUI", fill=self.text_light, font=("Arial", 11, "bold"))
        canvas.bind("<Button-1>", lambda e: self.launch_browser())
        canvas.bind("<Enter>", lambda e: canvas.configure(cursor="hand2"))
        canvas.bind("<Leave>", lambda e: canvas.configure(cursor=""))

    def _server_started(self):
        """Update UI after server starts."""
        port = effective_port()
        self.server_running = True
        self.start_time = time.time()  # Reset runtime counter
        self.status_label.config(text="Server running on all interfaces", fg=self.accent_teal)
        self.url_label.config(text=f"http://127.0.0.1:{port}/")
        self.enable_canvas_button(self.launch_btn, self.button_blue)

    def restart_application(self):
        """Perform a soft restart of the application."""
        # Show restart notification
        self.status_label.config(text="Restarting...", fg=self.button_orange)
        self.root.update()

        # Close console if open
        if self.console_visible:
            self._close_console()

        # Reload configuration
        from elliotts_singular_controls.core import load_config, CONFIG, build_registry
        try:
            # Reload config from file
            new_config = load_config()
            CONFIG.singular_token = new_config.singular_token
            CONFIG.singular_stream_url = new_config.singular_stream_url
            CONFIG.tfl_app_id = new_config.tfl_app_id
            CONFIG.tfl_app_key = new_config.tfl_app_key
            CONFIG.enable_tfl = new_config.enable_tfl
            CONFIG.enable_datastream = new_config.enable_datastream
            CONFIG.theme = new_config.theme
            CONFIG.port = new_config.port
            print("[Restart] Configuration reloaded")
        except Exception as e:
            print(f"[Restart] Config reload error: {e}")

        # Rebuild the Singular registry
        try:
            build_registry()
            print("[Restart] Singular registry rebuilt")
        except Exception as e:
            print(f"[Restart] Registry rebuild skipped: {e}")

        # Clear event log
        from elliotts_singular_controls.core import COMMAND_LOG
        COMMAND_LOG.clear()
        print("[Restart] Event log cleared")

        # Reset runtime counter
        self.start_time = time.time()
        self.runtime_label.config(text="Runtime: 0s")

        # Update status
        self.status_label.config(text="Server running on all interfaces", fg=self.accent_teal)

        # Show completion message in console only (no popup)
        print(f"[Restart] Soft restart completed at {time.strftime('%H:%M:%S')}")
        print("[Restart] • Configuration reloaded")
        print("[Restart] • Registry rebuilt")
        print("[Restart] • Event log cleared")
        print("[Restart] • Runtime counter reset")

    def launch_browser(self):
        """Open the web GUI in default browser."""
        port = effective_port()
        webbrowser.open(f"http://localhost:{port}")

    def minimize_to_tray(self):
        """Minimize window to system tray."""
        self.root.withdraw()
        if not self.icon:
            image = self.create_icon_image()
            menu = pystray.Menu(
                pystray.MenuItem("Show Window", self.show_window),
                pystray.MenuItem("Launch GUI", self.launch_browser),
                pystray.MenuItem("Quit", self.quit_app)
            )
            self.icon = pystray.Icon("ESC", image, "Elliott's Singular Controls", menu)
            threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self):
        """Restore window from system tray."""
        self.root.deiconify()
        if self.icon:
            self.icon.stop()
            self.icon = None

    def on_closing(self):
        """Handle window close event."""
        if messagebox.askokcancel("Quit", "Do you want to quit? Server will stop."):
            self.quit_app()

    def quit_app(self):
        """Quit the application."""
        if self.icon:
            self.icon.stop()
        self.root.quit()
        sys.exit(0)

    def run(self):
        """Start the GUI main loop."""
        print(f"Elliott's Singular Controls v{_runtime_version()}")
        print("GUI Launcher started. Click 'Start Server' to begin.")
        self.root.mainloop()


class ConsoleRedirector:
    """Redirect stdout/stderr to a Text widget."""

    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.buffer = StringIO()

    def write(self, message):
        try:
            self.text_widget.insert(tk.END, message)
            self.text_widget.see(tk.END)
        except:
            pass  # Widget may be destroyed
        self.buffer.write(message)

    def flush(self):
        pass


class TkinterLogHandler(logging.Handler):
    """Custom logging handler that writes to a Tkinter Text widget."""

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        try:
            msg = self.format(record) + '\n'
            self.text_widget.insert(tk.END, msg)
            self.text_widget.see(tk.END)
        except:
            pass  # Widget may be destroyed


def main():
    """Entry point for GUI launcher."""
    app = SingularTweaksGUI()
    app.run()


if __name__ == "__main__":
    main()

"""
GUI Launcher for Singular Tweaks with system tray support.
"""
import os
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import scrolledtext, messagebox
from pathlib import Path
import socket
import psutil
import pystray
from PIL import Image, ImageDraw
from io import StringIO

# Import needed functions
from singular_tweaks.core import effective_port, _runtime_version


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
        self.root.title(f"Singular Tweaks v{_runtime_version()}")
        self.root.geometry("600x450")
        self.root.resizable(True, True)

        # Icon for system tray
        self.icon = None
        self.server_thread = None
        self.server_running = False
        self.console_visible = False

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_icon_image(self):
        """Create a simple icon for the system tray."""
        # Create a 64x64 image with a colored circle
        width = 64
        height = 64
        color1 = "#4da3ff"
        color2 = "#10141c"

        image = Image.new('RGB', (width, height), color2)
        dc = ImageDraw.Draw(image)
        dc.ellipse((8, 8, 56, 56), fill=color1)
        dc.text((22, 22), "ST", fill=color2)
        return image

    def setup_ui(self):
        """Setup the main UI."""
        # Header
        header_frame = tk.Frame(self.root, bg="#4da3ff", height=60)
        header_frame.pack(fill=tk.X, padx=0, pady=0)
        header_frame.pack_propagate(False)

        title_label = tk.Label(
            header_frame,
            text=f"Singular Tweaks v{_runtime_version()}",
            font=("Arial", 16, "bold"),
            bg="#4da3ff",
            fg="white"
        )
        title_label.pack(pady=15)

        # Main content frame
        content_frame = tk.Frame(self.root, bg="#f5f5f5")
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Status label
        self.status_label = tk.Label(
            content_frame,
            text="Server Status: Stopped",
            font=("Arial", 11),
            bg="#f5f5f5",
            fg="#666"
        )
        self.status_label.pack(pady=(0, 15))

        # Buttons frame
        btn_frame = tk.Frame(content_frame, bg="#f5f5f5")
        btn_frame.pack(pady=10)

        # Start Server Button
        self.start_btn = tk.Button(
            btn_frame,
            text="▶ Start Server",
            command=self.start_server,
            bg="#4da3ff",
            fg="white",
            font=("Arial", 11, "bold"),
            width=15,
            height=2,
            relief=tk.FLAT,
            cursor="hand2"
        )
        self.start_btn.grid(row=0, column=0, padx=5)

        # Launch GUI Button
        self.launch_btn = tk.Button(
            btn_frame,
            text="🌐 Launch GUI",
            command=self.launch_browser,
            bg="#28a745",
            fg="white",
            font=("Arial", 11, "bold"),
            width=15,
            height=2,
            relief=tk.FLAT,
            cursor="hand2",
            state=tk.DISABLED
        )
        self.launch_btn.grid(row=0, column=1, padx=5)

        # Stop Server Button
        self.stop_btn = tk.Button(
            btn_frame,
            text="⏹ Stop Server",
            command=self.stop_server,
            bg="#dc3545",
            fg="white",
            font=("Arial", 11, "bold"),
            width=15,
            height=2,
            relief=tk.FLAT,
            cursor="hand2",
            state=tk.DISABLED
        )
        self.stop_btn.grid(row=0, column=2, padx=5)

        # Toggle Console Button
        self.console_btn = tk.Button(
            content_frame,
            text="▼ Show Console Output",
            command=self.toggle_console,
            bg="#6c757d",
            fg="white",
            font=("Arial", 10),
            relief=tk.FLAT,
            cursor="hand2"
        )
        self.console_btn.pack(pady=(15, 5))

        # Console output (hidden by default)
        self.console_frame = tk.Frame(content_frame, bg="#f5f5f5")

        self.console = scrolledtext.ScrolledText(
            self.console_frame,
            height=12,
            bg="#1e1e1e",
            fg="#d4d4d4",
            font=("Consolas", 9),
            relief=tk.FLAT
        )
        self.console.pack(fill=tk.BOTH, expand=True)

        # Redirect stdout to console
        sys.stdout = ConsoleRedirector(self.console)
        sys.stderr = ConsoleRedirector(self.console)

    def toggle_console(self):
        """Toggle console visibility."""
        if self.console_visible:
            self.console_frame.pack_forget()
            self.console_btn.config(text="▼ Show Console Output")
            self.root.geometry("600x300")
        else:
            self.console_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
            self.console_btn.config(text="▲ Hide Console Output")
            self.root.geometry("600x550")
        self.console_visible = not self.console_visible

    def start_server(self):
        """Start the server in a background thread."""
        port = effective_port()

        # Check if port is in use and kill it
        if is_port_in_use(port):
            response = messagebox.askyesno(
                "Port In Use",
                f"Port {port} is already in use (possibly another instance).\n\n"
                "Do you want to close it and start a new instance?"
            )
            if response:
                kill_process_on_port(port)
            else:
                return

        self.status_label.config(text=f"Server Status: Starting on port {port}...", fg="#ff8c00")
        self.start_btn.config(state=tk.DISABLED)

        # Start server in background thread
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

        # Update UI after short delay
        self.root.after(2000, self._server_started)

    def _run_server(self):
        """Run the server (called in background thread)."""
        try:
            import uvicorn
            from singular_tweaks.core import app, effective_port

            # Configure uvicorn with minimal logging to avoid formatter errors
            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=effective_port(),
                log_level="info",
                access_log=True,
                log_config=None  # Disable default log config to avoid formatter errors
            )
            server = uvicorn.Server(config)
            server.run()
        except Exception as e:
            print(f"Error starting server: {e}")
            import traceback
            traceback.print_exc()

    def _server_started(self):
        """Update UI after server starts."""
        port = effective_port()
        self.server_running = True
        self.status_label.config(
            text=f"Server Status: Running on http://localhost:{port}",
            fg="#28a745"
        )
        self.launch_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL)
        self.start_btn.config(state=tk.DISABLED)

    def stop_server(self):
        """Stop the server."""
        self.server_running = False
        self.status_label.config(text="Server Status: Stopped", fg="#dc3545")
        self.launch_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        self.start_btn.config(state=tk.NORMAL)
        # Note: Uvicorn doesn't have a clean shutdown in thread mode
        # User needs to restart the app to fully stop
        messagebox.showinfo(
            "Server Stop",
            "To fully stop the server, please close and restart the application."
        )

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
            self.icon = pystray.Icon("SingularTweaks", image, "Singular Tweaks", menu)
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
        print(f"Singular Tweaks v{_runtime_version()}")
        print("GUI Launcher started. Click 'Start Server' to begin.")
        self.root.mainloop()


class ConsoleRedirector:
    """Redirect stdout/stderr to a Text widget."""

    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.buffer = StringIO()

    def write(self, message):
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.buffer.write(message)

    def flush(self):
        pass


def main():
    """Entry point for GUI launcher."""
    app = SingularTweaksGUI()
    app.run()


if __name__ == "__main__":
    main()

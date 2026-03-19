import os
import posixpath
import threading
import asyncio
import asyncssh
import tkinter as tk
from tkinter import filedialog, messagebox
import datetime
import inspect
import sys

_log_lock = threading.Lock()

def get_base_path():
    """獲取程式執行的當前目錄，相容 PyInstaller 打包後的單一執行檔環境"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def log_event(ip, message):
    log_dir = os.path.join(get_base_path(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    safe_ip = ip.replace(".", "_").replace(":", "_")
    log_file = os.path.join(log_dir, f"{safe_ip}.log")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _log_lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"Failed to write log: {e}")

server_loop = None

class LoggingSFTPServer(asyncssh.SFTPServer):
    def __init__(self, conn, chroot=None, ip="Unknown"):
        super().__init__(conn, chroot=chroot)
        self.ip = ip

    def _get_decoded_path(self, path):
        if isinstance(path, bytes):
            return path.decode('utf-8', errors='replace')
        return str(path)

    def _normalize_path(self, path):
        """Normalize client paths to something closer to OpenSSH behaviour."""
        decoded_path = self._get_decoded_path(path).strip()

        if not decoded_path or decoded_path == ".":
            normalized = "/"
        else:
            decoded_path = decoded_path.replace("\\", "/")

            # Some Windows-based SFTP clients send drive-letter paths.
            if len(decoded_path) >= 2 and decoded_path[1] == ":":
                decoded_path = decoded_path[2:]

            if not decoded_path.startswith("/"):
                decoded_path = "/" + decoded_path

            normalized = posixpath.normpath(decoded_path)
            if normalized in ("", "."):
                normalized = "/"

        return normalized.encode("utf-8") if isinstance(path, bytes) else normalized

    def _display_path(self, path):
        return self._get_decoded_path(path)

    async def _call_super(self, method_name, *args):
        method = getattr(super(), method_name)
        res = method(*args)
        if inspect.isawaitable(res):
            res = await res
        return res

    async def _run_with_logging(self, op_name, path, *args):
        normalized_path = self._normalize_path(path)
        display_path = self._display_path(normalized_path)
        log_event(self.ip, f"Request {op_name}: {display_path}")

        try:
            return await self._call_super(op_name, normalized_path, *args)
        except Exception as e:
            log_event(self.ip, f"{op_name} failed: {display_path} - Error: {e}")
            raise

    async def open(self, path, pflags, attrs):
        if (pflags & asyncssh.FXF_WRITE) and (pflags & asyncssh.FXF_READ):
            action = "Read/Write"
        elif pflags & asyncssh.FXF_WRITE:
            action = "Upload"
        elif pflags & asyncssh.FXF_READ:
            action = "Download"
        else:
            action = "Access"

        normalized_path = self._normalize_path(path)
        decoded_path = self._display_path(normalized_path)
        log_event(self.ip, f"Request {action}: {decoded_path}")

        try:
            return await self._call_super("open", normalized_path, pflags, attrs)
        except Exception as e:
            log_event(self.ip, f"{action} failed: {decoded_path} - Error: {e}")
            raise

    async def list_folder(self, path):
        return await self._run_with_logging("list_folder", path)

    async def stat(self, path):
        return await self._run_with_logging("stat", path)

    async def lstat(self, path):
        return await self._run_with_logging("lstat", path)

    async def remove(self, path):
        return await self._run_with_logging("remove", path)

    async def mkdir(self, path, attrs):
        return await self._run_with_logging("mkdir", path, attrs)

    async def rmdir(self, path):
        return await self._run_with_logging("rmdir", path)

    async def chattr(self, path, attrs):
        return await self._run_with_logging("chattr", path, attrs)

    async def realpath(self, path):
        normalized_path = self._normalize_path(path)
        display_path = self._display_path(normalized_path)
        log_event(self.ip, f"Request realpath: {display_path}")

        try:
            result = await self._call_super("realpath", normalized_path)
            if isinstance(result, (str, bytes)):
                result = self._normalize_path(result)
            log_event(self.ip, f"Resolved realpath: {display_path} -> {self._display_path(result)}")
            return result
        except Exception as e:
            log_event(self.ip, f"realpath failed: {display_path} - Error: {e}")
            raise

    async def rename(self, oldpath, newpath):
        normalized_old = self._normalize_path(oldpath)
        normalized_new = self._normalize_path(newpath)
        display_old = self._display_path(normalized_old)
        display_new = self._display_path(normalized_new)
        log_event(self.ip, f"Request rename: {display_old} -> {display_new}")

        try:
            return await self._call_super("rename", normalized_old, normalized_new)
        except Exception as e:
            log_event(self.ip, f"rename failed: {display_old} -> {display_new} - Error: {e}")
            raise

class SFTPServerAuth(asyncssh.SSHServer):
    """Handle SFTP client authentication"""
    def __init__(self, username, password, on_connect=None, on_disconnect=None):
        self.allowed_username = username
        self.allowed_password = password
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._ip = "Unknown"

    def connection_made(self, conn):
        peer = conn.get_extra_info('peername')
        if peer:
            self._ip = peer[0]
        print(f"[Info] Device connected: {self._ip}")
        log_event(self._ip, "Device connected")
        if self.on_connect:
            self.on_connect(self._ip)

    def connection_lost(self, exc):
        if exc:
            msg = f"Connection lost (Error): {exc}"
            print(f"[Error] {msg}")
        else:
            msg = "Connection closed normally"
            print(f"[Info] {msg}")
        log_event(self._ip, msg)
        if self.on_disconnect:
            self.on_disconnect(self._ip)

    def begin_auth(self, username):
        # Declare password authentication is required
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        # Validate account password
        result = username == self.allowed_username and password == self.allowed_password
        if not result:
            log_event(self._ip, f"Auth failed: username={username}")
        return result

class SFTPServerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("💻 Easy SFTP Server")
        self.root.geometry("400x550")
        
        # --- UI Layout ---
        tk.Label(root, text="Step 1: Select Save Directory", font=("Arial", 10, "bold")).pack(pady=(15, 5))
        self.dir_frame = tk.Frame(root)
        self.dir_frame.pack(fill="x", padx=20)
        self.dir_var = tk.StringVar(value=os.path.abspath("."))
        tk.Entry(self.dir_frame, textvariable=self.dir_var, state="readonly").pack(side="left", fill="x", expand=True)
        tk.Button(self.dir_frame, text="Browse...", command=self.browse_dir).pack(side="right", padx=(5,0))
        
        tk.Label(root, text="Step 2: Set Connection Info", font=("Arial", 10, "bold")).pack(pady=(15, 5))
        
        # Port
        f_port = tk.Frame(root)
        f_port.pack(fill="x", padx=60, pady=2)
        tk.Label(f_port, text="Port:", width=8, anchor="e").pack(side="left")
        self.port_var = tk.StringVar(value="2222")
        tk.Entry(f_port, textvariable=self.port_var).pack(side="left", fill="x", expand=True)
        
        # Username
        f_user = tk.Frame(root)
        f_user.pack(fill="x", padx=60, pady=2)
        tk.Label(f_user, text="Username:", width=8, anchor="e").pack(side="left")
        self.user_var = tk.StringVar(value="admin")
        tk.Entry(f_user, textvariable=self.user_var).pack(side="left", fill="x", expand=True)
        
        # Password
        f_pass = tk.Frame(root)
        f_pass.pack(fill="x", padx=60, pady=2)
        tk.Label(f_pass, text="Password:", width=8, anchor="e").pack(side="left")
        self.pass_var = tk.StringVar(value="123456")
        tk.Entry(f_pass, textvariable=self.pass_var, show="*").pack(side="left", fill="x", expand=True)
        
        # Buttons
        self.start_btn = tk.Button(root, text="▶ Start Server", bg="#4CAF50", fg="white", font=("Arial", 11, "bold"), command=self.start_server)
        self.start_btn.pack(pady=(25, 5), ipadx=10, ipady=3)
        
        self.stop_btn = tk.Button(root, text="■ Stop Server", bg="#F44336", fg="white", font=("Arial", 11, "bold"), command=self.stop_server, state="disabled")
        self.stop_btn.pack(pady=5, ipadx=10, ipady=3)

        self.status_label = tk.Label(root, text="Status: Not Running", fg="red", font=("Arial", 10))
        self.status_label.pack(pady=5)
        
        # Client List
        tk.Label(root, text="Connected Devices:", font=("Arial", 9)).pack(pady=(5, 0))
        
        list_frame = tk.Frame(root)
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        
        self.scrollbar = tk.Scrollbar(list_frame)
        self.scrollbar.pack(side="right", fill="y")
        
        self.client_listbox = tk.Listbox(list_frame, yscrollcommand=self.scrollbar.set, font=("Arial", 9))
        self.client_listbox.pack(side="left", fill="both", expand=True)
        self.scrollbar.config(command=self.client_listbox.yview)

        # Cleanup resources on window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def browse_dir(self):
        folder = filedialog.askdirectory()
        if folder:
            self.dir_var.set(folder)

    def start_server(self):
        port = self.port_var.get()
        username = self.user_var.get()
        password = self.pass_var.get()
        chroot_dir = self.dir_var.get()
        
        if not username or not password:
            messagebox.showerror("Error", "Username and password cannot be empty!")
            return
            
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            messagebox.showerror("Error", "Port number must be a number between 1 and 65535!")
            return
            
        # Start background thread to run async SFTP server, preventing GUI block
        self.server_thread = threading.Thread(
            target=self.run_asyncio_server, 
            args=(port, username, password, chroot_dir), 
            daemon=True
        )
        self.server_thread.start()
        
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text=f"Status: Running (Port: {port})", fg="green")

    def stop_server(self):
        global server_loop
        if server_loop:
            server_loop.call_soon_threadsafe(self._shutdown_asyncio, server_loop)
            
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Status: Stopped", fg="red")
        self.client_listbox.delete(0, tk.END)

    def _shutdown_asyncio(self, loop):
        async def _async_shutdown():
            if hasattr(self, 'server'):
                self.server.close()
                await self.server.wait_closed()
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.stop()
        asyncio.run_coroutine_threadsafe(_async_shutdown(), loop)

    def on_client_connect(self, ip):
        self.root.after(0, self._add_client_ui, ip)

    def on_client_disconnect(self, ip):
        self.root.after(0, self._remove_client_ui, ip)

    def _add_client_ui(self, ip):
        self.client_listbox.insert(tk.END, ip)

    def _remove_client_ui(self, ip):
        items = self.client_listbox.get(0, tk.END)
        # 為了支援同 IP 多連線，從最後面往前找，只刪除第一個找到的，避免刪錯或索引偏移
        for i in range(len(items) - 1, -1, -1):
            if items[i] == ip:
                self.client_listbox.delete(i)
                break

    def run_asyncio_server(self, port, username, password, chroot_dir):
        global server_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        server_loop = loop
        
        # Run coroutine
        try:
            loop.run_until_complete(self.serve(port, username, password, chroot_dir))
            loop.run_forever()
        except Exception as e:
            # Async runtime errors (e.g., address in use) can be popped up via main thread's after queue
            self.root.after(0, lambda: messagebox.showerror("Runtime Error", str(e)))
        finally:
            loop.close()
            server_loop = None

    async def serve(self, port, username, password, chroot_dir):
        # Generate SSH Host key for the server on first run
        key_path = os.path.join(get_base_path(), "sftp_host_key")
        if not os.path.exists(key_path):
            key = asyncssh.generate_private_key('ssh-rsa', key_size=2048)
            key.write_private_key(key_path)

        def sftp_factory(conn):
            peer = conn.get_extra_info('peername')
            ip = peer[0] if peer else "Unknown"
            return LoggingSFTPServer(conn, chroot=chroot_dir, ip=ip)

        try:
            self.server = await asyncssh.create_server(
                lambda: SFTPServerAuth(username, password, self.on_client_connect, self.on_client_disconnect),
                '', int(port),
                server_host_keys=[key_path],
                sftp_factory=sftp_factory,
                reuse_address=True
            )
        except Exception as e:
            # Port in use or other errors pop up through main thread (thread-safe)
            self.root.after(0, lambda: messagebox.showerror("Startup Failed", f"Server failed to start: {e}"))
            self.root.after(0, self.stop_server)
            raise  # Pass exception to avoid run_forever() spinning out after failure

    def on_closing(self):
        self.stop_server()
        self.root.destroy()

if __name__ == "__main__":
    # Support for Windows High DPI to prevent blurry UI
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app = SFTPServerApp(root)
    root.mainloop()

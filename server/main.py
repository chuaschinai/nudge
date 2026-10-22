#!/usr/bin/env python3
import asyncio
import argparse
import json
import logging
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import RadialGradiantColorMask
from aiohttp import web, WSMsgType
from PIL import Image, ImageColor, ImageDraw, ImageTk
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse
from pystray import Icon, Menu, MenuItem

ROOT_DIR = Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT_DIR))
if getattr(sys, "frozen", False):
    if sys.platform.startswith("win"):
        APP_CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Nudge"
    else:
        APP_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "nudge"
else:
    APP_CONFIG_DIR = ROOT_DIR
CONFIG_PATH = APP_CONFIG_DIR / "config.json"
CLIENT_DIR = RESOURCE_DIR / "server" / "client"
LOGO_PATH = RESOURCE_DIR / "nudge-logo.svg"
APP_NAME = "Nudge"
APP_VERSION = os.environ.get("NUDGE_VERSION", "1.0.0")
LOGGER = logging.getLogger("nudge")


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def create_qr_image(data: str, size: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(radius_ratio=0.35),
        color_mask=RadialGradiantColorMask(
            back_color=(17, 24, 32),
            center_color=(105, 210, 255),
            edge_color=(82, 116, 237),
        ),
    ).get_image().convert("RGB")
    return image.resize((size, size), Image.Resampling.NEAREST)


def create_logo_image(size: int) -> Image.Image:
    svg = ET.parse(LOGO_PATH).getroot()
    namespace = "{http://www.w3.org/2000/svg}"
    view_box = [float(value) for value in svg.attrib["viewBox"].split()]
    _, _, source_width, source_height = view_box
    scale = size * 2 / max(source_width, source_height)
    width = round(source_width * scale)
    height = round(source_height * scale)

    gradient = svg.find(f"{namespace}defs/{namespace}linearGradient")
    background = svg.find(f"{namespace}rect")
    cursor = svg.find(f"{namespace}defs/{namespace}path[@id='cursor']")
    if gradient is None or background is None or cursor is None:
        raise ValueError("The Nudge SVG has an unsupported logo structure")

    stops = [
        (
            float(stop.attrib["offset"].rstrip("%")) / (100 if stop.attrib["offset"].endswith("%") else 1),
            ImageColor.getrgb(stop.attrib["stop-color"]),
        )
        for stop in gradient.findall(f"{namespace}stop")
    ]
    if len(stops) < 2:
        raise ValueError("The Nudge logo gradient must have at least two stops")

    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = ((x / max(1, width - 1)) + (y / max(1, height - 1))) / 2
            for index in range(len(stops) - 1):
                start, start_color = stops[index]
                end, end_color = stops[index + 1]
                if offset <= end:
                    ratio = min(1.0, max(0.0, (offset - start) / (end - start)))
                    color = tuple(round(a + (b - a) * ratio) for a, b in zip(start_color, end_color))
                    break
            else:
                color = stops[-1][1]
            position = (y * width + x) * 4
            pixels[position:position + 4] = bytes((*color, 255))

    image = Image.frombytes("RGBA", (width, height), bytes(pixels))
    mask = Image.new("L", (width, height))
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=float(background.attrib["rx"]) * scale,
        fill=255,
    )
    image.putalpha(mask)

    path_tokens = re.findall(r"[MLZ]|-?\d+(?:\.\d+)?", cursor.attrib["d"])
    points: list[tuple[float, float]] = []
    index = 0
    while index < len(path_tokens):
        command = path_tokens[index]
        index += 1
        if command == "Z":
            continue
        if command not in {"M", "L"} or index + 1 >= len(path_tokens):
            raise ValueError("The Nudge logo cursor path is unsupported")
        points.append(
            (
                (float(path_tokens[index]) - view_box[0]) * scale,
                (float(path_tokens[index + 1]) - view_box[1]) * scale,
            )
        )
        index += 2

    for use in svg.findall(f"{namespace}use"):
        href = use.attrib.get("href") or use.attrib.get("{http://www.w3.org/1999/xlink}href")
        if href != "#cursor":
            continue
        translation = re.search(r"translate\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)", use.attrib.get("transform", ""))
        offset_x, offset_y = (float(value) * scale for value in translation.groups()) if translation else (0, 0)
        transformed = [(x + offset_x, y + offset_y) for x, y in points]
        overlay = Image.new("RGBA", (width, height))
        cursor_draw = ImageDraw.Draw(overlay)
        fill = use.attrib.get("fill", "none")
        stroke = use.attrib.get("stroke")
        opacity = float(use.attrib.get("stroke-opacity", "1"))
        stroke_color = (255, 255, 255, round(255 * opacity))
        stroke_width = round(float(use.attrib.get("stroke-width", "1")) * scale)
        if fill != "none":
            cursor_draw.polygon(transformed, fill=fill)
        if stroke:
            cursor_draw.line(
                [*transformed, transformed[0]],
                fill=stroke_color,
                width=stroke_width,
                joint="curve",
            )
        image = Image.alpha_composite(image, overlay)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def ensure_config() -> dict[str, Any]:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        config = {
            "port": 8765,
            "allowed_ips": [],
            "start_minimized": False,
            "autostart": False,
            "debug": False,
        }
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config

    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        config = {
            "port": 8765,
            "allowed_ips": [],
            "start_minimized": False,
            "autostart": False,
            "debug": False,
        }
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")

    config.setdefault("port", 8765)
    config.setdefault("allowed_ips", [])
    if not isinstance(config["allowed_ips"], list):
        config["allowed_ips"] = []
    config["allowed_ips"] = sorted({
        ip.strip() for ip in config["allowed_ips"]
        if isinstance(ip, str) and ip.strip()
    })
    config.pop("token", None)
    config.setdefault("start_minimized", False)
    config.setdefault("autostart", False)
    config.setdefault("debug", False)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


class NudgeServer:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.app = web.Application()
        self.loop = None
        self.connected: dict[str, dict[str, Any]] = {}
        self.mouse = pynput_mouse.Controller()
        self.keyboard = pynput_keyboard.Controller()
        self.mouse_down: dict[str, str] = {}
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self.tray_icon: Icon | None = None
        self.window_thread: threading.Thread | None = None
        self.window_root: Any | None = None
        self.window_actions: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.window_closed = threading.Event()
        self.window_closed.set()
        self.shutdown_event: asyncio.Event | None = None
        self.shutdown_requested = False
        self.terminal_approval_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.index_page)
        self.app.router.add_get("/ws", self.websocket_handler)
        self.app.router.add_get("/qr", self.qr_page)
        self.app.router.add_get("/nudge-logo.svg", self.logo_file)
        self.app.router.add_static("/static/", str(CLIENT_DIR), show_index=True)
        self.app.on_shutdown.append(self.shutdown)

    def build_url(self) -> str:
        return f"http://{get_local_ip()}:{self.config['port']}"

    async def index_page(self, request: web.Request) -> web.Response:
        html = CLIENT_DIR / "index.html"
        if not html.exists():
            return web.Response(text="Client not found", status=500)

        response = web.FileResponse(html)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def qr_page(self, request: web.Request) -> web.Response:
        output = create_qr_image(self.build_url(), 360)
        buffer = __import__("io").BytesIO()
        output.save(buffer, format="PNG")
        return web.Response(body=buffer.getvalue(), content_type="image/png")

    async def logo_file(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(LOGO_PATH, headers={"Cache-Control": "public, max-age=3600"})

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        client_id = str(uuid.uuid4())
        self.connected[client_id] = {
            "ws": ws,
            "peer": request.remote or "unknown",
            "authed": False,
            "awaiting_approval": False,
            "device": {},
        }
        LOGGER.debug("WebSocket connected: id=%s ip=%s", client_id, request.remote)

        async for raw_msg in ws:
            if raw_msg.type == WSMsgType.TEXT:
                try:
                    msg = json.loads(raw_msg.data)
                except json.JSONDecodeError:
                    await ws.send_json({"t": "error", "msg": "Invalid JSON"})
                    continue
                await self.queue.put((client_id, msg))
            elif raw_msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR}:
                break

        self.connected.pop(client_id, None)
        held_button = self.mouse_down.pop(client_id, None)
        if held_button in {"left", "right", "middle"}:
            button = getattr(pynput_mouse.Button, held_button)
            self.mouse.release(button)
        LOGGER.debug("WebSocket disconnected: id=%s ip=%s", client_id, request.remote)
        return ws

    async def process_events(self) -> None:
        while True:
            client_id, msg = await self.queue.get()
            ws = self.connected.get(client_id, {}).get("ws")
            if ws is None:
                continue

            msg_type = str(msg.get("t", "")).lower()
            if msg_type == "hello":
                entry = self.connected[client_id]
                device = msg.get("device", {})
                if isinstance(device, dict):
                    entry["device"] = {
                        key: "".join(
                            character for character in str(device.get(key, ""))
                            if character.isprintable()
                        )[:256]
                        for key in ("model", "browser", "platform", "device_type", "user_agent")
                        if device.get(key)
                    }
                peer = str(entry["peer"])
                if peer in self.config["allowed_ips"]:
                    entry["authed"] = True
                    entry["awaiting_approval"] = False
                    LOGGER.info("Trusted device connected: ip=%s", peer)
                    await ws.send_json({"t": "ok"})
                else:
                    entry["awaiting_approval"] = True
                    LOGGER.info("Connection approval requested: ip=%s", peer)
                    await ws.send_json({"t": "approval_pending"})
                    device_summary = self._device_summary(entry["device"])
                    if not sys.platform.startswith("win") and sys.stdin.isatty():
                        self.terminal_approval_queue.put((client_id, peer, device_summary))
                    elif not sys.platform.startswith("win"):
                        LOGGER.warning("Approve %s from the desktop app or run the server in a terminal", peer)
                continue

            if not self.connected.get(client_id, {}).get("authed", False):
                await ws.send_json({"t": "error", "msg": "Connection approval required"})
                continue

            try:
                await self._dispatch_command(client_id, msg_type, msg)
                LOGGER.debug("Command processed: ip=%s type=%s", self.connected[client_id]["peer"], msg_type)
            except Exception as exc:  # pragma: no cover - defensive layer
                LOGGER.exception("Failed to process command %s", msg_type)
                await ws.send_json({"t": "error", "msg": str(exc)})

    @staticmethod
    def _device_summary(device: dict[str, Any]) -> str:
        return " · ".join(
            str(device[key])
            for key in ("model", "device_type", "browser", "platform")
            if device.get(key)
        ) or "Device details unavailable"

    async def _dispatch_command(self, client_id: str, msg_type: str, msg: dict[str, Any]) -> None:
        button_map = {
            "left": pynput_mouse.Button.left,
            "right": pynput_mouse.Button.right,
            "middle": pynput_mouse.Button.middle,
        }

        if msg_type == "move":
            dx = int(msg.get("dx", 0))
            dy = int(msg.get("dy", 0))
            self.mouse.move(dx, dy)
            return

        if msg_type == "click":
            button = button_map.get(msg.get("button", "left"), pynput_mouse.Button.left)
            self.mouse.click(button)
            return

        if msg_type == "dblclick":
            self.mouse.click(pynput_mouse.Button.left, 2)
            return

        if msg_type == "down":
            button_name = msg.get("button", "left")
            button = button_map.get(button_name, pynput_mouse.Button.left)
            self.mouse.press(button)
            self.mouse_down[client_id] = button_name
            return

        if msg_type == "up":
            button_name = msg.get("button", "left")
            button = button_map.get(button_name, pynput_mouse.Button.left)
            self.mouse.release(button)
            self.mouse_down.pop(client_id, None)
            return

        if msg_type == "scroll":
            dx = int(msg.get("dx", 0))
            dy = int(msg.get("dy", 0))
            self.mouse.scroll(dx, dy)
            return

        if msg_type == "text":
            value = str(msg.get("value", ""))
            if value:
                self.keyboard.type(value)
            return

        if msg_type == "key":
            key_name = str(msg.get("key", "")).lower()
            special_keys = {
                "backspace": pynput_keyboard.Key.backspace,
                "enter": pynput_keyboard.Key.enter,
                "tab": pynput_keyboard.Key.tab,
                "esc": pynput_keyboard.Key.esc,
                "f5": pynput_keyboard.Key.f5,
                "delete": pynput_keyboard.Key.delete,
                "up": pynput_keyboard.Key.up,
                "down": pynput_keyboard.Key.down,
                "left": pynput_keyboard.Key.left,
                "right": pynput_keyboard.Key.right,
                "home": pynput_keyboard.Key.home,
                "end": pynput_keyboard.Key.end,
                "pageup": pynput_keyboard.Key.page_up,
                "pagedown": pynput_keyboard.Key.page_down,
            }
            if key_name in special_keys:
                self.keyboard.press(special_keys[key_name])
                self.keyboard.release(special_keys[key_name])
                return
            if key_name:
                self.keyboard.press(key_name)
                self.keyboard.release(key_name)
            return

        raise ValueError(f"Unsupported message type: {msg_type}")

    def restore_window(self) -> None:
        root = self.window_root
        if root is not None:
            self.window_actions.put(("restore", None))
            return
        self.show_window()

    def show_window(self) -> None:
        if sys.platform.startswith("win"):
            def create_window() -> None:
                root = None
                try:
                    import customtkinter as ctk
                    import tkinter as tk

                    ctk.set_appearance_mode("dark")
                    ctk.set_default_color_theme("blue")
                    root = ctk.CTk()
                    self.window_closed.clear()
                    self.window_root = root
                    root.title(APP_NAME)
                    available_height = root.winfo_screenheight() - 60
                    window_height = min(680, available_height)
                    qr_size = min(260, max(150, window_height - 420))
                    window_x = max(0, (root.winfo_screenwidth() - 500) // 2)
                    window_y = max(0, (root.winfo_screenheight() - window_height) // 2)
                    root.geometry(f"500x{window_height}+{window_x}+{window_y}")
                    root.minsize(460, min(580, window_height))
                    root.protocol("WM_DELETE_WINDOW", root.withdraw)

                    def process_window_actions() -> None:
                        while True:
                            try:
                                action, payload = self.window_actions.get_nowait()
                            except queue.Empty:
                                break
                            if action == "quit":
                                try:
                                    root.destroy()
                                except Exception:
                                    LOGGER.exception("Failed to close the desktop window")
                                    root.quit()
                                return
                            if action == "restore":
                                root.deiconify()
                                root.lift()
                                root.focus_force()
                            elif action == "refresh":
                                current_pending, current_devices = payload
                                render_pending(current_pending)
                                render_devices(current_devices)
                        root.after(100, process_window_actions)

                    root.after(100, process_window_actions)

                    ctk.CTkButton(
                        root,
                        text="Exit",
                        width=54,
                        height=30,
                        fg_color="#633333",
                        hover_color="#7b3e3e",
                        command=self.quit,
                    ).pack(anchor="w", padx=24, pady=(12, 0))

                    header = ctk.CTkFrame(root, fg_color="transparent")
                    header.pack(fill="x", padx=24, pady=(8, 8))
                    brand = ctk.CTkFrame(header, fg_color="transparent")
                    brand.pack()
                    logo_image = create_logo_image(64)
                    window_icon = ImageTk.PhotoImage(logo_image, master=root)
                    icon_path = Path(tempfile.gettempdir()) / "nudge-window-icon.ico"
                    try:
                        create_logo_image(256).save(
                            icon_path,
                            format="ICO",
                            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
                        )
                        root.iconbitmap(str(icon_path))
                    except (OSError, tk.TclError):
                        LOGGER.exception("Failed to set the desktop title-bar icon; using the image fallback")
                        root.iconphoto(True, window_icon)
                    logo_photo = ctk.CTkImage(
                        light_image=logo_image,
                        dark_image=logo_image,
                        size=(38, 38),
                    )
                    ctk.CTkLabel(brand, text="", image=logo_photo).pack(side="left", padx=(0, 8))
                    brand_text = ctk.CTkFrame(brand, fg_color="transparent")
                    brand_text.pack(side="left")
                    ctk.CTkLabel(
                        brand_text,
                        text=APP_NAME,
                        font=ctk.CTkFont(size=24, weight="bold"),
                    ).pack(anchor="w")
                    ctk.CTkLabel(
                        brand_text,
                        text=f"v{APP_VERSION}",
                        text_color="#808080",
                        font=ctk.CTkFont(size=10),
                    ).pack(anchor="w")
                    ctk.CTkLabel(
                        header,
                        text="Scan the QR code to connect your phone",
                        text_color="#a3a3a3",
                        font=ctk.CTkFont(size=13),
                    ).pack(pady=(4, 0))

                    qr_image = create_qr_image(self.build_url(), qr_size)
                    qr_photo = ctk.CTkImage(
                        light_image=qr_image,
                        dark_image=qr_image,
                        size=(qr_size, qr_size),
                    )
                    ctk.CTkLabel(root, text="", image=qr_photo).pack(pady=(10, 8))
                    root.qr_photo = qr_photo
                    ctk.CTkLabel(
                        root,
                        text=self.build_url(),
                        text_color="#8ba9cc",
                        font=ctk.CTkFont(size=15),
                    ).pack(pady=(0, 18))
                    root.logo_photo = logo_photo
                    root.window_icon = window_icon

                    autostart_var = ctk.BooleanVar(value=bool(self.config.get("autostart", False)))

                    def update_autostart() -> None:
                        from tkinter import messagebox

                        enabled = bool(autostart_var.get())
                        try:
                            self.set_autostart_enabled(enabled)
                        except (OSError, ImportError, ValueError) as exc:
                            LOGGER.exception("Failed to update system startup setting")
                            autostart_var.set(not enabled)
                            messagebox.showerror(
                                "Autostart setting failed",
                                f"Could not update the startup setting:\n{exc}",
                                parent=root,
                            )

                    ctk.CTkCheckBox(
                        root,
                        text="Start with system",
                        variable=autostart_var,
                        command=update_autostart,
                    ).pack(anchor="w", padx=24, pady=(0, 12))

                    devices_header = ctk.CTkFrame(root, fg_color="transparent")
                    devices_header.pack(fill="x", padx=24, pady=(0, 8))
                    ctk.CTkLabel(
                        devices_header,
                        text="Connected devices",
                        font=ctk.CTkFont(size=16, weight="bold"),
                    ).pack(side="left")
                    devices = ctk.CTkScrollableFrame(root, corner_radius=10)
                    devices.pack(fill="both", expand=True, padx=24, pady=(0, 10))
                    device_rows: dict[str, Any] = {}
                    approval_dialog: Any | None = None
                    approval_client_id: str | None = None
                    whitelist_window: Any | None = None

                    def render_devices(current: list[tuple[str, str, str]]) -> None:
                        current_ids = {client_id for client_id, _, _ in current}
                        for client_id in tuple(device_rows):
                            if client_id != "__empty__" and client_id not in current_ids:
                                device_rows.pop(client_id).destroy()

                        if not current and not device_rows:
                            empty_label = device_rows.get("__empty__")
                            if empty_label is None:
                                empty_label = ctk.CTkLabel(
                                    devices,
                                    text="No devices connected",
                                    text_color="#a3a3a3",
                                )
                                empty_label.pack(pady=18)
                                device_rows["__empty__"] = empty_label
                        elif "__empty__" in device_rows:
                            device_rows.pop("__empty__").destroy()

                        for client_id, peer, details in current:
                            if client_id in device_rows:
                                continue
                            row = ctk.CTkFrame(devices, corner_radius=8)
                            row.pack(fill="x", padx=4, pady=4)
                            ctk.CTkLabel(row, text=f"{peer}\n{details}", anchor="w", justify="left").pack(
                                side="left", fill="x", expand=True, padx=12, pady=10
                            )
                            ctk.CTkButton(
                                row,
                                text="Disconnect",
                                width=100,
                                fg_color="#633333",
                                hover_color="#7b3e3e",
                                command=lambda device_id=client_id: self.disconnect_device(device_id),
                            ).pack(side="right", padx=8, pady=7)
                            device_rows[client_id] = row

                    def close_approval_dialog() -> None:
                        nonlocal approval_dialog, approval_client_id
                        if approval_dialog is not None:
                            try:
                                approval_dialog.grab_release()
                                approval_dialog.destroy()
                            except Exception:
                                LOGGER.exception("Failed to close the connection approval dialog")
                        approval_dialog = None
                        approval_client_id = None

                    def respond_to_connection(client_id: str, allow: bool) -> None:
                        if allow:
                            self.approve_device(client_id)
                        else:
                            self.deny_device(client_id)
                        close_approval_dialog()

                    def render_pending(current: list[tuple[str, str, str]]) -> None:
                        nonlocal approval_dialog, approval_client_id
                        if approval_dialog is not None:
                            if any(client_id == approval_client_id for client_id, _, _ in current):
                                return
                            close_approval_dialog()
                        if not current:
                            return

                        client_id, peer, details = current[0]
                        approval_client_id = client_id
                        approval_dialog = ctk.CTkToplevel(root)
                        dialog = approval_dialog
                        dialog.title("Connection request")
                        dialog.geometry("390x240")
                        dialog.resizable(False, False)
                        dialog.transient(root)
                        ctk.CTkLabel(
                            dialog,
                            text="Allow this connection?",
                            font=ctk.CTkFont(size=20, weight="bold"),
                        ).pack(padx=24, pady=(24, 12))
                        ctk.CTkLabel(
                            dialog,
                            text=f"IP address: {peer}\n{details}",
                            justify="center",
                            wraplength=340,
                        ).pack(padx=24, pady=(0, 20))
                        actions = ctk.CTkFrame(dialog, fg_color="transparent")
                        actions.pack(fill="x", padx=24, pady=(0, 20))
                        ctk.CTkButton(
                            actions,
                            text="Allow",
                            command=lambda: respond_to_connection(client_id, True),
                        ).pack(side="left", expand=True, padx=(0, 6))
                        ctk.CTkButton(
                            actions,
                            text="Deny",
                            fg_color="#633333",
                            hover_color="#7b3e3e",
                            command=lambda: respond_to_connection(client_id, False),
                        ).pack(side="left", expand=True, padx=(6, 0))
                        dialog.protocol(
                            "WM_DELETE_WINDOW",
                            lambda: respond_to_connection(client_id, False),
                        )
                        dialog.grab_set()
                        dialog.focus_force()

                    def open_whitelist() -> None:
                        nonlocal whitelist_window
                        if whitelist_window is not None:
                            try:
                                if whitelist_window.winfo_exists():
                                    whitelist_window.lift()
                                    whitelist_window.focus_force()
                                    return
                            except Exception:
                                LOGGER.exception("Failed to focus the whitelist window")
                            whitelist_window = None

                        window = ctk.CTkToplevel(root)
                        whitelist_window = window
                        window.title("Allowed IPs")
                        window.geometry("360x420")
                        window.transient(root)
                        window.grab_set()
                        ctk.CTkLabel(
                            window,
                            text="Allowed IP addresses",
                            font=ctk.CTkFont(size=18, weight="bold"),
                        ).pack(padx=20, pady=(20, 10))
                        allowed_list = ctk.CTkScrollableFrame(window, corner_radius=10)
                        allowed_list.pack(fill="both", expand=True, padx=20, pady=8)

                        def refresh_whitelist() -> None:
                            for child in allowed_list.winfo_children():
                                child.destroy()
                            ips = list(self.config.get("allowed_ips", []))
                            if not ips:
                                ctk.CTkLabel(
                                    allowed_list,
                                    text="No allowed IP addresses",
                                    text_color="#a3a3a3",
                                ).pack(pady=16)
                            for ip in ips:
                                ctk.CTkLabel(allowed_list, text=ip, anchor="w").pack(
                                    fill="x", padx=10, pady=8
                                )

                        def clear_whitelist() -> None:
                            from tkinter import messagebox

                            if messagebox.askyesno(
                                "Clear allowed IPs",
                                "Remove all allowed IP addresses and disconnect their active sessions?",
                                parent=window,
                            ):
                                self.clear_whitelist()
                                window.after(150, refresh_whitelist)

                        def close_whitelist() -> None:
                            nonlocal whitelist_window
                            try:
                                window.grab_release()
                                window.destroy()
                            except Exception:
                                LOGGER.exception("Failed to close the whitelist window")
                            whitelist_window = None

                        window.protocol("WM_DELETE_WINDOW", close_whitelist)
                        ctk.CTkButton(
                            window,
                            text="Clear allowed IPs",
                            fg_color="#633333",
                            hover_color="#7b3e3e",
                            command=clear_whitelist,
                        ).pack(fill="x", padx=20, pady=(8, 20))
                        refresh_whitelist()

                    ctk.CTkButton(
                        devices_header,
                        text="Whitelist",
                        width=92,
                        command=open_whitelist,
                    ).pack(side="right", padx=(0, 12))

                    def refresh_devices() -> None:
                        if self.loop is not None and self.loop.is_running():
                            def snapshot() -> None:
                                current_pending = [
                                    (
                                        client_id,
                                        str(entry.get("peer", "unknown")),
                                        self._device_summary(entry.get("device", {})),
                                    )
                                    for client_id, entry in self.connected.items()
                                    if entry.get("awaiting_approval")
                                ]
                                current = [
                                    (
                                        client_id,
                                        str(entry.get("peer", "unknown")),
                                        self._device_summary(entry.get("device", {})),
                                    )
                                    for client_id, entry in self.connected.items()
                                    if entry.get("authed")
                                ]
                                self.window_actions.put(
                                    ("refresh", (current_pending, current))
                                )

                            self.loop.call_soon_threadsafe(snapshot)
                        root.after(1000, refresh_devices)

                    refresh_devices()
                    LOGGER.debug("Desktop window displayed")
                    root.mainloop()
                except Exception:
                    LOGGER.exception("Failed to create desktop window")
                finally:
                    if root is not None and self.window_root is root:
                        self.window_root = None
                    self.window_closed.set()

            if self.window_thread and self.window_thread.is_alive():
                return
            self.window_thread = threading.Thread(target=create_window, daemon=True)
            self.window_thread.start()

    def disconnect_device(self, client_id: str) -> None:
        if self.loop is None or not self.loop.is_running():
            LOGGER.warning("Cannot disconnect device: server is unavailable")
            return
        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._disconnect_device(client_id))
        )

    async def _disconnect_device(self, client_id: str) -> None:
        entry = self.connected.get(client_id)
        if entry is None:
            LOGGER.info("Device already disconnected: id=%s", client_id)
            return
        ws = entry.get("ws")
        if ws is not None and not ws.closed:
            await ws.close(code=1000, message=b"Disconnected by server operator")
            LOGGER.info("Device disconnected by user: ip=%s", entry.get("peer", "unknown"))

    def approve_device(self, client_id: str) -> None:
        if self.loop is None or not self.loop.is_running():
            LOGGER.warning("Cannot approve device: server is unavailable")
            return
        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._approve_device(client_id))
        )

    async def _approve_device(self, client_id: str) -> None:
        entry = self.connected.get(client_id)
        if entry is None or not entry.get("awaiting_approval"):
            return
        peer = str(entry.get("peer", "unknown"))
        ws = entry.get("ws")
        if peer == "unknown" or ws is None or ws.closed:
            LOGGER.warning("Cannot add connection to whitelist: client IP is unavailable")
            if ws is not None and not ws.closed:
                await ws.close(code=1008, message=b"Client IP unavailable")
            return

        if peer not in self.config["allowed_ips"]:
            updated_config = dict(self.config)
            updated_config["allowed_ips"] = sorted([*self.config["allowed_ips"], peer])
            try:
                CONFIG_PATH.write_text(json.dumps(updated_config, indent=2), encoding="utf-8")
            except OSError:
                LOGGER.exception("Failed to save the approved IP address")
                await ws.send_json({"t": "error", "msg": "Could not save device approval"})
                await ws.close(code=1011, message=b"Could not save device approval")
                return
            self.config["allowed_ips"] = updated_config["allowed_ips"]
        entry["authed"] = True
        entry["awaiting_approval"] = False
        await ws.send_json({"t": "ok"})
        LOGGER.info("Device approved and added to whitelist: ip=%s", peer)

    def deny_device(self, client_id: str) -> None:
        if self.loop is None or not self.loop.is_running():
            LOGGER.warning("Cannot deny device: server is unavailable")
            return
        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._deny_device(client_id))
        )

    async def _deny_device(self, client_id: str) -> None:
        entry = self.connected.get(client_id)
        if entry is None:
            return
        ws = entry.get("ws")
        if ws is not None and not ws.closed:
            await ws.send_json({"t": "error", "msg": "Connection request denied"})
            await ws.close(code=1008, message=b"Connection request denied")
        LOGGER.info("Connection request denied: ip=%s", entry.get("peer", "unknown"))

    def clear_whitelist(self) -> None:
        if self.loop is None or not self.loop.is_running():
            LOGGER.warning("Cannot clear whitelist: server is unavailable")
            return
        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._clear_whitelist())
        )

    async def _clear_whitelist(self) -> None:
        updated_config = dict(self.config)
        updated_config["allowed_ips"] = []
        try:
            CONFIG_PATH.write_text(json.dumps(updated_config, indent=2), encoding="utf-8")
        except OSError:
            LOGGER.exception("Failed to save the cleared whitelist")
            return
        self.config["allowed_ips"] = []
        for client_id, entry in tuple(self.connected.items()):
            if entry.get("authed"):
                entry["authed"] = False
                await self._disconnect_device(client_id)
        LOGGER.info("Allowed IP whitelist cleared")

    def run_terminal_approvals(self) -> None:
        while True:
            client_id, peer, details = self.terminal_approval_queue.get()
            try:
                answer = input(
                    f"\nConnection request from {peer} ({details}). "
                    "Allow and add to whitelist? [y/N] "
                )
            except EOFError:
                LOGGER.warning("Terminal approval input closed; pending requests cannot be approved")
                return
            if self.loop is None or not self.loop.is_running():
                return
            action = self._approve_device if answer.strip().lower() in {"y", "yes"} else self._deny_device
            self.loop.call_soon_threadsafe(
                lambda device_id=client_id, callback=action: asyncio.create_task(callback(device_id))
            )

    def _startup_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            executable = [sys.executable]
        else:
            executable = [sys.executable, str(Path(__file__).resolve())]
        return [*executable, "--start-minimized"]

    @staticmethod
    def _desktop_entry_quote(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        return f'"{escaped}"'

    def _configure_system_autostart(self, enabled: bool) -> None:
        if sys.platform.startswith("win"):
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as startup_key:
                if enabled:
                    winreg.SetValueEx(
                        startup_key,
                        APP_NAME,
                        0,
                        winreg.REG_SZ,
                        subprocess.list2cmdline(self._startup_command()),
                    )
                else:
                    try:
                        winreg.DeleteValue(startup_key, APP_NAME)
                    except FileNotFoundError:
                        pass
        elif sys.platform.startswith("linux"):
            startup_file = Path.home() / ".config" / "autostart" / "nudge.desktop"
            if enabled:
                startup_file.parent.mkdir(parents=True, exist_ok=True)
                command = " ".join(
                    self._desktop_entry_quote(argument)
                    for argument in self._startup_command()
                )
                startup_file.write_text(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={APP_NAME}\n"
                    f"Exec={command}\n"
                    "Terminal=true\n"
                    "X-GNOME-Autostart-enabled=true\n",
                    encoding="utf-8",
                )
            else:
                try:
                    startup_file.unlink()
                except FileNotFoundError:
                    pass
        else:
            raise OSError(f"Autostart is not supported on {sys.platform}")

    def set_autostart_enabled(self, enabled: bool) -> None:
        previous_value = bool(self.config.get("autostart", False))
        self._configure_system_autostart(enabled)
        updated_config = dict(self.config)
        updated_config["autostart"] = enabled
        try:
            CONFIG_PATH.write_text(json.dumps(updated_config, indent=2), encoding="utf-8")
        except OSError:
            try:
                self._configure_system_autostart(previous_value)
            except (OSError, ImportError, ValueError):
                LOGGER.exception("Failed to roll back the system startup setting")
            raise
        self.config["autostart"] = enabled
        LOGGER.info("System autostart %s", "enabled" if enabled else "disabled")

    def quit(self) -> None:
        if self.shutdown_requested:
            return
        self.shutdown_requested = True

        if self.window_root is not None:
            self.window_actions.put(("quit", None))

        if self.loop is not None and self.loop.is_running():
            def request_shutdown() -> None:
                if self.shutdown_event is not None:
                    self.shutdown_event.set()

            self.loop.call_soon_threadsafe(request_shutdown)
        else:
            LOGGER.warning("Shutdown requested outside the server event loop")

        if self.tray_icon is not None:
            icon = self.tray_icon

            def stop_tray() -> None:
                try:
                    icon.stop()
                except Exception:
                    LOGGER.exception("Failed to stop the system tray icon")

            threading.Thread(target=stop_tray, daemon=True).start()

    async def shutdown(self, app: web.Application) -> None:
        for entry in list(self.connected.values()):
            ws = entry.get("ws")
            if ws is not None and not ws.closed:
                await ws.close(code=1000)

    def run_tray(self) -> None:
        try:
            image = create_logo_image(64)
            menu = Menu(
                MenuItem("Restore", self.restore_window),
                MenuItem("Quit", self.quit),
            )
            icon = Icon(APP_NAME, image, APP_NAME, menu=menu)
            self.tray_icon = icon
            icon.run()
        except Exception:
            LOGGER.exception("Could not start the system tray")
            self.tray_icon = None

    async def start_server(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.shutdown_event = asyncio.Event()
        if not sys.platform.startswith("win") and sys.stdin.isatty():
            threading.Thread(target=self.run_terminal_approvals, daemon=True).start()
        if self.shutdown_requested:
            self.shutdown_event.set()
        event_task = asyncio.create_task(self.process_events())
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(self.config.get("port", 8765)))
        await site.start()
        print(f"{APP_NAME} server running on {self.build_url()}")
        print(f"Client URL: {self.build_url()}")
        LOGGER.info("Server started at %s", self.build_url())

        try:
            await self.shutdown_event.wait()
        finally:
            await runner.cleanup()
            if not self.window_closed.is_set():
                closed = await asyncio.to_thread(self.window_closed.wait, 2)
                if not closed:
                    LOGGER.warning("Desktop window did not close before shutdown timed out")
            event_task.cancel()
            try:
                await event_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} server")
    parser.add_argument("--debug", action="store_true", help="Enable detailed console logging")
    parser.add_argument(
        "--start-minimized",
        action="store_true",
        help="Start without showing the desktop window",
    )
    args = parser.parse_args()
    config = ensure_config()
    logging.basicConfig(
        level=logging.DEBUG if args.debug or config.get("debug") else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)
    server = NudgeServer(config)
    if config.get("autostart"):
        try:
            server.set_autostart_enabled(True)
        except (OSError, ImportError, ValueError):
            LOGGER.exception("Could not synchronize the saved autostart setting")

    try:
        tray_thread = threading.Thread(target=server.run_tray, daemon=True)
        tray_thread.start()
    except Exception:
        LOGGER.exception("Could not start the system tray")

    if sys.platform.startswith("win") and not (args.start_minimized or config.get("start_minimized")):
        try:
            server.show_window()
        except Exception:
            LOGGER.exception("Could not open the desktop window")

    if args.debug or config.get("debug"):
        LOGGER.info("Debug mode enabled")

    try:
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        print(f"Stopping {APP_NAME} server...")


if __name__ == "__main__":
    main()

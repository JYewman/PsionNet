"""PsionNet control panel.

One window for both halves: the PPP link (which needs root) and the
downgrading proxy (which does not). Everything it shows comes from probe.py;
everything it starts comes from control.py.

Tk is not thread-safe, so all widget access happens on the main thread. Child
process output arrives through queues that are drained by `after()` ticks.
"""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

import control
import probe
import resources
import settings
import spotify_login
from psionproxy import host as machine

SERIAL = control.serial_supported()     # not on Windows: no pppd there

# Resolved at call time: frozen, the data lives outside the code archive.
def _icons_dir():
    return resources.icons_dir()

POLL_FAST = 700     # ms: process output, log tails
POLL_SLOW = 2000    # ms: ports, link state, throughput

BG = "#ececec"
OK = "#1a7f37"
WARN = "#9a6700"
BAD = "#b42318"
MUTED = "#57606a"


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.master = master
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._icons: dict[str, tk.PhotoImage] = {}
        self._load_icons()

        self.ppp = control.Runner("pppd")
        self.proxy = control.Runner("proxy")
        self.meter = probe.RateMeter()
        self.ports: list[probe.Port] = []
        self.ifaces: list[probe.Iface] = []
        self._login: spotify_login.Login | None = None
        self._ppp_log_pos = 0
        self._proxy_host = ""
        self.prefs = settings.load()
        # lsof costs ~110 ms and runs on the Tk thread, so it must not ride the
        # 2 s tick or the UI stutters. Refresh port holders on a slow cadence.
        self._holders_checked = 0.0

        self._build()
        self._tick_fast()
        self._tick_slow()
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- assets ------------------------------------------------------------

    def _load_icons(self) -> None:
        """Tk 8.6 reads PNG natively, so Pillow is not needed just to display."""
        for name in ("psion_tinted", "word_tinted",
                     "psion", "disconnected", "drive", "folder", "install",
                     "backup", "sketch", "word", "agenda", "jotter", "record",
                     "sheet", "data", "ssd", "disk", "opl",
                     "psionnet_16", "psionnet_32", "psionnet_64",
                     "psionnet_128", "psionnet_256", "psionnet_512",
                     "appicon_32", "appicon_128", "appicon_256"):
            path = _icons_dir() / f"{name}.png"
            if path.exists():
                try:
                    self._icons[name] = tk.PhotoImage(file=str(path))
                except tk.TclError:
                    pass
        # PsionNet blue, not Reconnect orange -- the two sit next to each
        # other in the Dock and were indistinguishable.
        sizes = [self._icons[n] for n in ("psionnet_256", "psionnet_128", "psionnet_512",
                                          "psionnet_64", "psionnet_32") if n in self._icons]
        if sizes:
            try:
                self.master.iconphoto(True, *sizes)
            except tk.TclError:
                pass

    def icon(self, name: str):
        return self._icons.get(name)

    # --- layout ------------------------------------------------------------

    def _build(self) -> None:
        row = 0

        header = ttk.Frame(self)
        header.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)
        head_icon = self.icon("psionnet_64") or self.icon("psionnet_32")
        if head_icon:
            ttk.Label(header, image=head_icon).grid(row=0, column=0, padx=(0, 10))
        title = ttk.Frame(header)
        title.grid(row=0, column=1, sticky="w")
        ttk.Label(title, text="PsionNet", font=("Helvetica", 17, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(title, text=("Internet for Psion devices, over the serial cable or your network"
                               if SERIAL else "Internet for the Psion netBook Pro, over your network"),
                  foreground=MUTED).grid(row=1, column=0, sticky="w")
        ttk.Label(title, text="Series 5mx  ·  Series 7  ·  netBook  ·  Revo  ·  netBook Pro",
                  foreground=MUTED, font=("Helvetica", 10)).grid(row=2, column=0, sticky="w")
        row += 1

        # --- serial port ---
        port_box = self.port_box = ttk.LabelFrame(self, text="  Serial port  ", padding=10)
        port_box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        port_box.columnconfigure(1, weight=1)
        if self.icon("drive"):
            ttk.Label(port_box, image=self.icon("drive")).grid(row=0, column=0, padx=(0, 8))
        self.port_var = tk.StringVar()
        self.port_menu = ttk.Combobox(port_box, textvariable=self.port_var,
                                      state="readonly", width=44)
        self.port_menu.grid(row=0, column=1, sticky="ew")
        ttk.Button(port_box, text="Refresh", width=9,
                   command=self.refresh_ports).grid(row=0, column=2, padx=(8, 0))
        self.port_note = ttk.Label(port_box, text="", foreground=MUTED)
        self.port_note.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))

        kind = ttk.Frame(port_box)
        kind.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(kind, text="Device:").grid(row=0, column=0)
        self.device_type = tk.StringVar(
            value=self.prefs.get("device_type", "epoc"))
        self._type_labels = {
            "Series 5mx / 7 / netBook / Revo": "epoc",
            "netBook Pro (CE, dial-up)": "ce",
            "netBook Pro (CE, network)": "ce-lan",
            "netBook Pro (PsionLX, network)": "lx-lan",
        }
        if not SERIAL:
            self._type_labels = {k: v for k, v in self._type_labels.items()
                                 if control.is_network(v)}
        self.type_box = ttk.Combobox(
            kind, state="readonly", width=32,
            values=list(self._type_labels))
        for label, key in self._type_labels.items():
            if key == self.device_type.get():
                self.type_box.set(label)
        if not self.type_box.get():
            self.type_box.current(0)
        self.type_box.grid(row=0, column=1, padx=(6, 0))
        self.type_note = ttk.Label(kind, text="", foreground=MUTED,
                                   font=("Helvetica", 10))
        self.type_note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.type_box.bind("<<ComboboxSelected>>", lambda e: self._type_changed())
        self.free_btn = ttk.Button(port_box, text="Quit Reconnect to free the port",
                                   command=self.free_port)
        if not SERIAL:
            # Only devices on the network: no port to pick, nothing to connect.
            for w in port_box.grid_slaves(row=0) + [self.port_note]:
                w.grid_remove()
            port_box.configure(text="  Device  ")
        row += 1

        # --- link ---
        link_box = ttk.LabelFrame(self, text="  Connection  ", padding=10)
        link_box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        link_box.columnconfigure(1, weight=1)
        self.link_icon = ttk.Label(link_box, image=self.icon("disconnected"))
        # psion.png is a macOS template image (LA mode); untinted it renders as a
        # near-black silhouette, so the baked-tint variant is used instead.
        self.link_icon.grid(row=0, column=0, rowspan=2, padx=(0, 8))
        self.link_state = ttk.Label(link_box, text="Not connected",
                                    font=("Helvetica", 12, "bold"))
        self.link_state.grid(row=0, column=1, sticky="w")
        self.link_detail = ttk.Label(link_box, text="", foreground=MUTED)
        self.link_detail.grid(row=1, column=1, sticky="w")
        self.link_btn = ttk.Button(link_box, text="Connect", width=12,
                                   command=self.toggle_link)
        self.link_btn.grid(row=0, column=2, rowspan=2, padx=(8, 0))
        # Network mode: which of this Mac's networks the proxy listens on.
        self.iface_row = ttk.Frame(link_box)
        ttk.Label(self.iface_row, text="Listen on:").grid(row=0, column=0)
        self.iface_box = ttk.Combobox(self.iface_row, state="readonly", width=40)
        self.iface_box.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(self.iface_row, text="Refresh", width=9,
                   command=self.refresh_ifaces).grid(row=0, column=2, padx=(8, 0))
        row += 1

        # --- proxy ---
        proxy_box = ttk.LabelFrame(self, text="  Web proxy  ", padding=10)
        proxy_box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        proxy_box.columnconfigure(1, weight=1)
        self.proxy_icon = ttk.Label(proxy_box, image=self.icon("word_tinted") or self.icon("sketch"))
        self.proxy_icon.grid(row=0, column=0, rowspan=2, padx=(0, 8))
        self.proxy_state = ttk.Label(proxy_box, text="Stopped",
                                     font=("Helvetica", 12, "bold"))
        self.proxy_state.grid(row=0, column=1, sticky="w")
        self.proxy_detail = ttk.Label(proxy_box, text="", foreground=MUTED)
        self.proxy_detail.grid(row=1, column=1, sticky="w")
        self.proxy_btn = ttk.Button(proxy_box, text="Start", width=12,
                                    command=self.toggle_proxy)
        self.proxy_btn.grid(row=0, column=2, rowspan=2, padx=(8, 0))

        opts = ttk.Frame(proxy_box)
        opts.grid(row=2, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(opts, text="Detail:").grid(row=0, column=0)
        self.fidelity = tk.StringVar(value=self.prefs.get("fidelity", "medium"))
        ttk.Combobox(opts, textvariable=self.fidelity, state="readonly", width=8,
                     values=("lite", "medium", "full")).grid(row=0, column=1, padx=(4, 14))
        self.images = tk.BooleanVar(value=bool(self.prefs.get("images", True)))
        ttk.Checkbutton(opts, text="Images", variable=self.images).grid(row=0, column=2)
        row += 1

        # --- Spotify, for the PsionLX app: one line that unfolds when clicked ---
        self.spot_box = ttk.Frame(self)
        self.spot_box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        self.spot_box.columnconfigure(0, weight=1)
        hand = "pointinghand" if sys.platform == "darwin" else "hand2"
        head = ttk.Frame(self.spot_box, cursor=hand)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        self.spot_arrow = ttk.Label(head, text="\u25b8", width=2, cursor=hand)
        self.spot_arrow.grid(row=0, column=0, sticky="w")
        ttk.Label(head, text="Spotify for the netBook Pro (PsionLX)", cursor=hand,
                  font=("Helvetica", 13, "bold")).grid(row=0, column=1, sticky="w")
        self.spot_summary = ttk.Label(head, text="", foreground=MUTED, cursor=hand)
        self.spot_summary.grid(row=0, column=2, sticky="e")
        for w in (head, *head.winfo_children()):
            w.bind("<Button-1>", lambda e: self.toggle_spotify_section())
        self.spot_body = ttk.Frame(self.spot_box, padding=(22, 8, 0, 0))
        self.spot_body.grid(row=1, column=0, sticky="ew")
        self.spot_body.columnconfigure(1, weight=1)
        ttk.Label(self.spot_body, text="Client ID:").grid(row=0, column=0, sticky="w")
        self.client_id = tk.StringVar(value=self.prefs.get("spotify_client_id", ""))
        ttk.Entry(self.spot_body, textvariable=self.client_id, width=36).grid(
            row=0, column=1, sticky="ew", padx=(6, 0))
        self.spot_btn = ttk.Button(self.spot_body, text="Log in", width=12,
                                   command=self.toggle_spotify_login)
        self.spot_btn.grid(row=0, column=2, padx=(8, 0))
        self.spot_state = ttk.Label(self.spot_body, text="", foreground=MUTED)
        self.spot_state.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(self.spot_body, foreground=MUTED, font=("Helvetica", 10), justify="left",
                  text=("Needs Spotify Premium and your own Client ID: create an app at "
                        "developer.spotify.com/dashboard,\nadd the redirect URI "
                        "http://127.0.0.1:8897/callback, choose Web API, and paste its "
                        "Client ID here.")).grid(row=2, column=0, columnspan=3,
                                                 sticky="w", pady=(6, 0))
        self._spot_row = row
        self.spot_open = bool(self.prefs.get("spotify_open", False))
        self._show_spotify_section()
        row += 1

        # --- logs ---
        tabs = ttk.Notebook(self)
        tabs.grid(row=row, column=0, sticky="nsew")
        self.rowconfigure(row, weight=1)
        self.ppp_text = self._log_tab(tabs, "Connection log")
        self.proxy_text = self._log_tab(tabs, "Proxy log")
        row += 1

        self.status = ttk.Label(self, text="", foreground=MUTED, anchor="w")
        self.status.grid(row=row, column=0, sticky="ew", pady=(8, 0))

        if SERIAL:
            self.refresh_ports()
        self.refresh_ifaces()
        self._type_changed()

    def _log_tab(self, notebook: ttk.Notebook, label: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, height=11, wrap="none",
                       font=("Menlo", 10) if machine.MAC else "TkFixedFont",
                       background="#1e1e1e", foreground="#d4d4d4",
                       insertbackground="#d4d4d4", relief="flat",
                       highlightthickness=0)
        text.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=bar.set, state="disabled")
        notebook.add(frame, text=label)
        return text

    def log(self, widget: tk.Text, lines) -> None:
        if not lines:
            return
        widget.configure(state="normal")
        for line in lines:
            widget.insert("end", line + "\n")
        # Keep the buffer bounded; a long session would otherwise grow forever.
        if int(widget.index("end-1c").split(".")[0]) > 600:
            widget.delete("1.0", "200.0")
        widget.see("end")
        widget.configure(state="disabled")

    # --- actions -----------------------------------------------------------

    def refresh_ports(self, with_holders: bool = True) -> None:
        import time
        previous = {p.device: p.busy_by for p in self.ports}
        self.ports = probe.list_ports()
        if with_holders:
            for port in self.ports:
                port.busy_by = probe.port_holder(port.device)
            self._holders_checked = time.time()
        else:
            for port in self.ports:
                port.busy_by = previous.get(port.device, "")
        labels = [f"{p.name}  —  {p.device}" for p in self.ports]
        self.port_menu["values"] = labels
        if not self.ports:
            self.port_var.set("")
            self.port_note.configure(text="No serial ports found.", foreground=BAD)
            return
        if self.port_var.get() not in labels:
            remembered = self.prefs.get("last_good_device") or self.prefs.get("device")
            best = next((i for i, p in enumerate(self.ports) if p.device == remembered), None)
            if best is None:
                best = next((i for i, p in enumerate(self.ports) if p.likely_psion), 0)
            self.port_menu.current(best)
        self._update_port_note()

    def refresh_ifaces(self) -> None:
        self.ifaces = probe.lan_interfaces()
        self.iface_box["values"] = [i.label for i in self.ifaces]
        if not self.ifaces:
            self.iface_box.set("")
            return
        if self.iface_box.get() not in self.iface_box["values"]:
            want = self.prefs.get("lan_iface") or probe.uplink_interface()
            best = next((n for n, i in enumerate(self.ifaces) if i.name == want), 0)
            self.iface_box.current(best)

    def selected_iface(self) -> probe.Iface | None:
        idx = self.iface_box.current()
        if 0 <= idx < len(self.ifaces):
            return self.ifaces[idx]
        return None

    # --- Spotify -----------------------------------------------------------

    def toggle_spotify_login(self) -> None:
        if spotify_login.logged_in():
            if messagebox.askyesno("Log out of Spotify?",
                                   "PsionNet will forget its Spotify login. The netBook "
                                   "Pro's Spotify app stops working until you log in "
                                   "again."):
                spotify_login.log_out()
            return
        cid = self.client_id.get().strip()
        if len(cid) < 16:
            messagebox.showinfo(
                "Client ID needed",
                "Paste your Spotify app's Client ID first.\n\n"
                "Create one at developer.spotify.com/dashboard: any name, the "
                "redirect URI http://127.0.0.1:8897/callback, and Web API ticked. "
                "It needs a Spotify Premium account.")
            return
        self.prefs["spotify_client_id"] = cid
        settings.save(self.prefs)
        self._login = spotify_login.Login(cid)
        self._login.start()
        self._spotify_show("Waiting for you to sign in in the browser...", WARN, "signing in...")

    def toggle_spotify_section(self) -> None:
        self.spot_open = not self.spot_open
        self._show_spotify_section()

    def _show_spotify_section(self) -> None:
        self.spot_arrow.configure(text="\u25be" if self.spot_open else "\u25b8")
        if self.spot_open:
            self.spot_body.grid()
        else:
            self.spot_body.grid_remove()

    def _spotify_show(self, detail: str, colour: str, summary: str) -> None:
        """The full state inside the section, a short one on its folded line."""
        self.spot_state.configure(text=detail, foreground=colour)
        self.spot_summary.configure(text=summary, foreground=colour)

    def _spotify_tick(self) -> None:
        if self._login is not None:
            try:
                ok, msg = self._login.results.get_nowait()
            except Exception:
                ok = None
            if ok is not None:
                self._login = None
                if not ok:
                    messagebox.showerror("Spotify", msg)
        if self._login is not None:
            return
        logged = spotify_login.logged_in()
        self.spot_btn.configure(text="Log out" if logged else "Log in")
        if not logged:
            self._spotify_show("Not logged in.", MUTED,
                               "not logged in" if self.client_id.get().strip()
                               else "not set up \u2014 click to set up")
            return
        st = spotify_login.status()
        if not self.proxy.running or st.get("stale"):
            self._spotify_show("Logged in. Start the proxy to bring the netBook Pro speaker "
                               "online.", MUTED, "logged in \u00b7 proxy stopped")
            return
        bits = [f"Logged in as {st.get('user')}" if st.get("user") else "Logged in"]
        if st.get("state") == "ready":
            bits.append(f"speaker \u201c{st.get('device', 'netBook Pro')}\u201d online")
            colour, summary = OK, "speaker online"
        elif st.get("state") == "login":
            bits.append(st.get("message") or "sign in to Spotify in the browser")
            colour, summary = WARN, "sign in, in the browser"
        elif st.get("state") == "error":
            bits.append(st.get("message") or "the speaker could not start")
            colour, summary = BAD, "speaker not working \u2014 click for details"
        else:
            bits.append("starting the speaker...")
            colour, summary = WARN, "starting..."
        if st.get("listeners"):
            bits.append(f"{st['listeners']} listening")
            summary += f" \u00b7 {st['listeners']} listening"
        self._spotify_show("   ·   ".join(bits), colour, summary)

    def selected_port(self) -> probe.Port | None:
        idx = self.port_menu.current()
        if 0 <= idx < len(self.ports):
            return self.ports[idx]
        return None

    def _update_port_note(self) -> None:
        port = self.selected_port()
        if port is None:
            return
        self.free_btn.grid_forget()
        if port.busy_by and port.busy_by.lower().startswith("reconnect"):
            self.port_note.configure(
                text="Reconnect is holding this port. It must let go before connecting.",
                foreground=BAD)
            self.free_btn.grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))
        elif port.busy_by:
            self.port_note.configure(text=f"In use by {port.busy_by}.", foreground=WARN)
        elif port.likely_psion:
            self.port_note.configure(text="Looks like a USB serial adapter. Ready.",
                                     foreground=OK)
        else:
            self.port_note.configure(text="Ready.", foreground=MUTED)

    def selected_type(self) -> str:
        return self._type_labels.get(self.type_box.get(), "epoc")

    def _type_changed(self) -> None:
        """Explain the device-side setup, which differs completely."""
        kind = self.selected_type()
        if kind == "ce":
            self.type_note.configure(text=(
                "Dial-up connection via a modem on COM1, 19200. "
                "Turn PC Connection OFF."))
        elif kind == "ce-lan":
            self.type_note.configure(text=(
                "The netBook Pro joins your network with its own card. "
                f"Set Internet Explorer's proxy to {machine.THIS}."))
        elif kind == "lx-lan":
            self.type_note.configure(text=(
                "PsionLX on your network. Run the command below on it once; after "
                "that Firefox, Spotify and Find new software find PsionNet by themselves."))
        else:
            self.type_note.configure(text=(
                "Connection type: Direct, 115200. "
                "Turn Link to desktop OFF."))
        if hasattr(self, "iface_row"):
            self._apply_mode()

    def _apply_mode(self) -> None:
        """Serial devices use the port and the PPP link; network devices use
        neither, only the proxy on one of this computer's LAN addresses."""
        network = control.is_network(self.selected_type())
        state = "disabled" if network else "readonly"
        self.port_menu.configure(state=state)
        self.port_box.configure(text="  Device  " if network else "  Serial port  ")
        if network:
            self.iface_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=(8, 0))
            self.link_btn.configure(state="disabled")
            if not SERIAL:
                self.link_btn.grid_remove()
        else:
            self.iface_row.grid_remove()
            self.link_btn.configure(state="normal")
        if self.selected_type() == "lx-lan":
            self.spot_box.grid()
        else:
            self.spot_box.grid_remove()
        if self.proxy.running:
            self.log(self.proxy_text, ["-- device type changed: restart the proxy to apply"])

    def free_port(self) -> None:
        ok, msg = control.stop_reconnect()
        self.log(self.ppp_text, [f"-- {msg}"])
        self.after(2000, self.refresh_ports)

    def toggle_link(self) -> None:
        if control.is_network(self.selected_type()):
            return
        if control.pppd_running():
            self.link_btn.configure(state="disabled")
            ok, msg = control.ppp_stop()
            self.link_btn.configure(state="normal")
            self.log(self.ppp_text, [f"-- disconnect: {msg or 'ok'}"])
            return
        port = self.selected_port()
        if port is None:
            messagebox.showwarning("No port", "Choose a serial port first.")
            return
        if port.busy_by:
            messagebox.showwarning(
                "Port in use",
                f"{port.device} is held by {port.busy_by}.\n\n"
                "pppd would open it anyway (root ignores the exclusive-use lock) "
                "and the two would corrupt each other's data. Free the port first.")
            return
        if not probe.ppp_config_installed(self.selected_type()):
            where = ("/etc/ppp/peers/psion and the NAT hooks in /etc/ppp/ip-up.d"
                     if machine.LINUX else
                     "/etc/ppp/peers/psion, /etc/ppp/ip-up and /etc/pf.anchors/psion.nat")
            if not messagebox.askyesno(
                    "Set up PPP?",
                    "The PPP configuration is not installed yet.\n\n"
                    f"Install it now? This writes {where}, and asks for "
                    "your password."):
                return
            ok, msg = control.ppp_install()
            self.log(self.ppp_text, [f"-- install: {msg or 'ok'}"])
            if not ok:
                return
        self.link_btn.configure(state="disabled")
        ok, msg = control.ppp_start(port.device, self.selected_type())
        self.link_btn.configure(state="normal")
        if not ok:
            self.log(self.ppp_text, [f"!! {msg}"])
            if msg == "cancelled":
                # Silence here looked like the button doing nothing at all.
                self.log(self.ppp_text,
                         ["-- authorisation was dismissed; pppd was not started"])
                messagebox.showinfo(
                    "Not connected",
                    "The authorisation prompt was dismissed, so pppd did not "
                    "start.\n\nPress Connect again and enter your password.")
            else:
                messagebox.showerror("Could not start", msg)
            return
        self.log(self.ppp_text, ["-- pppd started; waiting for the Psion to connect"])

    def toggle_proxy(self) -> None:
        if self.proxy.running:
            self.proxy.stop()
            return
        kind = self.selected_type()
        if control.is_network(kind):
            iface = self.selected_iface()
            if iface is None:
                messagebox.showwarning(
                    "No network",
                    f"{machine.THIS.capitalize()} is not on a private network, so there "
                    "is nowhere for the netBook Pro to reach the proxy.")
                return
            # The LAN address, served only to that network. This proxy strips
            # TLS, so it is never offered beyond the network it sits on.
            argv = control.proxy_argv(iface.ip, 8080, self.fidelity.get(),
                                      self.images.get(), allow=iface.network,
                                      spotify=(kind == "lx-lan"), software=(kind == "lx-lan"))
            self._proxy_host = iface.ip
            self.prefs["lan_iface"] = iface.name
            settings.save(self.prefs)
        else:
            link = probe.link_state()
            # Loopback when there is no link: this proxy strips TLS, so it must
            # never be offered to the LAN.
            host = link.local_ip or "127.0.0.1"
            argv = control.proxy_argv(host, 8080, self.fidelity.get(), self.images.get())
            self._proxy_host = host
        self.proxy.start(argv)

    # --- polling -----------------------------------------------------------

    def _tick_fast(self) -> None:
        self.log(self.ppp_text, self.ppp.drain())
        self.log(self.proxy_text, self.proxy.drain())
        self._tail_ppp_log()
        self.after(POLL_FAST, self._tick_fast)

    def _tail_ppp_log(self) -> None:
        """pppd writes to its own logfile, not to our pipe, because it is
        started detached under osascript."""
        try:
            size = Path(probe.PPP_LOG).stat().st_size
        except OSError:
            return
        if self._ppp_log_pos == 0 or size < self._ppp_log_pos:
            self._ppp_log_pos = max(0, size - 2000)
        if size > self._ppp_log_pos:
            try:
                with open(probe.PPP_LOG, "r", errors="replace") as fh:
                    fh.seek(self._ppp_log_pos)
                    chunk = fh.read()
                    self._ppp_log_pos = fh.tell()
            except OSError:
                return
            noise = ("publish_entry SCDSet", "set_up_tty", "acscp",
                     "ACSCP", "Received protocol dictionaries",
                     "Committed PPP store", "lcp_reqci", "ms-wins",
                     "Starting negotiation on")
            interesting = [
                l for l in chunk.splitlines()
                if any(k in l for k in ("LCP", "IPCP", "IP address", "Connect",
                                        "terminated", "Modem hangup", "error"))
                and not any(n in l for n in noise)]
            self.log(self.ppp_text, interesting[-20:])

    def _tick_slow(self) -> None:
        link = probe.link_state()
        rate_in, rate_out = self.meter.update(link.bytes_in, link.bytes_out)
        network = control.is_network(self.selected_type())
        iface = self.selected_iface() if network else None

        if network:
            self.link_icon.configure(image=self.icon("psion_tinted") or self.icon("psion"))
            if iface is not None:
                self.link_state.configure(text="On your network", foreground=OK)
                self.link_detail.configure(
                    text=f"The proxy listens on {iface.ip}, for devices on {iface.network}")
            else:
                self.link_state.configure(text="No private network", foreground=BAD)
                self.link_detail.configure(text=f"Join {machine.THIS} to the netBook Pro's network")
            self.link_btn.configure(text="Connect")
        elif link.up:
            port = self.selected_port()
            if port is not None and self.prefs.get("last_good_device") != port.device:
                self.prefs["last_good_device"] = port.device
                settings.save(self.prefs)
            self.link_icon.configure(image=self.icon("psion_tinted") or self.icon("psion"))
            self.link_state.configure(text="Connected", foreground=OK)
            self.link_detail.configure(
                text=f"{link.local_ip} → {link.remote_ip}     "
                     f"↓{rate_out/1024:5.1f} KB/s  ↑{rate_in/1024:4.1f} KB/s     "
                     f"{link.bytes_out/1024:,.0f} KB sent")
            self.link_btn.configure(text="Disconnect")
        elif control.pppd_running():
            self.link_icon.configure(image=self.icon("disconnected"))
            self.link_state.configure(text="Waiting for the Psion", foreground=WARN)
            self.link_detail.configure(
                text=f"{link.stage}, open Web on the Psion to connect")
            self.link_btn.configure(text="Disconnect")
        else:
            self.link_icon.configure(image=self.icon("disconnected"))
            self.link_state.configure(text="Not connected", foreground=MUTED)
            self.link_detail.configure(text="")
            self.link_btn.configure(text="Connect")

        px = probe.proxy_state(host=self._proxy_host)
        running = px.running or self.proxy.running
        if running:
            self.proxy_state.configure(text="Running", foreground=OK)
            where = px.address or "starting…"
            self.proxy_detail.configure(
                text=f"{where}     {px.requests} requests"
                     + (f", {px.errors} errors" if px.errors else "")
                     + (f", {px.in_flight} in flight" if px.in_flight else ""))
            self.proxy_btn.configure(text="Stop")
        else:
            self.proxy_state.configure(text="Stopped", foreground=MUTED)
            self.proxy_detail.configure(text="")
            self.proxy_btn.configure(text="Start")

        bits = []
        if network:
            if iface is not None and self.selected_type() == "lx-lan":
                # After this, Firefox, Spotify and Find new software find PsionNet themselves.
                bits.append(f"PsionLX, once, as root:  wget -O - http://{iface.ip}:8080/lx/install | sh")
            elif iface is not None:
                bits.append(f"netBook Pro: set the browser proxy to {iface.ip} port 8080")
            self.status.configure(text="   ·   ".join(bits))
            if self.selected_type() == "lx-lan":
                self._spotify_tick()
            import time
            if SERIAL and time.time() - self._holders_checked > 10:
                self.refresh_ports(with_holders=True)
            self.after(POLL_SLOW, self._tick_slow)
            return
        bits.append("forwarding on" if probe.forwarding_enabled() else "forwarding OFF")
        uplink = probe.uplink_interface()
        if uplink:
            bits.append(f"uplink {uplink}")
        if link.up:
            bits.append("Psion: set proxy to "
                        f"{link.local_ip} port 8080")
        hijack = probe.dns_hijacked_by_vpn()
        if hijack:
            bits.append(f"WARNING: DNS routes via {hijack} (VPN) - "
                        "the device will not resolve names")
        self.status.configure(text="   ·   ".join(bits))

        import time
        if time.time() - self._holders_checked > 10:
            self.refresh_ports(with_holders=True)
        self._update_port_note()
        self.after(POLL_SLOW, self._tick_slow)

    # --- shutdown ----------------------------------------------------------

    def _on_close(self) -> None:
        port = self.selected_port()
        iface = self.selected_iface()
        self.prefs.update({
            "device": port.device if port else "",
            "device_type": self.selected_type(),
            "fidelity": self.fidelity.get(),
            "images": bool(self.images.get()),
            "geometry": self.master.winfo_geometry(),
            "lan_iface": iface.name if iface else self.prefs.get("lan_iface", ""),
            "spotify_client_id": self.client_id.get().strip(),
            "spotify_open": self.spot_open,
        })
        settings.save(self.prefs)
        if self.proxy.running:
            self.proxy.stop()
        self.master.destroy()


def main() -> None:
    if machine.WINDOWS:
        # Without this Windows draws the window at 96 dpi and scales it up,
        # blurring every letter on a high-resolution screen.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    # className names the window to Linux desktops, matching psionnet.desktop.
    root = tk.Tk(className="PsionNet")
    root.title("PsionNet")
    root.minsize(640, 600 if SERIAL else 520)

    if machine.MAC:
        # On macOS the application menu has to be built BEFORE it is attached,
        # or Tk silently discards it.
        menubar = tk.Menu(root)
        app_menu = tk.Menu(menubar, name="apple")
        menubar.add_cascade(menu=app_menu)
        root.config(menu=menubar)
    theme = "aqua" if machine.MAC else "vista" if machine.WINDOWS else "clam"
    try:
        ttk.Style().theme_use(theme)
    except tk.TclError:
        pass
    ui = App(root)
    if machine.MAC:
        # Cmd-Q bypasses WM_DELETE_WINDOW, so route it to the same teardown or
        # the proxy child outlives the window.
        root.createcommand("tk::mac::Quit", ui._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

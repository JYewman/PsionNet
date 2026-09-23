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
        self._ppp_log_pos = 0
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
                     "psionnet_128", "psionnet_256",
                     "appicon_32", "appicon_128", "appicon_256"):
            path = _icons_dir() / f"{name}.png"
            if path.exists():
                try:
                    self._icons[name] = tk.PhotoImage(file=str(path))
                except tk.TclError:
                    pass
        # PsionNet blue, not Reconnect orange -- the two sit next to each
        # other in the Dock and were indistinguishable.
        big = self._icons.get("psionnet_256") or self._icons.get("psionnet_128")
        if big is not None:
            try:
                self.master.iconphoto(True, big)
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
        ttk.Label(title, text="Internet over the serial cable, for EPOC Release 5 devices",
                  foreground=MUTED).grid(row=1, column=0, sticky="w")
        ttk.Label(title, text="Series 5mx  ·  Series 7  ·  netBook  ·  Revo",
                  foreground=MUTED, font=("Helvetica", 10)).grid(row=2, column=0, sticky="w")
        row += 1

        # --- serial port ---
        port_box = ttk.LabelFrame(self, text="  Serial port  ", padding=10)
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
        self.free_btn = ttk.Button(port_box, text="Quit Reconnect to free the port",
                                   command=self.free_port)
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

        # --- logs ---
        tabs = ttk.Notebook(self)
        tabs.grid(row=row, column=0, sticky="nsew")
        self.rowconfigure(row, weight=1)
        self.ppp_text = self._log_tab(tabs, "Connection log")
        self.proxy_text = self._log_tab(tabs, "Proxy log")
        row += 1

        self.status = ttk.Label(self, text="", foreground=MUTED, anchor="w")
        self.status.grid(row=row, column=0, sticky="ew", pady=(8, 0))

        self.refresh_ports()

    def _log_tab(self, notebook: ttk.Notebook, label: str) -> tk.Text:
        frame = ttk.Frame(notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, height=11, wrap="none", font=("Menlo", 10),
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
        labels = [f"{p.name}  —  {p.label.split('  ')[-1] if False else p.device}"
                  for p in self.ports]
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

    def free_port(self) -> None:
        ok, msg = control.stop_reconnect()
        self.log(self.ppp_text, [f"-- {msg}"])
        self.after(2000, self.refresh_ports)

    def toggle_link(self) -> None:
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
        if not probe.ppp_config_installed():
            if not messagebox.askyesno(
                    "Set up PPP?",
                    "The PPP configuration is not installed yet.\n\n"
                    "Install it now? This writes /etc/ppp/peers/psion, "
                    "/etc/ppp/ip-up and /etc/pf.anchors/psion.nat, and asks for "
                    "your password."):
                return
            ok, msg = control.ppp_install()
            self.log(self.ppp_text, [f"-- install: {msg or 'ok'}"])
            if not ok:
                return
        self.link_btn.configure(state="disabled")
        ok, msg = control.ppp_start(port.device)
        self.link_btn.configure(state="normal")
        if not ok:
            self.log(self.ppp_text, [f"!! {msg}"])
            if msg != "cancelled":
                messagebox.showerror("Could not start", msg)
            return
        self.log(self.ppp_text, ["-- pppd started; waiting for the Psion to connect"])

    def toggle_proxy(self) -> None:
        if self.proxy.running:
            self.proxy.stop()
            return
        link = probe.link_state()
        # Loopback when there is no link: this proxy strips TLS, so it must
        # never be offered to the LAN.
        host = link.local_ip or "127.0.0.1"
        argv = control.proxy_argv(host, 8080, self.fidelity.get(), self.images.get())
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

        if link.up:
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
                text=f"{link.stage} — open Web on the Psion to connect")
            self.link_btn.configure(text="Disconnect")
        else:
            self.link_icon.configure(image=self.icon("disconnected"))
            self.link_state.configure(text="Not connected", foreground=MUTED)
            self.link_detail.configure(text="")
            self.link_btn.configure(text="Connect")

        px = probe.proxy_state()
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
        bits.append("forwarding on" if probe.forwarding_enabled() else "forwarding OFF")
        uplink = probe.uplink_interface()
        if uplink:
            bits.append(f"uplink {uplink}")
        if link.up:
            bits.append("Psion: set proxy to "
                        f"{link.local_ip} port 8080")
        self.status.configure(text="   ·   ".join(bits))

        import time
        if time.time() - self._holders_checked > 10:
            self.refresh_ports(with_holders=True)
        self._update_port_note()
        self.after(POLL_SLOW, self._tick_slow)

    # --- shutdown ----------------------------------------------------------

    def _on_close(self) -> None:
        port = self.selected_port()
        self.prefs.update({
            "device": port.device if port else "",
            "fidelity": self.fidelity.get(),
            "images": bool(self.images.get()),
            "geometry": self.master.winfo_geometry(),
        })
        settings.save(self.prefs)
        if self.proxy.running:
            self.proxy.stop()
        self.master.destroy()


def main() -> None:
    root = tk.Tk()
    root.title("PsionNet")
    root.minsize(640, 600)

    # On macOS the application menu has to be built BEFORE it is attached, or
    # Tk silently discards it.
    menubar = tk.Menu(root)
    app_menu = tk.Menu(menubar, name="apple")
    menubar.add_cascade(menu=app_menu)
    root.config(menu=menubar)
    try:
        ttk.Style().theme_use("aqua")     # native on macOS; falls back elsewhere
    except tk.TclError:
        pass
    ui = App(root)
    # Cmd-Q bypasses WM_DELETE_WINDOW, so route it to the same teardown or the
    # proxy child outlives the window.
    root.createcommand("tk::mac::Quit", ui._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()

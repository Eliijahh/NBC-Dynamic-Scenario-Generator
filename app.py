"""Run with Python 3.10+; uses only the Python standard library."""
import copy
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from nbc.core import Campaign, Fleet, DEFAULT_GAME, DEFAULT_STARTING_CREDITS, ROOT, discover, put, ship_damage_fraction
from nbc.economy import EconomyService, PriceError, format_credits
from nbc.fitting import Catalog, CatalogError
from nbc.generation import GenerationService
from nbc.library import CampaignLibrary
from nbc.missions import MissionService
from nbc.refit import RefitService, installed

BG, PANEL, TEXT, MUTED, ACCENT = "#101820", "#182530", "#e4edf4", "#9aafbf", "#53d4b1"


def mission_expiry_text(value):
    if not value:
        return "—"
    text = str(value)
    if len(text) >= 16 and text[10] == "T":
        return f"{text[:10]} {text[11:16]} UTC"
    return text


class Tooltip:
    """Small dependency-free hover label for compact controls."""

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self.after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _show(self):
        self.after_id = None
        if self.tip is not None or not self.widget.winfo_exists():
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_attributes("-topmost", True)
        tk.Label(
            self.tip,
            text=self.text,
            background=PANEL,
            foreground=TEXT,
            padx=9,
            pady=5,
            relief="solid",
            borderwidth=1,
        ).pack()
        self.tip.update_idletasks()
        width = self.tip.winfo_reqwidth()
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - width) // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 7
        x = min(max(4, x), self.widget.winfo_screenwidth() - width - 4)
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, event=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


class App(tk.Tk):
    def __init__(self, game=DEFAULT_GAME, data=None):
        super().__init__()
        self.title("NEBULOUS · Campaign Command / Proof of Concept")
        self._center_window(1520, 960)
        self.configure(bg=BG)
        self.library = CampaignLibrary(Path(data) if data else ROOT / "data")
        self.campaign = self._startup_campaign(game)
        # Only one UI may mutate a given save; independent campaigns remain usable.
        try:
            self.save_lock = self._acquire_lock(self.campaign.data)
        except ValueError:
            self.withdraw()
            messagebox.showerror("Campaign already open", "This save is already open in another Campaign Command window.", parent=self)
            self.destroy()
            raise SystemExit(1)
        self.refits = RefitService(self.campaign)
        self.generation = GenerationService(self.campaign)
        self.missions = MissionService(self.campaign)
        self.mission_job = None
        self.mission_job_reroll = False
        self.mission_generation_failed = False
        self.economy = EconomyService(self.campaign)
        self.templates = discover(game)
        self.selected = None
        self.loading = False
        self.debug_unlocked = False
        self.settings_visible = False
        self._style()
        self._build()
        self.bind_all("<Shift-X>", self._reveal_debug)
        self.refresh()
        self.show_menu()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(1500, self.poll)

    def _center_window(self, preferred_width, preferred_height):
        """Open large enough for the Fleet controls, centered on the current screen."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(preferred_width, max(900, screen_width - 80), screen_width)
        height = min(preferred_height, max(650, screen_height - 100), screen_height)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(1200, width), min(800, height))

    def _startup_campaign(self, game):
        """Open the most recently played campaign, falling back to the legacy root save."""
        root = self.library.path_for_id("root")
        paths = []
        last = self.library.last_info()
        if last and last.valid:
            paths.append(last.path)
        if root.resolve() not in {path.resolve() for path in paths}:
            paths.append(root)
        for path in paths:
            try:
                campaign = Campaign(game, path)
            except Exception:
                continue
            if campaign.has_campaign and campaign.player is not None:
                return campaign
        try:
            return Campaign(game, root)
        except Exception:
            # Keep the main menu usable so the player can load another save or
            # create a fresh campaign even when the legacy root save is corrupt.
            return Campaign(game, self.library.root / ".menu")

    @staticmethod
    def _acquire_lock(directory):
        import msvcrt
        lock_path = Path(directory) / "app.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = lock_path.open("a+b")
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            raise ValueError("This campaign is already open in another Campaign Command window.")
        return lock

    def _switch_campaign(self, campaign, campaign_id, new_lock):
        """Replace the active campaign and move the single-save lock to it."""
        old_lock = self.save_lock
        self.campaign = campaign
        self.save_lock = new_lock
        self.refits = RefitService(self.campaign)
        self.generation = GenerationService(self.campaign)
        self.missions = MissionService(self.campaign)
        self.mission_job = None
        self.mission_job_reroll = False
        self.mission_generation_failed = False
        self.economy = EconomyService(self.campaign)
        if old_lock is not None:
            old_lock.close()
        self.library.set_last(campaign_id)
        campaign.mark_played()
        self.selected = 0
        self.refresh()
        self.show_main()
        self.resume_pending_campaign()
        self._ensure_missions()

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10), borderwidth=0)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("Title.TLabel", font=("Segoe UI Semibold", 25))
        s.configure("Sub.TLabel", font=("Segoe UI Semibold", 13))
        s.configure("TButton", background="#263c4b", foreground=TEXT, padding=(12, 9))
        s.map("TButton", background=[("active", "#355669")], foreground=[("disabled", "#667985")])
        s.configure("Accent.TButton", background=ACCENT, foreground="#092b25", font=("Segoe UI Semibold", 10))
        s.map("Accent.TButton", background=[("active", "#83e7cc")])
        s.configure("Gear.TButton", background=PANEL, foreground=ACCENT, font=("Segoe UI Symbol", 13), padding=(9, 3))
        s.map("Gear.TButton", background=[("active", "#2a4354")], foreground=[("active", "#8cf0d6")])
        s.configure("Danger.TButton", background="#43262d", foreground="#ffd6dc")
        s.map("Danger.TButton", background=[("active", "#5c333d")], foreground=[("disabled", "#7b666b")])
        s.configure("TEntry", fieldbackground=PANEL, foreground=TEXT, padding=7, insertcolor=TEXT)
        s.configure("TCombobox", fieldbackground=PANEL, foreground=TEXT, padding=6, arrowsize=15)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=32)
        s.configure("Treeview.Heading", background="#233746", foreground=MUTED, padding=7)
        s.map("Treeview", background=[("selected", "#285b60")])
        s.configure("TNotebook", background=BG, tabmargins=(0, 12, 0, 10))
        s.configure("TNotebook.Tab", background=PANEL, padding=(22, 10))
        s.map("TNotebook.Tab", background=[("selected", "#31515b")])

    def _build(self):
        self.menu = ttk.Frame(self, padding=24)
        self._build_menu()
        self.main = ttk.Frame(self, padding=24)
        shell = self.main
        header = ttk.Frame(shell)
        header.pack(fill="x")
        settings_bar = ttk.Frame(header)
        settings_bar.pack(side="right", anchor="n")
        self.settings_button = ttk.Button(
            settings_bar,
            text="⚙",
            width=3,
            style="Gear.TButton",
            command=self.show_settings,
            takefocus=True,
        )
        self.settings_button.pack(side="right")
        self.settings_tooltip = Tooltip(self.settings_button, "Settings")
        heading = ttk.Frame(header)
        heading.pack(side="left", fill="x", expand=True)
        ttk.Label(heading, text="CAMPAIGN COMMAND", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(heading, text="Your fleet. Every engagement.", style="Title.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(heading, text="Proof of concept  /  Native fleet editing → live skirmish → battle results", style="Muted.TLabel").pack(anchor="w")
        self.campaign_info = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.campaign_info, foreground=MUTED, wraplength=1160).pack(anchor="w", pady=(7, 0))
        footer = ttk.Frame(shell)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(footer, textvariable=self.status, foreground=ACCENT, wraplength=1160).pack(anchor="w")
        ttk.Label(footer, text=str(self.campaign.game), style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        self.fleet_tab = ttk.Frame(self.tabs, padding=(0, 12))
        self.missions_tab = ttk.Frame(self.tabs, padding=(0, 12))
        self.result_tab = ttk.Frame(self.tabs, padding=(0, 12))
        for frame, title in [(self.fleet_tab, "01  Fleet"), (self.missions_tab, "03  Missions"), (self.result_tab, "04  Debrief")]:
            self.tabs.add(frame, text=title)
        bar = ttk.Frame(self.fleet_tab)
        bar.pack(fill="x")
        self.template_choice = ttk.Combobox(bar, state="readonly", width=42, values=[f"{f.name} · {len(f.ships)} ships · {f.points} pts" for f in self.templates])
        self.template_choice.pack(side="left", padx=(0, 8))
        if self.templates:
            self.template_choice.current(0)
        ttk.Button(bar, text="Load fleet", command=self.load_template).pack(side="left", padx=4)
        ttk.Button(bar, text="Import .fleet", command=self.import_fleet).pack(side="left", padx=4)
        ttk.Button(bar, text="Export .fleet", command=self.export).pack(side="right")
        self.fleet_info = ttk.Label(self.fleet_tab, text="", style="Sub.TLabel")
        self.fleet_info.pack(anchor="w", pady=14)
        self.mission_summary = tk.StringVar(value="")
        ttk.Label(self.fleet_tab, textvariable=self.mission_summary, foreground=ACCENT, wraplength=1100).pack(anchor="w", pady=(0, 10))
        body = ttk.Frame(self.fleet_tab)
        body.pack(fill="both", expand=True)
        left, right = ttk.Frame(body), ttk.Frame(body, padding=(20, 0, 0, 0))
        left.pack(side="left", fill="both", expand=True)
        right.pack(side="right", fill="both", expand=True)
        self.ships = self.tree(left, ("name", "hull", "number", "status", "points"), ("SHIP", "HULL", "NUMBER", "STATUS", "POINTS"), (180, 190, 85, 105, 65))
        self.ships.bind("<<TreeviewSelect>>", self.select_ship)
        self._enable_sorting(self.ships, self.refresh, ("number", "points"))
        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=10)
        ttk.Button(buttons, text="Buy hull", command=self.add_hull, style="Accent.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Sell hull", command=self.sell_hull).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Create 3-ship test fleet", command=self.create_test_fleet).pack(side="left", padx=(0, 8))
        ttk.Label(right, text="SHIP DETAILS", foreground=ACCENT).pack(anchor="w")
        row = ttk.Frame(right)
        row.pack(fill="x", pady=8)
        self.name = tk.StringVar()
        self.number = tk.StringVar()
        ttk.Entry(row, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Entry(row, textvariable=self.number, width=7).pack(side="left")
        ttk.Button(right, text="Save ship details", command=self.save_ship).pack(anchor="w", pady=8)
        self.equipment = self.tree(right, ("component", "contents"), ("INSTALLED EQUIPMENT", "STATUS / LOAD"), (250, 170), height=4)
        self.equipment.bind("<<TreeviewSelect>>", self.refresh_repair)
        ttk.Label(right, text="REPAIR", foreground=ACCENT).pack(anchor="w", pady=(14, 4))
        self.repair_info = ttk.Label(right, text="Select a hull to inspect repair costs.", style="Muted.TLabel", wraplength=440)
        self.repair_info.pack(anchor="w")
        repair_buttons = ttk.Frame(right)
        repair_buttons.pack(anchor="w", pady=8)
        self.repair_selected_button = ttk.Button(repair_buttons, text="Repair selected", command=self.repair_selected)
        self.repair_selected_button.pack(side="left", padx=(0, 8))
        self.repair_all_button = ttk.Button(repair_buttons, text="Repair all", command=self.repair_all)
        self.repair_all_button.pack(side="left", padx=(0, 8))
        self.scrap_component_button = ttk.Button(repair_buttons, text="Scrap component", command=self.scrap_component)
        self.scrap_component_button.pack(side="left")
        self.build_inventory_tab()
        self.build_settings_screen()

        self.build_missions_tab()

        ttk.Label(self.result_tab, text="Battle history", style="Sub.TLabel").pack(anchor="w", pady=(0, 12))
        self.history = self.tree(self.result_tab, ("time", "outcome", "duration", "id"), ("ENGAGEMENT", "OUTCOME", "SECONDS", "BATTLE ID"), (230, 140, 100, 330), height=5)
        self.history.bind("<<TreeviewSelect>>", self.select_result)
        self.results = self.tree(self.result_tab, ("side", "name", "status", "condition", "crew", "damage"), ("SIDE", "SHIP", "STATUS", "CONDITION", "CREW", "DAMAGE DEALT"), (90, 230, 140, 100, 100, 130), height=7)
        ttk.Label(self.result_tab, text="Losses", style="Sub.TLabel").pack(anchor="w", pady=(16, 6))
        self.losses = tk.StringVar(value="No ships lost in this engagement.")
        self.losses_label = ttk.Label(self.result_tab, textvariable=self.losses, foreground=MUTED, wraplength=1050, justify="left")
        self.losses_label.pack(anchor="w")
        ttk.Label(self.result_tab, text="Raw battle reports and input fleets are archived under data/battles/<battle ID>/.", style="Muted.TLabel").pack(anchor="w", pady=10)
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_menu(self):
        self.menu.columnconfigure(0, weight=1)
        self.menu.rowconfigure(0, weight=1)
        center = ttk.Frame(self.menu)
        center.grid(row=0, column=0)
        ttk.Label(center, text="CAMPAIGN COMMAND", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="center")
        ttk.Label(center, text="Your fleet. Every engagement.", style="Title.TLabel").pack(anchor="center", pady=(4, 3))
        ttk.Label(center, text="NEBULOUS campaign proof of concept", style="Muted.TLabel").pack(anchor="center")
        self.menu_campaign_info = tk.StringVar(value="")
        ttk.Label(center, textvariable=self.menu_campaign_info, foreground=MUTED, wraplength=520).pack(anchor="center", pady=(18, 0))
        buttons = ttk.Frame(center)
        buttons.pack(fill="x", pady=(24, 0))
        buttons.columnconfigure(0, weight=1)
        self.continue_button = ttk.Button(buttons, text="Continue", command=self.continue_campaign, style="Accent.TButton", width=28)
        self.continue_button.grid(row=0, column=0, sticky="ew", pady=5)
        self.new_campaign_menu_button = ttk.Button(buttons, text="New Campaign", command=self.new_campaign, width=28)
        self.new_campaign_menu_button.grid(row=1, column=0, sticky="ew", pady=5)
        self.load_campaign_button = ttk.Button(buttons, text="Load Campaign", command=self.load_campaign, width=28)
        self.load_campaign_button.grid(row=2, column=0, sticky="ew", pady=5)
        self.exit_button = ttk.Button(buttons, text="Exit", command=self.close, width=28)
        self.exit_button.grid(row=3, column=0, sticky="ew", pady=5)
        ttk.Label(center, text=str(self.campaign.game), style="Muted.TLabel", wraplength=520).pack(anchor="center", pady=(24, 0))

    def _refresh_menu(self):
        meta = self.campaign.metadata
        can_continue = self.campaign.has_campaign and self.campaign.player is not None
        if can_continue:
            self.continue_button.grid()
            self.menu_campaign_info.set(
                f"Continue: {meta.get('name', 'Campaign')}  /  "
                f"{meta.get('commander') or 'No commander'}  /  "
                f"{format_credits(int(meta.get('credits', 0)))}"
            )
        else:
            self.continue_button.grid_remove()
            campaigns = self.library.list()
            valid = sum(1 for campaign in campaigns if campaign.valid)
            if valid:
                self.menu_campaign_info.set(
                    f"{valid} saved campaign{'s' if valid != 1 else ''} available."
                )
            elif campaigns:
                self.menu_campaign_info.set(
                    "Saved campaign files were found, but none are readable. Use Load Campaign for details."
                )
            else:
                self.menu_campaign_info.set("No saved campaign detected.")

    def show_menu(self):
        self.settings_visible = False
        self.main.pack_forget()
        self.menu.pack(fill="both", expand=True)
        self._refresh_menu()

    def show_main(self):
        self.settings_visible = False
        self.menu.pack_forget()
        self.settings_screen.pack_forget()
        self.tabs.pack(fill="both", expand=True)
        self.main.pack(fill="both", expand=True)

    def continue_campaign(self):
        if not self.campaign.has_campaign or self.campaign.player is None:
            self._refresh_menu()
            return
        campaign_id = self.library.id_for_path(self.campaign.data)
        if campaign_id:
            self.library.set_last(campaign_id)
        self.campaign.mark_played()
        self.show_main()
        self.refresh()
        pending = self.resume_pending_campaign()
        if not pending:
            self.status.set("Campaign loaded. Ready to continue.")
        self.mission_generation_failed = False
        self._ensure_missions()

    def load_campaign(self):
        LoadCampaignDialog(self)

    def open_campaign(self, info):
        if not info.valid:
            raise ValueError(info.error or "This campaign save is unreadable.")
        if self.campaign.data.resolve() == info.path.resolve():
            self.library.set_last(info.id)
            self.campaign.mark_played()
            self.show_main()
            self.refresh()
            pending = self.resume_pending_campaign()
            if not pending:
                self.status.set(f"Campaign '{self.campaign.metadata.get('name', 'Campaign')}' loaded.")
            return
        if self.campaign.pending_action():
            raise ValueError("Finish or cancel the pending battle or refit before loading another campaign.")
        new_lock = self._acquire_lock(info.path)
        try:
            campaign = Campaign(self.campaign.game, info.path)
            if not campaign.has_campaign or campaign.player is None:
                raise ValueError("This save does not contain a valid campaign fleet.")
        except Exception:
            new_lock.close()
            raise
        self._switch_campaign(campaign, info.id, new_lock)
        if not campaign.pending_action() and not campaign.accepted_mission:
            self.status.set(f"Campaign '{campaign.metadata.get('name', 'Campaign')}' loaded.")

    def resume_pending_campaign(self):
        pending = self.campaign.pending_action()
        if pending == "refit":
            # The refit workspace was removed ahead of the mission redesign, so a
            # refit left over from an older build is discarded rather than blocking.
            self.refits.cancel()
            self.refresh()
            self.tabs.select(self.fleet_tab)
            self.status.set("Discarded a pending refit left by the previous build.")
        elif pending == "battle":
            self.tabs.select(self.missions_tab)
            self.status.set("Resumed pending battle. Waiting for NEBULOUS.")
        elif self.campaign.accepted_mission:
            self.tabs.select(self.missions_tab)
            self.status.set("Accepted mission restored. The rest of the campaign remains available.")
            return "mission"
        else:
            self.tabs.select(self.fleet_tab)
        return pending

    def build_settings_screen(self):
        self.settings_screen = ttk.Frame(self.main, padding=(0, 12))
        ttk.Label(
            self.settings_screen,
            text="SETTINGS",
            foreground=ACCENT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        ttk.Label(self.settings_screen, text="Campaign controls", style="Sub.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(
            self.settings_screen,
            text="Save, resume, or leave the current campaign.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        panel = ttk.Frame(self.settings_screen)
        panel.pack(fill="x", pady=(18, 0))
        self.settings_campaign = tk.StringVar(value="")
        ttk.Label(
            panel,
            textvariable=self.settings_campaign,
            foreground=ACCENT,
            wraplength=1100,
        ).pack(anchor="w", pady=(0, 16))
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        ttk.Button(actions, text="Save now", command=self.save_campaign_now).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Resume", command=self.resume_campaign, style="Accent.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Save and exit", command=self.save_and_exit).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Exit without saving", command=self.exit_without_saving, style="Danger.TButton").pack(side="left")
        self.settings_message = tk.StringVar(value="Campaign changes are also saved automatically as you play.")
        ttk.Label(
            panel,
            textvariable=self.settings_message,
            foreground=MUTED,
            wraplength=1100,
        ).pack(anchor="w", pady=(16, 0))
        ttk.Label(
            panel,
            text="Exit without saving skips only the final save; changes already auto-saved remain.",
            style="Muted.TLabel",
            wraplength=1100,
        ).pack(anchor="w", pady=(8, 0))

        # Built hidden. SHIFT-X reveals this only while the settings screen is open.
        self.debug_section = ttk.Frame(panel)
        ttk.Separator(self.debug_section, orient="horizontal").pack(fill="x", pady=(0, 14))
        ttk.Label(
            self.debug_section,
            text="DEBUG",
            foreground=ACCENT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        ttk.Label(
            self.debug_section,
            text="Developer tools unlocked with SHIFT-X.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 8))
        ttk.Button(
            self.debug_section,
            text="Open price debug (Hulls / Components)",
            command=self.open_price_debug,
        ).pack(anchor="w")
        self._refresh_settings()

    def _reveal_debug(self, event=None):
        if not self.settings_visible:
            return
        if self.debug_unlocked:
            return
        self.debug_unlocked = True
        self.debug_section.pack(fill="x", pady=(18, 0))
        self.settings_message.set("Debug tools revealed. Compare native points with credits in the price debug screen.")

    def open_price_debug(self):
        self.guarded(lambda: PriceDebugDialog(self))

    def _refresh_settings(self):
        meta = self.campaign.metadata
        if not meta:
            self.settings_campaign.set("No campaign is loaded.")
            return
        faction = meta.get("faction", "").replace("Stock/", "")
        ships = len(self.campaign.player.ships) if self.campaign.player else 0
        self.settings_campaign.set(
            f"{meta.get('name', 'Campaign')}  /  {meta.get('commander') or 'No commander'}  /  "
            f"{faction}  /  {format_credits(int(meta.get('credits', 0)))}  /  {ships} ships"
        )

    def show_settings(self):
        self.settings_visible = True
        self.tabs.pack_forget()
        self.settings_screen.pack(fill="both", expand=True)
        self._refresh_settings()
        self.status.set("Campaign settings.")

    def resume_campaign(self):
        self.settings_visible = False
        self.settings_screen.pack_forget()
        self.tabs.pack(fill="both", expand=True)
        self.status.set("Resumed campaign.")

    def _save_campaign(self, backup=False):
        if not self.campaign.has_campaign:
            return None
        backup_path = self.campaign.backup_campaign("manual-save") if backup else None
        self.campaign.save_fleets()
        return backup_path

    def save_campaign_now(self):
        try:
            backup_path = self._save_campaign(backup=True)
        except Exception as exc:
            self.settings_message.set(f"Save failed: {exc}")
            self.status.set(f"Save failed: {exc}")
            messagebox.showerror("Save campaign", str(exc), parent=self)
            return
        self.settings_message.set("Campaign saved. Previous save backed up." if backup_path else "Campaign saved.")
        self.status.set("Campaign saved.")

    def save_and_exit(self):
        self._shutdown(save=True, backup=True)

    def exit_without_saving(self):
        self._shutdown(save=False)

    def build_inventory_tab(self):
        self.inventory_tab = ttk.Frame(self.tabs, padding=(0, 12))
        self.tabs.insert(1, self.inventory_tab, text="02  Inventory")
        ttk.Label(self.inventory_tab, text="Equipment store & spare inventory", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(self.inventory_tab, text="Unlimited store stock • Purchases debit credits; selling spare equipment returns 10%. Ammunition is free for the PoC.", style="Muted.TLabel", wraplength=1100).pack(anchor="w", pady=8)
        controls = ttk.Frame(self.inventory_tab)
        controls.pack(fill="x", pady=8)
        self.catalog_search = tk.StringVar()
        search = ttk.Entry(controls, textvariable=self.catalog_search, width=35)
        search.pack(side="left", padx=(0, 10))
        self.catalog_search.trace_add("write", lambda *args: self.refresh_inventory())
        ttk.Label(controls, text="Type").pack(side="left", padx=(0, 6))
        self.catalog_type = tk.StringVar(value="All types")
        self.type_filter = ttk.Combobox(controls, textvariable=self.catalog_type, state="readonly", width=20, values=("All types",))
        self.type_filter.pack(side="left", padx=(0, 10))
        self.type_filter.bind("<<ComboboxSelected>>", lambda event: self.refresh_inventory())
        ttk.Button(controls, text="Connect / open NEBULOUS", command=lambda: self.guarded(self.refits.open_game)).pack(side="left")
        self.inventory_note = tk.StringVar()
        ttk.Label(self.inventory_tab, textvariable=self.inventory_note, foreground=ACCENT, wraplength=1100).pack(anchor="w", pady=8)
        self.affordability = tk.StringVar(value="Select a component to see its price and affordability.")
        ttk.Label(self.inventory_tab, textvariable=self.affordability, foreground=TEXT, wraplength=1100).pack(anchor="w", pady=(0, 4))
        trade = ttk.Frame(self.inventory_tab)
        trade.pack(side="bottom", fill="x", pady=10)
        ttk.Label(trade, text="Quantity").pack(side="left")
        self.trade_quantity = tk.StringVar(value="1")
        ttk.Entry(trade, textvariable=self.trade_quantity, width=7).pack(side="left", padx=10)
        self.trade_quantity.trace_add("write", lambda *args: self._update_affordability())
        self.buy_button = ttk.Button(trade, text="Buy → spares", command=lambda: self.trade_inventory(True), style="Accent.TButton")
        self.buy_button.pack(side="left", padx=5)
        self.sell_button = ttk.Button(trade, text="Sell spare items", command=lambda: self.trade_inventory(False))
        self.sell_button.pack(side="left", padx=5)
        self.stock = self.tree(self.inventory_tab, ("name", "type", "points", "spares", "installed", "price", "sale"), ("COMPONENT / UNLIMITED STOCK", "TYPE", "POINTS", "SPARES OWNED", "INSTALLED", "BUY", "SELL 10%"), (390, 140, 70, 100, 90, 105, 105))
        self.stock.bind("<<TreeviewSelect>>", lambda event: self._update_affordability())
        self._enable_sorting(self.stock, self.refresh_inventory, ("points", "spares", "installed", "price", "sale"))
        self.catalog_rows = []
        self.catalog_signature = None

    def refresh_inventory(self):
        if not hasattr(self, "stock"):
            return
        try:
            catalog = self.refits.catalog()
            owned = self.campaign.state["inventory"]
            fitted = installed(self.campaign.player) if self.campaign.player else {}
            locked = bool(self.campaign.pending_action())
            self.buy_button.configure(state="disabled" if locked or not catalog else "normal")
            self.sell_button.configure(state="disabled" if locked else "normal")
            balance = format_credits(self.campaign.credits)
            prefix = "Inventory locked until the pending refit or battle finishes. " if locked else ""
            self.inventory_note.set(
                prefix + f"{balance} available. {sum(owned.values())} spare components owned. "
                + (f"{len(catalog)} stock components exported by NEBULOUS." if catalog else "Start NEBULOUS with bridge 0.5.0 to export the component catalogue.")
            )
            query = self.catalog_search.get().casefold()
            entries = {k: v for k, v in catalog.items()}
            for key in owned.keys() | fitted.keys():
                entries.setdefault(key, {"Name": key.replace("Stock/", ""), "Type": "Existing equipment"})
            types = sorted(
                {str(value.get("Type") or "").strip() or "Unclassified" for value in entries.values()}
            )
            options = ["All types"] + types
            if tuple(options) != tuple(self.type_filter["values"]):
                self.type_filter.configure(values=options)
            if self.catalog_type.get() not in options:
                self.catalog_type.set("All types")
            type_filter = self.catalog_type.get()
            rows = []
            for key, value in sorted(entries.items(), key=lambda x: x[1]["Name"]):
                if query not in (value["Name"] + key).casefold():
                    continue
                component_type = str(value.get("Type") or "").strip() or "Unclassified"
                if type_filter != "All types" and component_type != type_filter:
                    continue
                price = self.economy.component_price(key)
                points = self.economy.component_points(key)
                rows.append(
                    (
                        key,
                        (
                            value["Name"],
                            component_type,
                            points if points else "—",
                            owned.get(key, 0),
                            fitted.get(key, 0),
                            f"{price:,}",
                            f"{self.economy.sale_value(price):,}",
                        ),
                    )
                )
            if self.stock._sort_column:
                index = tuple(self.stock["columns"]).index(self.stock._sort_column)
                rows = self._sort_rows(
                    rows,
                    index,
                    self.stock._sort_direction < 0,
                    self.stock._sort_column in self.stock._sort_numeric,
                )
            if rows != self.catalog_rows:
                selection = self.stock.selection()
                key = self.catalog_rows[int(selection[0])][0] if selection and int(selection[0]) < len(self.catalog_rows) else None
                self.catalog_rows = rows
                self.stock.delete(*self.stock.get_children())
                for i, (rowkey, values) in enumerate(rows):
                    self.stock.insert("", "end", iid=str(i), values=values)
                    if key == rowkey:
                        self.stock.selection_set(str(i))
            self._update_affordability()
        except Exception as exc:
            self.inventory_note.set(str(exc))

    def _update_affordability(self):
        if not hasattr(self, "stock"):
            return
        selection = self.stock.selection()
        index = int(selection[0]) if selection and selection[0].isdigit() else -1
        if not 0 <= index < len(self.catalog_rows):
            self.affordability.set("Select a component to see its price and affordability.")
            return
        key, values = self.catalog_rows[index]
        name = values[0]
        try:
            quantity = int(self.trade_quantity.get())
        except ValueError:
            self.affordability.set("Enter a quantity from 1 to 999.")
            return
        if not 1 <= quantity <= 999:
            self.affordability.set("Enter a quantity from 1 to 999.")
            return
        total = self.economy.component_price(key) * quantity
        balance = self.campaign.credits
        if total <= balance:
            self.affordability.set(
                f"Buy {quantity} × {name}: {format_credits(total)}. "
                f"Balance {format_credits(balance)} — affordable."
            )
        else:
            self.affordability.set(
                f"Buy {quantity} × {name}: {format_credits(total)}. "
                f"Balance {format_credits(balance)} — short by {format_credits(total - balance)}."
            )

    def trade_inventory(self, buy):
        def action():
            selection = self.stock.selection()
            if not selection:
                raise ValueError("Select a component in the equipment store.")
            key, values = self.catalog_rows[int(selection[0])]
            quantity = int(self.trade_quantity.get())
            if not 1 <= quantity <= 999:
                raise ValueError("Choose a quantity from 1 to 999.")
            if buy:
                total = self.economy.component_price(key) * quantity
                balance = self.campaign.credits
                if total > balance:
                    raise ValueError(
                        f"Not enough credits. This purchase costs {format_credits(total)}; "
                        f"your balance is {format_credits(balance)}."
                    )
                if not messagebox.askyesno(
                    "Buy components",
                    f"Buy {quantity} × {values[0]} for {format_credits(total)}?\n\n"
                    f"Balance after purchase: {format_credits(balance - total)}.",
                    parent=self,
                ):
                    return
            amount = self.refits.trade(key, quantity, buy)
            self.refresh()
            self.status.set(
                f"{'Bought' if buy else 'Sold'} {quantity} × {key.replace('Stock/', '')} "
                f"for {format_credits(amount)}. Balance {format_credits(self.campaign.credits)}."
            )
        self.guarded(action)

    def new_campaign(self):
        NewCampaignDialog(self)

    def create_new_campaign(self, name, commander, faction, credits, starter):
        self.status.set("Creating campaign…")
        self.update_idletasks()
        campaign_id, path = self.library.new_campaign_path()
        new_lock = None
        try:
            new_lock = self._acquire_lock(path)
            campaign = Campaign(self.campaign.game, path)
            meta = campaign.start_new_campaign(name, commander, faction, credits, starter, campaign_id=campaign_id)
        except Exception:
            if new_lock is not None:
                new_lock.close()
            self.library.remove_campaign_path(path)
            raise
        self._switch_campaign(campaign, campaign_id, new_lock)
        self.status.set(
            f"Campaign '{meta['name']}' created with {meta['starting_ships']} ships, "
            f"{meta['starting_points']:,} points and {format_credits(meta['credits'])}."
        )

    def create_test_fleet(self):
        def action():
            self.editable()
            if not messagebox.askyesno("Create test fleet", "Replace the working roster with three test frigates? Your previous fleet will be backed up in data/roster-backups.", parent=self):
                return
            candidates = [(f, i) for f in self.templates if f.source and f.source.parent.name == "Starter Fleets - Alliance" for i, ship in enumerate(f.ships) if ship.findtext("HullType") == "Stock/Raines Frigate"]
            if not candidates:
                raise ValueError("No current starter Raines design found. Import a current three-ship fleet instead.")
            from nbc.core import now
            if self.campaign.player:
                self.campaign.player.save(self.campaign.data / "roster-backups" / (now().replace(":", "-") + ".fleet"))
            donor, index = candidates[0]
            fleet = donor.clone()
            for ship in list(fleet.root.find("Ships")):
                fleet.root.find("Ships").remove(ship)
            fleet.root.find("Ships").append(copy.deepcopy(donor.ships[index]))
            fleet.duplicate(0)
            fleet.duplicate(0)
            put(fleet.root, "Name", "Campaign Refit Test Fleet")
            self.campaign.player = fleet
            self.campaign.save_fleets()
            self.selected = 0
            self.refresh()
        self.guarded(action)

    def tree(self, parent, columns, headings, widths, height=9):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
        tree._heading_labels = dict(zip(columns, headings))
        tree._sort_column = None
        tree._sort_direction = -1
        for c, h, w in zip(columns, headings, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, minwidth=50)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        return tree

    def _enable_sorting(self, tree, refresh, numeric=()):
        tree._sort_numeric = set(numeric)
        for column in tree["columns"]:
            tree.heading(column, command=lambda c=column: self._toggle_sort(tree, c, refresh))

    def _toggle_sort(self, tree, column, refresh):
        if tree._sort_column != column:
            tree._sort_column = column
            tree._sort_direction = -1
        elif tree._sort_direction == -1:
            tree._sort_direction = 1
        else:
            tree._sort_column = None
            tree._sort_direction = -1
        for name, label in tree._heading_labels.items():
            suffix = ""
            if name == tree._sort_column:
                suffix = " ▼" if tree._sort_direction < 0 else " ▲"
            tree.heading(name, text=label + suffix)
        refresh()

    @staticmethod
    def _sort_value(value):
        text = "" if value is None else str(value).strip()
        cleaned = text.replace(",", "").replace("credits", "").replace("%", "").strip()
        try:
            return True, float(cleaned)
        except ValueError:
            return False, text.casefold()

    def _sort_rows(self, rows, column_index, descending, numeric=False):
        if not numeric:
            return sorted(
                rows,
                key=lambda row: str(row[1][column_index] or "").casefold(),
                reverse=descending,
            )
        values, missing = [], []
        for row in rows:
            is_numeric, key = self._sort_value(row[1][column_index])
            if is_numeric:
                values.append((key, row))
            else:
                missing.append(row)
        values.sort(key=lambda item: item[0], reverse=descending)
        return [row for _, row in values] + missing

    def guarded(self, action):
        try:
            return action()
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror("Campaign Command", str(exc), parent=self)

    def editable(self):
        self.refits.ensure_idle()

    def refresh(self):
        meta = self.campaign.metadata
        if meta:
            self.campaign_info.set(f"{meta.get('name', 'Campaign')}  /  {meta.get('commander') or 'No commander'}  /  {meta.get('faction', '').replace('Stock/', '')}  /  {format_credits(int(meta.get('credits', 0)))}")
        else:
            self.campaign_info.set("No campaign created. Use New Campaign to begin.")
        self.ships.delete(*self.ships.get_children())
        f = self.campaign.player
        if f and f.ships:
            states = self.campaign.state.get("ship_state", {})
            ready = 0
            rows = []
            for i, s in enumerate(f.ships):
                key = s.findtext("Key", "")
                item = states.get(key, {})
                status = item.get("status", "ready")
                if status != "lost" and item.get("components"):
                    status = "damaged"
                if status == "ready":
                    ready += 1
                if status == "damaged":
                    label = f"{ship_damage_fraction(s, item):.0%} Damaged"
                elif status == "lost":
                    label = "Lost"
                else:
                    label = status.title()
                rows.append(
                    (
                        i,
                        (
                            s.findtext("Name"),
                            s.findtext("HullType", "").replace("Stock/", ""),
                            s.findtext("Number", ""),
                            label,
                            s.findtext("Cost"),
                        ),
                    )
                )
            if self.ships._sort_column:
                index = tuple(self.ships["columns"]).index(self.ships._sort_column)
                rows = self._sort_rows(
                    rows,
                    index,
                    self.ships._sort_direction < 0,
                    self.ships._sort_column in self.ships._sort_numeric,
                )
            for i, values in rows:
                self.ships.insert("", "end", iid=str(i), values=values)
            suffix = "  /  deployment selection needed above ten" if len(f.ships) > 10 else ""
            self.fleet_info.configure(text=f"{f.name}  /  {f.faction.replace('Stock/', '')}  /  {len(f.ships)} owned hulls  /  {ready} ready  /  ~{f.points:,} points{suffix}")
            self.ships.selection_set(str(min(self.selected or 0, len(f.ships) - 1)))
            self.select_ship()
        else:
            self.selected = None
            self.name.set("")
            self.number.set("")
            self.equipment.delete(*self.equipment.get_children())
            self.fleet_info.configure(text="No hulls owned. Use Buy hull to purchase a hull from the native catalogue.")
        self.history.delete(*self.history.get_children())
        for b in reversed(self.campaign.state["battles"]):
            self.history.insert("", "end", iid=b["id"], values=(b["created"][:19].replace("T", " ") + " UTC", b.get("outcome", b["status"]), b.get("duration", "—"), b["id"]))
        self._refresh_menu()
        self.refresh_inventory()
        self.refresh_repair()
        self._refresh_mission_summary()

    def refresh_repair(self, event=None):
        if not hasattr(self, "repair_info"):
            return
        busy = bool(self.campaign.pending_action())
        if not self.campaign.player or self.selected is None or not 0 <= self.selected < len(self.campaign.player.ships):
            self.repair_info.configure(text="Select a hull to inspect repair costs.")
            self.repair_selected_button.configure(state="disabled")
            self.repair_all_button.configure(text="Repair all", state="disabled")
            self.scrap_component_button.configure(state="disabled")
            return
        can_scrap = bool(self.equipment.selection()) and not busy
        key = self.campaign.player.ships[self.selected].findtext("Key")
        try:
            quote = self.economy.repair_quote(key)
        except Exception as exc:
            self.repair_info.configure(text=str(exc))
            self.repair_selected_button.configure(state="disabled")
        else:
            missing = len(quote["destroyed_components"])
            if quote["needs_repair"]:
                text = f"{len(quote['repairable'])} damaged component(s) · repair {format_credits(quote['cost'])}"
                if missing:
                    text += f" · {missing} destroyed component(s) require scrapping or refit"
                if self.campaign.credits < quote["cost"]:
                    text += " · insufficient credits"
                self.repair_info.configure(text=text)
                self.repair_selected_button.configure(
                    state="disabled" if busy or self.campaign.credits < quote["cost"] else "normal"
                )
            elif missing:
                self.repair_info.configure(
                    text=f"Damaged · {missing} destroyed component(s) require scrapping or refit"
                )
                self.repair_selected_button.configure(state="disabled")
            else:
                self.repair_info.configure(text="Ready · no repair needed.")
                self.repair_selected_button.configure(state="disabled")
        self.scrap_component_button.configure(state="normal" if can_scrap else "disabled")
        try:
            quotes = self.economy.repair_quotes()
        except Exception:
            self.repair_all_button.configure(text="Repair all", state="disabled")
            return
        damaged = [quote for quote in quotes if quote["needs_repair"]]
        if not damaged:
            self.repair_all_button.configure(text="Repair all", state="disabled")
            return
        total = sum(quote["cost"] for quote in damaged)
        self.repair_all_button.configure(
            text=f"Repair all ({len(damaged)} · {format_credits(total)})",
            state="disabled" if busy or self.campaign.credits < total else "normal",
        )

    def build_missions_tab(self):
        header = ttk.Frame(self.missions_tab)
        header.pack(fill="x")
        ttk.Label(header, text="Mission board", style="Sub.TLabel").pack(side="left")
        self.missions_refresh_button = ttk.Button(
            header, text="Regenerate offers", command=self.regenerate_missions
        )
        self.missions_refresh_button.pack(side="right")
        ttk.Label(
            self.missions_tab,
            text=(
                "Offers are fitted offline from the bridge catalogue. Every offer is an "
                "annihilation mission on Abyssal (4p) or The Cliff (4p). Accept one to freeze "
                "its enemy fleet and remove it from the pool; deployment selection comes later."
            ),
            style="Muted.TLabel",
            wraplength=1100,
        ).pack(anchor="w", pady=(8, 8))

        self.missions_progress = ttk.Progressbar(
            self.missions_tab, mode="determinate", maximum=1.0
        )
        self.missions_message = tk.StringVar(value="")
        self.missions_message_label = ttk.Label(
            self.missions_tab,
            textvariable=self.missions_message,
            foreground=ACCENT,
            wraplength=1100,
        )
        self.missions_message_label.pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(self.missions_tab)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, padding=(20, 0, 0, 0))
        right.pack(side="right", fill="both", expand=True)

        self.mission_offers = self.tree(
            left,
            ("name", "faction", "difficulty", "points", "ships", "reward", "expires"),
            ("MISSION", "FACTION", "DIFFICULTY", "POINTS", "SHIPS", "REWARD", "EXPIRES"),
            (200, 85, 75, 60, 50, 90, 130),
            height=9,
        )
        self.mission_offers.bind("<<TreeviewSelect>>", self.select_mission)

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=10)
        self.accept_mission_button = ttk.Button(
            actions, text="Accept mission", command=self.accept_mission, style="Accent.TButton"
        )
        self.accept_mission_button.pack(side="left", padx=(0, 8))
        self.cancel_mission_button = ttk.Button(
            actions, text="Cancel accepted mission", command=self.cancel_mission
        )
        self.cancel_mission_button.pack(side="left", padx=(0, 8))

        ttk.Label(right, text="MISSION DETAILS", foreground=ACCENT).pack(anchor="w")
        self.mission_detail = tk.StringVar(value="Select an offer to inspect its enemy fleet.")
        ttk.Label(
            right,
            textvariable=self.mission_detail,
            style="Muted.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(6, 8))
        self.mission_enemy = self.tree(
            right,
            ("name", "hull", "points"),
            ("ENEMY SHIP", "HULL", "POINTS"),
            (200, 230, 80),
            height=7,
        )

        ttk.Label(right, text="ACCEPTED MISSION", foreground=ACCENT).pack(anchor="w", pady=(16, 4))
        self.active_mission = tk.StringVar(value="No mission accepted.")
        ttk.Label(
            right,
            textvariable=self.active_mission,
            style="Muted.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w")

    def _on_tab_changed(self, event=None):
        if not hasattr(self, "mission_offers"):
            return
        try:
            selected = self.tabs.select()
        except tk.TclError:
            return
        if selected == str(self.missions_tab):
            self._ensure_missions()

    def _ensure_missions(self):
        """Generate a batch on first visit, otherwise render the current board."""
        if not hasattr(self, "mission_offers") or not self.campaign.has_campaign:
            return
        self.missions.migrate()
        if self.mission_job is not None and self.mission_job.running():
            return
        if (
            not self.missions.accepted
            and not self.missions.sanitize()
            and not self.mission_generation_failed
        ):
            self.start_mission_generation()
            return
        self.refresh_missions()

    def regenerate_missions(self):
        if self.campaign.accepted_mission:
            self.status.set("Cancel the accepted mission before regenerating offers.")
            return
        self.mission_generation_failed = False
        self.start_mission_generation(reroll=True)

    def start_mission_generation(self, reroll=False):
        if not self.campaign.has_campaign:
            self.status.set("Create or load a campaign first.")
            return
        if self.mission_job is not None and self.mission_job.running():
            return
        try:
            self.mission_job = self.missions.start_generation()
        except Exception as exc:
            self.missions_message.set(str(exc))
            self.mission_generation_failed = True
            self.refresh_missions()
            return
        self.mission_job_reroll = reroll
        self.missions_message.set("Generating mission offers…")
        self._set_mission_busy(True, 0.0)
        self.after(80, lambda: self._poll_mission_job(self.mission_job))

    def _poll_mission_job(self, job=None):
        if job is not None and job is not self.mission_job:
            return
        job = self.mission_job
        if job is None:
            self._set_mission_busy(False)
            return
        if job.running():
            self._set_mission_busy(True, job.progress)
            self.after(80, lambda: self._poll_mission_job(job))
            return
        if job.failed():
            self.missions_message.set(f"Could not generate missions: {job.error}")
            self.mission_generation_failed = True
            self.mission_job = None
            self._set_mission_busy(False)
            self.refresh_missions()
            self.status.set("Mission generation failed.")
            return
        try:
            offers = self.missions.store_generation(job, rerolled=self.mission_job_reroll)
        except Exception as exc:
            self.missions_message.set(str(exc))
            self.mission_generation_failed = True
            self.mission_job = None
            self._set_mission_busy(False)
            self.refresh_missions()
            return
        self.mission_job = None
        self.missions_message.set(
            f"{len(offers)} mission offers available. Select one to inspect its enemy fleet."
        )
        self._set_mission_busy(False)
        self.refresh()
        self.refresh_missions()
        self.status.set(f"Generated {len(offers)} mission offers.")

    def _set_mission_busy(self, busy, progress=0.0):
        if not hasattr(self, "missions_progress"):
            return
        if busy:
            if not self.missions_progress.winfo_manager():
                self.missions_progress.pack(
                    anchor="w", fill="x", pady=(0, 6), before=self.missions_message_label
                )
            self.missions_progress.configure(value=progress)
        else:
            self.missions_progress.pack_forget()
        accepted = self.missions.accepted
        self.missions_refresh_button.configure(
            state="disabled" if busy or accepted else "normal"
        )
        if busy:
            self.accept_mission_button.configure(state="disabled")
            self.cancel_mission_button.configure(state="disabled")
        else:
            self.accept_mission_button.configure(
                state="disabled" if accepted or not self.missions.sanitize() else "normal"
            )
            self.cancel_mission_button.configure(
                state="normal" if accepted else "disabled"
            )

    def refresh_missions(self):
        if not hasattr(self, "mission_offers"):
            return
        self._render_missions()

    def _render_missions(self):
        offers = self.missions.sanitize()
        accepted = self.missions.accepted
        busy = bool(self.mission_job is not None and self.mission_job.running())
        selected = self.mission_offers.selection()
        selected_id = selected[0] if selected else None

        self.mission_offers.delete(*self.mission_offers.get_children())
        for offer in offers:
            self.mission_offers.insert(
                "",
                "end",
                iid=offer["id"],
                values=(
                    offer.get("name", ""),
                    (offer.get("faction") or "").replace("Stock/", ""),
                    (offer.get("difficulty") or "").title(),
                    f"{offer.get('points', 0):,}",
                    offer.get("ships", 0),
                    format_credits(offer.get("reward", 0)),
                    mission_expiry_text(offer.get("expires")),
                ),
            )
        ids = [offer["id"] for offer in offers]
        if selected_id in ids:
            self.mission_offers.selection_set(selected_id)
        elif ids:
            self.mission_offers.selection_set(ids[0])
        self.select_mission()

        if accepted:
            self.active_mission.set(
                f"{accepted.get('name')}  ·  {(accepted.get('type') or '').title()}  ·  "
                f"{(accepted.get('difficulty') or '').title()}  ·  "
                f"{accepted.get('points', 0):,} points  ·  {accepted.get('ships', 0)} ships\n"
                f"Map: {accepted.get('map') or '—'}  ·  "
                f"reward {format_credits(accepted.get('reward', 0))}"
            )
        else:
            self.active_mission.set("No mission accepted.")
        self._refresh_mission_summary(accepted)

        self.accept_mission_button.configure(
            state="disabled" if busy or accepted or not offers else "normal"
        )
        self.cancel_mission_button.configure(
            state="disabled" if busy or not accepted else "normal"
        )
        self.missions_refresh_button.configure(
            state="disabled" if busy or accepted else "normal"
        )

    def select_mission(self, event=None):
        if not hasattr(self, "mission_enemy"):
            return
        self.mission_enemy.delete(*self.mission_enemy.get_children())
        selection = self.mission_offers.selection()
        offers = {offer["id"]: offer for offer in self.missions.sanitize()}
        if not selection or selection[0] not in offers:
            self.mission_detail.set("Select an offer to inspect its enemy fleet.")
            return
        offer = offers[selection[0]]
        self.mission_detail.set(
            f"{offer.get('name')}  ·  {(offer.get('type') or '').title()}  ·  "
            f"{(offer.get('difficulty') or '').title()}  ·  "
            f"{(offer.get('faction') or '').replace('Stock/', '')}  ·  "
            f"{offer.get('points', 0):,} points  ·  {offer.get('ships', 0)} ships  ·  "
            f"{(offer.get('status') or 'offered').title()}\n"
            f"Map: {offer.get('map') or '—'}  ·  "
            f"Reward {format_credits(offer.get('reward', 0))}  ·  "
            f"offer expires {mission_expiry_text(offer.get('expires'))}."
        )
        for ship in offer.get("preview", []):
            self.mission_enemy.insert(
                "",
                "end",
                values=(
                    ship.get("name", ""),
                    (ship.get("hull") or "").replace("Stock/", ""),
                    f"{ship.get('points', 0):,}",
                ),
            )

    def accept_mission(self):
        def action():
            selection = self.mission_offers.selection()
            if not selection:
                raise ValueError("Select a mission offer to accept.")
            accepted = self.missions.accept(selection[0])
            self.missions_message.set(
                f"Accepted {accepted.get('name')}. Deployment selection is next."
            )
            self.refresh()
            self.refresh_missions()
            self.status.set(
                f"Mission accepted: {accepted.get('name')}  ·  "
                f"reward {format_credits(accepted.get('reward', 0))}."
            )
        self.guarded(action)

    def cancel_mission(self):
        def action():
            returned = self.missions.cancel_accepted()
            self.missions_message.set(
                f"Cancelled {returned.get('name')}. The offer is back on the board."
            )
            self.refresh()
            self.refresh_missions()
            self.status.set("Mission cancelled. No reward was awarded.")
        self.guarded(action)

    def _refresh_mission_summary(self, accepted=None):
        if not hasattr(self, "mission_summary"):
            return
        accepted = self.missions.accepted if accepted is None else accepted
        if accepted:
            self.mission_summary.set(
                f"Active mission: {accepted.get('name')}  ·  "
                f"{(accepted.get('type') or '').title()} on {accepted.get('map') or '—'}  ·  "
                f"{(accepted.get('difficulty') or '').title()}  ·  "
                f"{accepted.get('points', 0):,} points  ·  "
                f"reward {format_credits(accepted.get('reward', 0))}"
            )
        else:
            offers = self.missions.sanitize()
            if offers:
                self.mission_summary.set(
                    f"{len(offers)} mission offers available. Open the Missions tab to accept one."
                )
            else:
                self.mission_summary.set(
                    "No active mission. Open the Missions tab to generate offers."
                )

    def select_ship(self, event=None):
        selection = self.ships.selection()
        if not selection or not self.campaign.player:
            self.refresh_repair()
            return
        self.selected = int(selection[0])
        if not 0 <= self.selected < len(self.campaign.player.ships):
            self.refresh_repair()
            return
        ship = self.campaign.player.ships[self.selected]
        self.name.set(ship.findtext("Name", ""))
        self.number.set(ship.findtext("Number", "0"))
        self.equipment.delete(*self.equipment.get_children())
        state = (self.campaign.state.get("ship_state") or {}).get(ship.findtext("Key"), {})
        records = {
            entry.get("socket"): entry
            for entry in (state.get("components") or [])
            if entry.get("socket")
        }

        def damage_text(record):
            try:
                health = float(record.get("health", 1.0) or 0.0)
            except (TypeError, ValueError):
                health = 0.0
            return f"{max(0.0, min(1.0, 1.0 - health)):.0%} damaged"

        socket_keys = set()
        for i, socket in enumerate(ship.findall("SocketMap/HullSocket")):
            socket_key = (socket.findtext("Key") or "").strip()
            socket_keys.add(socket_key)
            component = socket.findtext("ComponentName", "").replace("Stock/", "")
            record = records.get(socket_key)
            if record and record.get("destroyed"):
                contents = "DESTROYED"
            elif record:
                contents = damage_text(record)
            else:
                loads = socket.findall(".//MagSaveData")
                contents = ", ".join(
                    f"{m.findtext('Quantity', '?')} {m.findtext('MunitionKey', '').replace('Stock/', '')}"
                    for m in loads
                )
            self.equipment.insert("", "end", iid=str(i), values=(component, contents))
        for entry in state.get("components") or []:
            socket_key = entry.get("socket")
            if socket_key in socket_keys:
                continue
            component = (entry.get("component") or "Unknown component").replace("Stock/", "")
            label = "DESTROYED" if entry.get("destroyed") else damage_text(entry)
            self.equipment.insert(
                "", "end", iid=f"missing:{socket_key}", values=(f"{component} (missing)", label)
            )
        self.refresh_repair()

    def load_template(self):
        def action():
            self.editable()
            idx = self.template_choice.current()
            if idx < 0:
                raise ValueError("Choose a fleet first.")
            self.campaign.player = self.templates[idx].clone()
            self.campaign.save_fleets()
            self.selected = 0
            self.refresh()
            self.status.set("Fleet loaded. Original game files are preserved.")
        self.guarded(action)

    def import_fleet(self):
        path = filedialog.askopenfilename(filetypes=[("NEBULOUS fleet", "*.fleet")], initialdir=self.campaign.game / "Saves" / "Fleets")
        if not path:
            return
        def action():
            self.editable()
            f = Fleet.load(path)
            f.validate()
            self.campaign.player = f
            self.templates.append(f.clone())
            self.template_choice["values"] = [f"{x.name} · {len(x.ships)} ships · {x.points} pts" for x in self.templates]
            self.selected = 0
            self.campaign.save_fleets()
            self.refresh()
        self.guarded(action)

    def save_ship(self):
        def action():
            self.editable()
            if self.selected is None or not self.campaign.player:
                raise ValueError("Select a hull first.")
            ship = self.campaign.player.ships[self.selected]
            self.campaign.rename_owned_hull(ship.findtext("Key"), self.name.get(), self.number.get())
            self.refresh()
            self.status.set("Hull details saved.")
        self.guarded(action)

    def add_hull(self):
        def action():
            self.editable()
            path = self.campaign.exchange / "fitting-catalog.xml"
            if not path.exists():
                raise ValueError("The native fitting catalogue is missing. Start NEBULOUS once with the bridge installed and enabled, then try again.")
            try:
                catalog = Catalog.load(path)
            except CatalogError as exc:
                raise ValueError(str(exc)) from exc
            HullPurchaseDialog(self, catalog)
        self.guarded(action)

    def create_hull(self, faction, hull, name, number):
        key, price = self.economy.buy_hull(faction, hull, name, number)
        self.selected = next((i for i, s in enumerate(self.campaign.player.ships) if s.findtext("Key") == key), 0)
        self.refresh()
        self.status.set(
            f"{name} bought for {format_credits(price)}. Balance {format_credits(self.campaign.credits)}. "
            "Refit the hull to install equipment."
        )

    def sell_hull(self):
        def action():
            self.editable()
            if self.selected is None or not self.campaign.player:
                raise ValueError("Select a hull to sell.")
            ship = self.campaign.player.ships[self.selected]
            name = ship.findtext("Name", "this hull")
            key = ship.findtext("Key")
            if not self.economy.hull_record(key):
                raise ValueError("That hull is not in the campaign roster.")
            value = self.economy.hull_value(key)
            refund = self.economy.sale_value(value)
            sale_percent = self.economy.price_book().sale_percent
            if not messagebox.askyesno(
                "Sell hull",
                f"Sell {name}? Its installed components will be returned to spare inventory. "
                f"You receive {format_credits(refund)} ({sale_percent}% of its {format_credits(value)} value).",
                parent=self,
            ):
                return
            returned, refund = self.economy.sell_hull(key)
            self.selected = min(self.selected, max(0, len(self.campaign.player.ships) - 1))
            self.refresh()
            self.status.set(
                f"Sold {name} for {format_credits(refund)}. Returned {sum(returned.values())} installed "
                f"component(s) to inventory. Balance {format_credits(self.campaign.credits)}."
            )
        self.guarded(action)

    def repair_selected(self):
        def action():
            if self.selected is None or not self.campaign.player:
                raise ValueError("Select a hull to repair.")
            ship = self.campaign.player.ships[self.selected]
            key = ship.findtext("Key")
            quote = self.economy.repair_quote(key)
            if not quote["damaged"]:
                raise ValueError(f"{quote['name'] or 'That hull'} is already ready.")
            if not quote["needs_repair"]:
                raise ValueError(
                    f"{quote['name'] or 'That hull'} has no repairable component damage. "
                    "Scrap or refit its destroyed components first."
                )
            if quote["cost"] and not messagebox.askyesno(
                "Repair ship",
                f"Repair {quote['name'] or 'this hull'} for {format_credits(quote['cost'])}? "
                f"Balance {format_credits(self.campaign.credits)}.",
                parent=self,
            ):
                return
            repaired = self.economy.repair_ship(key)
            self.refresh()
            self.status.set(
                f"Repaired {repaired['name'] or 'hull'} for {format_credits(repaired['cost'])}. "
                f"Balance {format_credits(self.campaign.credits)}."
            )
        self.guarded(action)

    def repair_all(self):
        def action():
            if not self.campaign.player:
                raise ValueError("There is no campaign fleet to repair.")
            quotes = [quote for quote in self.economy.repair_quotes() if quote["needs_repair"]]
            if not quotes:
                raise ValueError("No component damage needs repair.")
            total = sum(quote["cost"] for quote in quotes)
            if total and not messagebox.askyesno(
                "Repair all ships",
                f"Repair {len(quotes)} ships for {format_credits(total)}? "
                f"Balance {format_credits(self.campaign.credits)}.",
                parent=self,
            ):
                return
            repaired, spent = self.economy.repair_all()
            self.refresh()
            self.status.set(
                f"Repaired {len(repaired)} ships for {format_credits(spent)}. "
                f"Balance {format_credits(self.campaign.credits)}."
            )
        self.guarded(action)

    def scrap_component(self):
        def action():
            if self.selected is None or not self.campaign.player:
                raise ValueError("Select a hull first.")
            selection = self.equipment.selection()
            if not selection:
                raise ValueError("Select a component to scrap.")
            ship = self.campaign.player.ships[self.selected]
            selected_id = selection[0]
            if selected_id.startswith("missing:"):
                socket_key = selected_id.split(":", 1)[1]
                records = (
                    (self.campaign.state.get("ship_state") or {})
                    .get(ship.findtext("Key"), {})
                    .get("components")
                    or []
                )
                record = next((entry for entry in records if entry.get("socket") == socket_key), None)
                if record is None:
                    raise ValueError("That destroyed component record is no longer present.")
                component = (record.get("component") or "").strip()
            else:
                sockets = ship.findall("SocketMap/HullSocket")
                try:
                    socket_index = int(selected_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Select a component to scrap.") from exc
                if not 0 <= socket_index < len(sockets):
                    raise ValueError("That equipment socket is no longer installed.")
                socket = sockets[socket_index]
                component = (socket.findtext("ComponentName") or "").strip()
                socket_key = (socket.findtext("Key") or "").strip()
            if not component:
                raise ValueError("That component is no longer present.")
            if not messagebox.askyesno(
                "Scrap component",
                f"Permanently scrap {component.replace('Stock/', '')} from "
                f"{ship.findtext('Name', 'this hull')}? No credits or spare parts are returned.",
                parent=self,
            ):
                return
            scrapped = self.campaign.scrap_installed_component(ship.findtext("Key"), socket_key)
            self.refresh()
            self.status.set(f"Scrapped {scrapped.replace('Stock/', '')}. No credits or spare parts returned.")
        self.guarded(action)

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".fleet", initialfile="NBC Player.fleet", filetypes=[("NEBULOUS fleet", "*.fleet")])
        if path:
            def action():
                if not self.campaign.player or not 1 <= len(self.campaign.player.ships) <= 10:
                    raise ValueError("Export requires 1–10 ships. Mission deployment selection is not implemented yet.")
                unready = self.campaign.unready_ships()
                if unready:
                    names = ", ".join(ship.findtext("Name", "Unnamed hull") for ship in unready)
                    raise ValueError(f"Repair damaged ships before exporting: {names}.")
                self.campaign.player.save(path)
            self.guarded(action)
            self.status.set(f"Exported {path}")

    def poll(self):
        try:
            self.campaign.pump_game()
            if self.campaign.runtime.purpose:
                self.status.set(self.campaign.runtime.message)
            if self.campaign.state.get("active_battle"):
                result = self.campaign.collect()
                if result:
                    self.refresh()
                    self.tabs.select(self.result_tab)
                    self.history.selection_set(result["id"])
                    self.select_result()
                    self.status.set(f"Battle imported: {result['outcome']}. Your debrief is ready.")
                    self.deiconify()
                    self.lift()
                    self.attributes("-topmost", True)
                    self.after(700, lambda: self.attributes("-topmost", False))
                    self.focus_force()
                    self.bell()
                else:
                    self.status.set(self.campaign.bridge_status())
        except Exception as exc:
            self.status.set(f"Result import: {exc}")
        self.refresh_inventory()
        self.after(1500, self.poll)

    def select_result(self, event=None):
        selection = self.history.selection()
        if not selection:
            return
        battle = next(b for b in self.campaign.state["battles"] if b["id"] == selection[0])
        self.results.delete(*self.results.get_children())
        for s in battle.get("ships", []):
            condition = s.get("Condition", "")
            display = f"{float(condition):.0%}" if condition else "—"
            self.results.insert("", "end", values=(s.get("Side"), s.get("Name"), s.get("Status"), display, f"{s.get('FinalCrew', '?')}/{s.get('OriginalCrew', '?')}", s.get("DamageDealt")))
        self.show_losses(battle)

    def show_losses(self, battle):
        """List ships destroyed in the selected battle and the components lost with them."""
        losses = battle.get("lost_ships")
        if losses is None:
            # Battles imported before loss tracking was added.
            losses = [
                {"name": s.get("Name"), "hull": "", "reason": "destroyed", "components": []}
                for s in battle.get("ships", [])
                if s.get("Side") == "Player"
                and (s.get("Status") or "").strip().casefold() in {"destroyed", "true", "1", "eliminated"}
            ]
        if not losses:
            self.losses.set("No ships lost in this engagement.")
            self.losses_label.configure(foreground=MUTED)
            return
        lines = []
        for ship in losses:
            reason = "reactor detonation" if ship.get("reason") == "reactor" else "hull destroyed"
            components = [
                entry.get("component", "")
                for entry in (ship.get("components") or [])
                if entry.get("component")
            ]
            detail = ", ".join(components) if components else "no installed components"
            lines.append(
                f"{ship.get('name') or 'Unnamed hull'} "
                f"({ship.get('hull') or 'unknown hull'}) — {reason}. Lost: {detail}"
            )
        self.losses.set("\n".join(lines))
        self.losses_label.configure(foreground="#ef9b9b")

    def _release_lock(self):
        lock = getattr(self, "save_lock", None)
        if lock is not None:
            try:
                lock.close()
            except OSError:
                pass
            self.save_lock = None

    def _shutdown(self, save=True, backup=False):
        if save:
            try:
                self._save_campaign(backup=backup)
            except Exception as exc:
                self.status.set(f"Save failed: {exc}")
                messagebox.showerror(
                    "Exit Campaign Command",
                    f"Could not save before exiting:\n\n{exc}",
                    parent=self,
                )
                return False
        elif not messagebox.askyesno(
            "Exit without saving",
            "Exit Campaign Command without a final save?\n\nChanges already auto-saved will remain.",
            parent=self,
        ):
            return False
        self._release_lock()
        self.destroy()
        return True

    def close(self):
        self._shutdown(save=True)


class LoadCampaignDialog(tk.Toplevel):
    """Modal campaign picker with metadata and save-health reporting."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.busy = False
        self.campaigns = []
        self.by_id = {}
        self.title("Load Campaign")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._build()
        self._reload()
        self.update_idletasks()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(max(1040, self.winfo_reqwidth() + 24), max(820, screen_width - 120))
        height = min(max(560, self.winfo_reqheight() + 24), max(500, screen_height - 120))
        x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(880, width), min(480, height))
        self.grab_set()
        self.lift()
        self.tree.focus_set()

    def _build(self):
        shell = ttk.Frame(self, padding=24)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="LOAD CAMPAIGN", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(shell, text="Choose a saved command", style="Title.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(
            shell,
            text="Each campaign has its own fleet, inventory, credits, battle history and pending operations.",
            style="Muted.TLabel",
            wraplength=960,
        ).pack(anchor="w")

        actions = ttk.Frame(shell)
        actions.pack(side="bottom", fill="x", pady=(16, 0))
        self.load_button = ttk.Button(actions, text="Load Selected", command=self.load, style="Accent.TButton")
        self.load_button.pack(side="left")
        self.refresh_button = ttk.Button(actions, text="Refresh", command=self._reload)
        self.refresh_button.pack(side="left", padx=10)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel)
        self.cancel_button.pack(side="left")

        self.message = tk.StringVar(value="Select a campaign to load.")
        ttk.Label(shell, textvariable=self.message, foreground=ACCENT, wraplength=960).pack(side="bottom", anchor="w", pady=(10, 0))

        frame = ttk.Frame(shell)
        frame.pack(fill="both", expand=True, pady=(18, 0))
        columns = ("name", "commander", "faction", "credits", "ships", "played", "status")
        headings = ("CAMPAIGN", "COMMANDER", "FACTION", "CREDITS", "SHIPS", "LAST PLAYED", "STATUS")
        widths = (240, 165, 115, 105, 70, 165, 105)
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=60)
        self.tree.tag_configure("invalid", foreground="#ef9b9b")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)

    @staticmethod
    def _display_time(value):
        value = (value or "").strip().replace("T", " ")
        return value[:19] if value else "Unknown"

    def _reload(self):
        if self.busy:
            return
        self.campaigns = self.app.library.list()
        self.by_id = {campaign.id: campaign for campaign in self.campaigns}
        self.tree.delete(*self.tree.get_children())
        for campaign in self.campaigns:
            faction = campaign.faction.replace("Stock/", "") if campaign.faction else "—"
            self.tree.insert(
                "",
                "end",
                iid=campaign.id,
                values=(
                    campaign.name,
                    campaign.commander or "—",
                    faction,
                    f"{campaign.credits:,}",
                    campaign.ships,
                    self._display_time(campaign.last_played),
                    "Ready" if campaign.valid else "Unreadable",
                ),
                tags=() if campaign.valid else ("invalid",),
            )
        if not self.campaigns:
            self.load_button.configure(state="disabled")
            self.message.set("No saved campaigns found. Create one from the main menu.")
            return
        current_id = self.app.library.id_for_path(self.app.campaign.data)
        target = current_id if current_id in self.by_id else next(
            (campaign.id for campaign in self.campaigns if campaign.valid), self.campaigns[0].id
        )
        self.tree.selection_set(target)
        self.tree.focus(target)
        self.tree.see(target)
        self._select()

    def _select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            if not self.busy:
                self.load_button.configure(state="disabled")
            self.message.set("Select a campaign to load.")
            return
        campaign = self.by_id.get(selection[0])
        if campaign is None:
            self.load_button.configure(state="disabled")
            return
        if not campaign.valid:
            self.load_button.configure(state="disabled")
            self.message.set(f"{campaign.name}: {campaign.error}")
            return
        self.load_button.configure(state="disabled" if self.busy else "normal")
        self.message.set(
            f"{campaign.name}  /  {campaign.commander or 'No commander'}  /  "
            f"{format_credits(campaign.credits)}  /  {campaign.ships} ships  /  "
            f"Last played {self._display_time(campaign.last_played)}"
        )

    def load(self):
        if self.busy:
            return
        selection = self.tree.selection()
        campaign = self.by_id.get(selection[0]) if selection else None
        if campaign is None:
            self.message.set("Select a campaign to load.")
            return
        if not campaign.valid:
            self.message.set(f"{campaign.name}: {campaign.error}")
            return
        self.busy = True
        self.load_button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.message.set("Loading campaign…")
        self.update_idletasks()
        try:
            self.app.open_campaign(campaign)
        except Exception as exc:
            self.busy = False
            self.refresh_button.configure(state="normal")
            self.cancel_button.configure(state="normal")
            self.message.set(f"Could not load campaign: {exc}")
            messagebox.showerror("Load campaign", str(exc), parent=self)
            self._select()
            return
        self.grab_release()
        self.destroy()

    def cancel(self):
        if self.busy:
            return
        self.grab_release()
        self.destroy()


class NewCampaignDialog(tk.Toplevel):
    """Modal setup screen for a new independent campaign save."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.busy = False
        self.starting_fleets = []
        self.faction_values = []
        self.title("New Campaign")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._build()
        self._refresh_factions()
        self.update_idletasks()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(max(880, self.winfo_reqwidth() + 24), max(760, screen_width - 120))
        height = min(max(700, self.winfo_reqheight() + 24), max(600, screen_height - 120))
        x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(800, width), min(620, height))
        self.grab_set()
        self.lift()
        self.name_entry.focus_set()

    def _build(self):
        shell = ttk.Frame(self, padding=24)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="NEW CAMPAIGN", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(shell, text="Choose your command", style="Title.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(
            shell,
            text="The selected starter fleet and its loadouts are copied into the new campaign.",
            style="Muted.TLabel",
            wraplength=800,
        ).pack(anchor="w")

        form = ttk.Frame(shell)
        form.pack(fill="x", pady=(18, 8))
        form.columnconfigure(1, weight=1)
        self.name = tk.StringVar(value="New Campaign")
        self.commander = tk.StringVar(value="Commander")
        self.faction = ttk.Combobox(form, state="readonly", width=28)
        self.faction.bind("<<ComboboxSelected>>", self._refresh_starters)
        self.starting_choice = ttk.Combobox(form, state="readonly")
        self.starting_choice.bind("<<ComboboxSelected>>", self._preview)

        self.name_entry = ttk.Entry(form, textvariable=self.name)
        rows = [
            ("Campaign name", self.name_entry),
            ("Commander", ttk.Entry(form, textvariable=self.commander)),
            ("Faction", self.faction),
            ("Starter fleet", self.starting_choice),
        ]
        for index, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=(0, 14), pady=6)
            widget.grid(row=index, column=1, sticky="ew", pady=6)

        self.fleet_summary = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.fleet_summary, foreground=ACCENT, wraplength=800).pack(anchor="w", pady=(4, 0))

        # Reserve the action row from the bottom first so the dialog buttons can
        # never be pushed out of view by the expanding fleet preview.
        actions = ttk.Frame(shell)
        actions.pack(side="bottom", fill="x", pady=(16, 0))
        self.create_button = ttk.Button(actions, text="Create Campaign", command=self.create, style="Accent.TButton")
        self.create_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel)
        self.cancel_button.pack(side="left", padx=10)
        self.message = tk.StringVar(value="Select a starter fleet.")
        ttk.Label(shell, textvariable=self.message, style="Muted.TLabel", wraplength=800).pack(side="bottom", anchor="w", pady=(10, 0))

        ttk.Label(
            shell,
            text="Preview: hulls and points in the starting roster. The native fleet editor can change loadouts later.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(10, 5))
        preview_frame = ttk.Frame(shell)
        preview_frame.pack(fill="both", expand=True)
        self.preview = ttk.Treeview(preview_frame, columns=("name", "hull", "points"), show="headings", height=6)
        for column, heading, width in (("name", "SHIP", 250), ("hull", "HULL", 360), ("points", "POINTS", 90)):
            self.preview.heading(column, text=heading)
            self.preview.column(column, width=width, minwidth=60)
        scrollbar = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        scrollbar.pack(side="right", fill="y")
        self.preview.configure(yscrollcommand=scrollbar.set)
        self.preview.pack(side="left", fill="both", expand=True)

    def _selected_faction(self):
        index = self.faction.current()
        return self.faction_values[index] if 0 <= index < len(self.faction_values) else None

    def _refresh_factions(self):
        values = sorted({f.faction for f in self.app.templates if f.faction in ("Stock/Alliance", "Stock/Protectorate")})
        if not values:
            values = ["Stock/Alliance", "Stock/Protectorate"]
        self.faction_values = values
        self.faction["values"] = [value.replace("Stock/", "") for value in values]
        default = values.index("Stock/Alliance") if "Stock/Alliance" in values else 0
        self.faction.current(default)
        self._refresh_starters()

    def _refresh_starters(self, event=None):
        faction = self._selected_faction()
        folder = {
            "Stock/Alliance": "Starter Fleets - Alliance",
            "Stock/Protectorate": "Starter Fleets - Protectorate",
        }.get(faction)
        available = [fleet for fleet in self.app.templates if fleet.faction == faction]
        preferred = [fleet for fleet in available if fleet.source and fleet.source.parent.name == folder]
        self.starting_fleets = preferred or available
        self.starting_choice["values"] = [
            f"{fleet.name}  ·  {len(fleet.ships)} ships  ·  {fleet.points:,} pts" for fleet in self.starting_fleets
        ]
        if self.starting_fleets:
            self.starting_choice.current(0)
        else:
            self.starting_choice.set("")
        self._preview()

    def _preview(self, event=None):
        self.preview.delete(*self.preview.get_children())
        index = self.starting_choice.current()
        if not 0 <= index < len(self.starting_fleets):
            self.fleet_summary.set("No starter fleet is available for this faction.")
            self.create_button.configure(state="disabled")
            return
        fleet = self.starting_fleets[index]
        for ship in fleet.ships:
            self.preview.insert("", "end", values=(
                ship.findtext("Name", ""),
                ship.findtext("HullType", "").replace("Stock/", ""),
                ship.findtext("Cost", "0"),
            ))
        self.fleet_summary.set(
            f"{fleet.name}: {len(fleet.ships)} ships / {fleet.points:,} points. "
            "Loadouts are copied from this starter fleet."
        )
        self.create_button.configure(state="disabled" if self.busy else "normal")

    def create(self):
        if self.busy:
            return
        try:
            name = self.name.get().strip()
            commander = self.commander.get().strip()
            index = self.starting_choice.current()
            if not 0 <= index < len(self.starting_fleets):
                raise ValueError("Choose a starter fleet.")
            starter = self.starting_fleets[index]
            faction = self._selected_faction()
            if faction != starter.faction:
                raise ValueError("The starter fleet must match the selected faction.")
        except ValueError as exc:
            self.message.set(str(exc))
            messagebox.showerror("New campaign", str(exc), parent=self)
            return
        self.busy = True
        self.create_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.message.set("Creating campaign…")
        self.update_idletasks()
        try:
            self.app.create_new_campaign(name, commander, faction, DEFAULT_STARTING_CREDITS, starter)
        except Exception as exc:
            self.busy = False
            self.cancel_button.configure(state="normal")
            self._preview()
            self.message.set(str(exc))
            messagebox.showerror("New campaign", str(exc), parent=self)
            return
        self.grab_release()
        self.destroy()

    def cancel(self):
        if self.busy:
            return
        self.grab_release()
        self.destroy()


class HullPurchaseDialog(tk.Toplevel):
    """Choose a native hull and add an empty ship to the campaign roster."""

    def __init__(self, app, catalog):
        super().__init__(app)
        self.app = app
        self.catalog = catalog
        self.busy = False
        self.hull_rows = []
        self.selected_hull = None
        self.faction = app.campaign.metadata.get("faction")
        self.title("Buy hull")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._build()
        self._refresh_hulls()
        self.update_idletasks()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(max(900, self.winfo_reqwidth() + 24), max(760, screen_width - 120))
        height = min(max(600, self.winfo_reqheight() + 24), max(480, screen_height - 120))
        x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(800, width), min(480, height))
        self.grab_set()
        self.lift()
        self.name_entry.focus_set()

    def _build(self):
        shell = ttk.Frame(self, padding=24)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="BUY HULL", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(shell, text="Choose a hull for your campaign", style="Title.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(
            shell,
            text=(
                f"Showing {self.faction.replace('Stock/', '')} hulls only, the faction selected when this campaign began. "
                f"Hulls are added empty and their price is deducted from your {format_credits(self.app.campaign.credits)} balance."
            ),
            style="Muted.TLabel",
            wraplength=800,
        ).pack(anchor="w")

        form = ttk.Frame(shell)
        form.pack(fill="x", pady=(18, 8))
        form.columnconfigure(1, weight=1)
        self.name = tk.StringVar()
        self.number = tk.StringVar()
        self.name_entry = ttk.Entry(form, textvariable=self.name)
        number_entry = ttk.Entry(form, textvariable=self.number, width=12)
        for index, (label, widget) in enumerate(
            (("Hull name", self.name_entry), ("Hull number", number_entry))
        ):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=(0, 14), pady=6)
            widget.grid(row=index, column=1, sticky="ew", pady=6)

        filters = ttk.Frame(shell)
        filters.pack(fill="x", pady=(0, 6))
        ttk.Label(filters, text="Hull class").pack(side="left")
        self.hull_class = tk.StringVar(value="All classes")
        self.class_filter = ttk.Combobox(filters, textvariable=self.hull_class, state="readonly", width=24, values=("All classes",))
        self.class_filter.pack(side="left", padx=(10, 0))
        self.class_filter.bind("<<ComboboxSelected>>", lambda event: self._refresh_hulls())

        self.hulls = self.app.tree(shell, ("name", "class", "points", "credits", "owned", "sockets"), ("HULL", "CLASS", "POINTS", "CREDITS", "OWNED", "SOCKETS"), (300, 150, 70, 115, 65, 70), height=8)
        self.hulls.bind("<<TreeviewSelect>>", self._select_hull)
        self.price = tk.StringVar(value="Select a hull to see its price.")
        ttk.Label(shell, textvariable=self.price, foreground=TEXT).pack(anchor="w", pady=(8, 0))
        self.message = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.message, foreground=ACCENT, wraplength=800).pack(anchor="w", pady=8)

        actions = ttk.Frame(shell)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        self.add_button = ttk.Button(actions, text="Buy hull", command=self.add, style="Accent.TButton", state="disabled")
        self.add_button.pack(side="left")
        ttk.Button(actions, text="Cancel", command=self.cancel).pack(side="left", padx=10)

    def _existing_names(self):
        if not self.app.campaign.player:
            return set()
        return {s.findtext("Name", "").strip().casefold() for s in self.app.campaign.player.ships}

    def _existing_numbers(self):
        if not self.app.campaign.player:
            return set()
        return {int(s.findtext("Number", "0") or 0) for s in self.app.campaign.player.ships}

    def _unique_name(self, base):
        names = self._existing_names()
        base = base.strip() or "New Hull"
        if base.casefold() not in names:
            return base
        suffix = 2
        while f"{base} {suffix}".casefold() in names:
            suffix += 1
        return f"{base} {suffix}"

    def _unique_number(self):
        numbers = self._existing_numbers()
        for _ in range(2000):
            value = random.randint(100, 9999)
            if value not in numbers:
                return value
        value = 100
        while value in numbers:
            value += 1
        return value

    def _refresh_hulls(self, event=None):
        self.hulls.delete(*self.hulls.get_children())
        self.hull_rows = []
        self.selected_hull = None
        faction = self.catalog.factions.get(self.faction)
        if faction is None:
            self.message.set("The campaign faction is missing from the native fitting catalogue.")
            self.add_button.configure(state="disabled")
            return
        all_hulls = sorted(faction.hulls, key=lambda h: (h.class_name.casefold(), h.name.casefold(), h.points))
        classes = sorted({(hull.class_name or "Unclassified") for hull in all_hulls}, key=str.casefold)
        options = ["All classes"] + classes
        if tuple(options) != tuple(self.class_filter["values"]):
            self.class_filter.configure(values=options)
        if self.hull_class.get() not in options:
            self.hull_class.set("All classes")
        selected_class = self.hull_class.get()
        self.hull_rows = [
            hull
            for hull in all_hulls
            if selected_class == "All classes" or (hull.class_name or "Unclassified") == selected_class
        ]
        try:
            prices = [self.app.economy.hull_price(hull.key, hull.points) for hull in self.hull_rows]
        except PriceError as exc:
            self.message.set(str(exc))
            self.add_button.configure(state="disabled")
            return
        owned = {}
        player = self.app.campaign.player
        if player:
            for ship in player.ships:
                key = ship.findtext("HullType", "")
                owned[key] = owned.get(key, 0) + 1
        for index, (hull, price) in enumerate(zip(self.hull_rows, prices)):
            self.hulls.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    hull.name,
                    hull.class_name,
                    hull.points,
                    f"{price:,}",
                    owned.get(hull.key, 0),
                    len(hull.sockets),
                ),
            )
        if self.hull_rows:
            self.hulls.selection_set("0")
            self._select_hull()
        else:
            self.price.set("Select a hull to see its price.")
            if selected_class == "All classes":
                self.message.set("This faction has no usable hulls in the native catalogue.")
            else:
                self.message.set(f"No {selected_class} hulls are available for this campaign faction.")
            self.add_button.configure(state="disabled")

    def _select_hull(self, event=None, reset_fields=True):
        selection = self.hulls.selection()
        if not selection:
            self.selected_hull = None
            self.add_button.configure(state="disabled")
            return
        index = int(selection[0])
        if not 0 <= index < len(self.hull_rows):
            return
        hull = self.hull_rows[index]
        self.selected_hull = hull
        if reset_fields:
            self.name.set(self._unique_name(hull.name))
            self.number.set(str(self._unique_number()))
        try:
            price = self.app.economy.hull_price(hull.key, hull.points)
        except PriceError as exc:
            self.price.set(str(exc))
            self.message.set(str(exc))
            self.add_button.configure(state="disabled")
            return
        balance = self.app.campaign.credits
        self.price.set(
            f"{hull.name}: {format_credits(price)}  •  {hull.points:,} native points  •  "
            f"sale value {format_credits(self.app.economy.sale_value(price))}"
        )
        if price > balance:
            self.message.set(
                f"Not enough credits. This hull costs {format_credits(price)}; your balance is {format_credits(balance)}."
            )
            self.add_button.configure(state="disabled")
        else:
            self.message.set("")
            self.add_button.configure(state="normal")

    def add(self):
        if self.busy:
            return
        try:
            selection = self.hulls.selection()
            if not selection:
                raise ValueError("Choose a hull.")
            hull = self.hull_rows[int(selection[0])]
            name = self.name.get().strip()
            if not name:
                raise ValueError("Enter a hull name.")
            number = int(self.number.get())
        except (ValueError, IndexError) as exc:
            self.message.set(str(exc))
            messagebox.showerror("Buy hull", str(exc), parent=self)
            return
        try:
            price = self.app.economy.hull_price(hull.key, hull.points)
        except PriceError as exc:
            self.message.set(str(exc))
            messagebox.showerror("Buy hull", str(exc), parent=self)
            return
        balance = self.app.campaign.credits
        if price > balance:
            message = (
                f"Not enough credits. This hull costs {format_credits(price)}; "
                f"your balance is {format_credits(balance)}."
            )
            self.message.set(message)
            messagebox.showerror("Buy hull", message, parent=self)
            return
        if not messagebox.askyesno(
            "Buy hull",
            f"Buy {name} ({hull.name}, hull number {number}) for {format_credits(price)}?\n\n"
            f"Balance after purchase: {format_credits(balance - price)}.",
            parent=self,
        ):
            return
        self.busy = True
        self.add_button.configure(state="disabled")
        try:
            self.app.create_hull(self.faction, hull, name, number)
        except Exception as exc:
            self.busy = False
            self._select_hull(reset_fields=False)
            self.message.set(str(exc))
            messagebox.showerror("Buy hull", str(exc), parent=self)
            return
        self.grab_release()
        self.destroy()

    def cancel(self):
        if self.busy:
            return
        self.grab_release()
        self.destroy()


class PriceDebugDialog(tk.Toplevel):
    """Read-only SHIFT-X view of native points and campaign credits."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Price debug · native points vs credits")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(True, True)
        try:
            self.book = app.economy.price_book(reload=True)
            self.catalog = app.economy.fitting_catalog(reload=True)
            if self.catalog is None:
                raise ValueError("The native fitting catalogue is missing. Start NEBULOUS with the bridge enabled first.")
            if not self.book.validate_catalog(self.catalog):
                raise PriceError("No priced hulls or components were found in the fitting catalogue.")
        except Exception:
            self.destroy()
            raise
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._populate()
        self.update_idletasks()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(max(1050, self.winfo_reqwidth() + 24), max(760, screen_width - 120))
        height = min(max(620, self.winfo_reqheight() + 24), max(480, screen_height - 120))
        x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(840, width), min(480, height))
        self.grab_set()
        self.lift()
        self.focus_set()

    def _build(self):
        shell = ttk.Frame(self, padding=20)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="DEBUG · PRICE TABLE", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(shell, text="Native points and campaign credits", style="Title.TLabel").pack(anchor="w", pady=(4, 3))
        component = self.book.formula["component"]
        hull = self.book.formula["hull"]
        ttk.Label(
            shell,
            text=(
                f"Price table: {self.book.source}\n"
                f"Components: {component['base']:,} + {component['points_squared']:,} × points² "
                f"(min {component['minimum']:,}, rounded to {component['round_to']:,}).  "
                f"Hulls: {hull['base']:,} + {hull['per_point']:,} × points "
                f"(min {hull['minimum']:,}, rounded to {hull['round_to']:,}).  "
                f"Selling returns {self.book.sale_percent}%."
            ),
            style="Muted.TLabel",
            wraplength=1000,
        ).pack(anchor="w", pady=(0, 12))
        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill="both", expand=True)
        self.hulls_tab = ttk.Frame(self.notebook, padding=10)
        self.components_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.hulls_tab, text="Hulls")
        self.notebook.add(self.components_tab, text="Components")
        sale_heading = f"SELL {self.book.sale_percent}%"
        self.hulls = self.app.tree(
            self.hulls_tab,
            ("faction", "name", "class", "points", "credits", "sale", "key"),
            ("FACTION", "HULL", "CLASS", "POINTS", "CREDITS", sale_heading, "KEY"),
            (110, 230, 150, 80, 120, 110, 330),
        )
        self.components = self.app.tree(
            self.components_tab,
            ("faction", "name", "type", "points", "credits", "sale", "key"),
            ("FACTION", "COMPONENT", "TYPE", "POINTS", "CREDITS", sale_heading, "KEY"),
            (110, 230, 150, 80, 120, 110, 330),
        )
        actions = ttk.Frame(shell)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Close", command=self.close, style="Accent.TButton").pack(side="right")

    def _populate(self):
        for faction_key in sorted(self.catalog.factions):
            faction = self.catalog.factions[faction_key]
            faction_name = faction_key.replace("Stock/", "")
            for hull in sorted(faction.hulls, key=lambda item: (item.class_name.casefold(), item.name.casefold())):
                price = self.book.hull_price(hull.key, hull.points)
                self.hulls.insert(
                    "",
                    "end",
                    values=(
                        faction_name,
                        hull.name,
                        hull.class_name,
                        f"{hull.points:,}",
                        f"{price:,}",
                        f"{self.book.sale_value(price):,}",
                        hull.key,
                    ),
                )
            for component in sorted(faction.components.values(), key=lambda item: item.name.casefold()):
                price = self.book.component_price(component.key, component.points)
                self.components.insert(
                    "",
                    "end",
                    values=(
                        faction_name,
                        component.name,
                        component.category,
                        f"{component.points:,}",
                        f"{price:,}",
                        f"{self.book.sale_value(price):,}",
                        component.key,
                    ),
                )

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    App(args.game, args.data).mainloop()

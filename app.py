"""Run with Python 3.10+; uses only the Python standard library."""
import copy
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from nbc.core import Campaign, Fleet, DEFAULT_GAME, DEFAULT_STARTING_CREDITS, ROOT, discover, put
from nbc.fitting import Catalog, CatalogError, empty_ship
from nbc.generation import GenerationService
from nbc.library import CampaignLibrary
from nbc.refit import RefitService, installed

BG, PANEL, TEXT, MUTED, ACCENT = "#101820", "#182530", "#e4edf4", "#9aafbf", "#53d4b1"


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
        self.geometry("1260x820")
        self.minsize(1080, 700)
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
        self.templates = discover(game)
        self.selected = None
        self.loading = False
        self._style()
        self._build()
        self.refresh()
        self.show_menu()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(1500, self.poll)

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
        if old_lock is not None:
            old_lock.close()
        self.library.set_last(campaign_id)
        campaign.mark_played()
        self.selected = 0
        self.refresh()
        self.show_main()
        self.resume_pending_campaign()

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
        self.battle_tab = ttk.Frame(self.tabs, padding=(0, 12))
        self.result_tab = ttk.Frame(self.tabs, padding=(0, 12))
        for frame, title in [(self.fleet_tab, "01  Fleet"), (self.battle_tab, "04  Battle"), (self.result_tab, "05  Debrief")]:
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
        body = ttk.Frame(self.fleet_tab)
        body.pack(fill="both", expand=True)
        left, right = ttk.Frame(body), ttk.Frame(body, padding=(20, 0, 0, 0))
        left.pack(side="left", fill="both", expand=True)
        right.pack(side="right", fill="both", expand=True)
        self.ships = self.tree(left, ("name", "hull", "number", "status", "points"), ("SHIP", "HULL", "NUMBER", "STATUS", "POINTS"), (180, 190, 85, 105, 65))
        self.ships.bind("<<TreeviewSelect>>", self.select_ship)
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
        ttk.Label(right, text="Refit equipment in the native game editor.", style="Muted.TLabel").pack(anchor="w", pady=(8, 4))
        self.loadouts = ttk.Combobox(right, state="readonly", width=45)
        self.refit_selected_button = ttk.Button(right, text="Refit selected hull → NEBULOUS", command=self.start_selected_refit)
        self.refit_selected_button.pack(anchor="w", pady=8)
        ttk.Button(right, text="Refit whole fleet → NEBULOUS", command=self.start_refit).pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text="Buy spare equipment in Inventory before opening a refit. Whole-fleet refit is limited to ten hulls; individual hulls can always be refitted.", style="Muted.TLabel", wraplength=440).pack(anchor="w")
        self.equipment = self.tree(right, ("component", "contents"), ("INSTALLED EQUIPMENT", "LOAD"), (250, 150), height=4)
        self.build_refit_tabs()
        self.build_settings_screen()

        ttk.Label(self.battle_tab, text="Prepare an engagement", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(self.battle_tab, text="Build randomly fitted ships near your target budget. Ships are fitted offline from the native catalogue exported by NEBULOUS.", style="Muted.TLabel").pack(anchor="w", pady=8)
        controls = ttk.Frame(self.battle_tab)
        controls.pack(fill="x", pady=12)
        self.faction = ttk.Combobox(controls, state="readonly", values=["Stock/Protectorate", "Stock/Alliance"], width=23)
        self.faction.current(0)
        self.faction.pack(side="left", padx=(0, 10))
        self.budget = tk.StringVar(value="700")
        ttk.Entry(controls, textvariable=self.budget, width=9).pack(side="left", padx=(0, 8))
        ttk.Label(controls, text="target points", style="Muted.TLabel").pack(side="left", padx=(0, 14))
        self.generate_button = ttk.Button(controls, text="Generate enemy", command=self.generate)
        self.generate_button.pack(side="left")
        self.enemy_info = ttk.Label(self.battle_tab, text="Generate an enemy to continue.", foreground=ACCENT)
        self.enemy_info.pack(anchor="w", pady=8)
        self.enemies = self.tree(self.battle_tab, ("name", "hull", "points"), ("ENEMY SHIP", "HULL", "POINTS"), (260, 400, 100), height=6)
        launchbar = ttk.Frame(self.battle_tab)
        launchbar.pack(fill="x", pady=18)
        self.run_button = ttk.Button(launchbar, text="Run battle  →  NEBULOUS", style="Accent.TButton", command=self.launch)
        self.run_button.pack(side="left")
        ttk.Button(launchbar, text="Cancel pending request", command=self.cancel).pack(side="left", padx=12)
        ttk.Label(self.battle_tab, text="First run: install the bridge, launch NEBULOUS once with “NBC Campaign Bridge” enabled, then return here.\nThe bridge exports the fitting catalogue and creates local skirmishes. You choose deployment and play.\nResults appear here automatically when the battle ends. Damage is recorded for review; it is not restored in the next battle yet.", style="Muted.TLabel", wraplength=1050).pack(anchor="w")

        ttk.Label(self.result_tab, text="Battle history", style="Sub.TLabel").pack(anchor="w", pady=(0, 12))
        self.history = self.tree(self.result_tab, ("time", "outcome", "duration", "id"), ("ENGAGEMENT", "OUTCOME", "SECONDS", "BATTLE ID"), (230, 140, 100, 330), height=5)
        self.history.bind("<<TreeviewSelect>>", self.select_result)
        self.results = self.tree(self.result_tab, ("side", "name", "status", "condition", "crew", "damage"), ("SIDE", "SHIP", "STATUS", "CONDITION", "CREW", "DAMAGE DEALT"), (90, 230, 140, 100, 100, 130), height=7)
        ttk.Label(self.result_tab, text="Raw battle reports and input fleets are archived under data/battles/<battle ID>/.", style="Muted.TLabel").pack(anchor="w", pady=10)

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
                f"{int(meta.get('credits', 0)):,} credits"
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
        self.main.pack_forget()
        self.menu.pack(fill="both", expand=True)
        self._refresh_menu()

    def show_main(self):
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
            raise ValueError("Finish or cancel the pending mission, battle or refit before loading another campaign.")
        new_lock = self._acquire_lock(info.path)
        try:
            campaign = Campaign(self.campaign.game, info.path)
            if not campaign.has_campaign or campaign.player is None:
                raise ValueError("This save does not contain a valid campaign fleet.")
        except Exception:
            new_lock.close()
            raise
        self._switch_campaign(campaign, info.id, new_lock)
        if not campaign.pending_action():
            self.status.set(f"Campaign '{campaign.metadata.get('name', 'Campaign')}' loaded.")

    def resume_pending_campaign(self):
        pending = self.campaign.pending_action()
        if pending == "refit":
            self.tabs.select(self.refit_tab)
            self.status.set("Resumed pending refit. Confirm, continue or cancel it.")
        elif pending == "battle":
            self.tabs.select(self.battle_tab)
            self.status.set("Resumed pending battle. Waiting for NEBULOUS.")
        elif pending == "mission":
            self.tabs.select(self.battle_tab)
            self.status.set("Accepted mission restored from the save.")
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
        self._refresh_settings()

    def _refresh_settings(self):
        meta = self.campaign.metadata
        if not meta:
            self.settings_campaign.set("No campaign is loaded.")
            return
        faction = meta.get("faction", "").replace("Stock/", "")
        ships = len(self.campaign.player.ships) if self.campaign.player else 0
        self.settings_campaign.set(
            f"{meta.get('name', 'Campaign')}  /  {meta.get('commander') or 'No commander'}  /  "
            f"{faction}  /  {int(meta.get('credits', 0)):,} credits  /  {ships} ships"
        )

    def show_settings(self):
        self.tabs.pack_forget()
        self.settings_screen.pack(fill="both", expand=True)
        self._refresh_settings()
        self.status.set("Campaign settings.")

    def resume_campaign(self):
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

    def build_refit_tabs(self):
        self.inventory_tab = ttk.Frame(self.tabs, padding=(0, 12))
        self.refit_tab = ttk.Frame(self.tabs, padding=(0, 12))
        self.tabs.insert(1, self.inventory_tab, text="02  Inventory")
        self.tabs.insert(2, self.refit_tab, text="03  Refit")
        ttk.Label(self.inventory_tab, text="Equipment store & spare inventory", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(self.inventory_tab, text="Unlimited store stock • No money • Buy adds spares; sell removes spares. Installed equipment stays on its ship.", style="Muted.TLabel", wraplength=1100).pack(anchor="w", pady=8)
        controls = ttk.Frame(self.inventory_tab)
        controls.pack(fill="x", pady=8)
        self.catalog_search = tk.StringVar()
        search = ttk.Entry(controls, textvariable=self.catalog_search, width=35)
        search.pack(side="left", padx=(0, 10))
        self.catalog_search.trace_add("write", lambda *args: self.refresh_inventory())
        ttk.Button(controls, text="Connect / open NEBULOUS", command=lambda: self.guarded(self.refits.open_game)).pack(side="left")
        self.inventory_note = tk.StringVar()
        ttk.Label(self.inventory_tab, textvariable=self.inventory_note, foreground=ACCENT, wraplength=1100).pack(anchor="w", pady=8)
        trade = ttk.Frame(self.inventory_tab)
        trade.pack(side="bottom", fill="x", pady=10)
        ttk.Label(trade, text="Quantity").pack(side="left")
        self.trade_quantity = tk.StringVar(value="1")
        ttk.Entry(trade, textvariable=self.trade_quantity, width=7).pack(side="left", padx=10)
        self.buy_button = ttk.Button(trade, text="Buy → spares", command=lambda: self.trade_inventory(True), style="Accent.TButton")
        self.buy_button.pack(side="left", padx=5)
        self.sell_button = ttk.Button(trade, text="Sell spare items", command=lambda: self.trade_inventory(False))
        self.sell_button.pack(side="left", padx=5)
        self.stock = self.tree(self.inventory_tab, ("name", "type", "spares", "installed"), ("COMPONENT / UNLIMITED STOCK", "TYPE", "SPARES OWNED", "INSTALLED"), (510, 180, 120, 120))
        self.catalog_rows = []
        self.catalog_signature = None

        ttk.Label(self.refit_tab, text="Native fleet refit", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(self.refit_tab, text="Edit all ships together in NEBULOUS. Removed components become available to the rest of your fleet immediately.\nNo refit costs or facility requirements. Campaign ships and inventory change only after confirmation here.", style="Muted.TLabel", wraplength=1100).pack(anchor="w", pady=8)
        bar = ttk.Frame(self.refit_tab)
        bar.pack(fill="x", pady=12)
        self.refit_start_button = ttk.Button(bar, text="Refit fleet → NEBULOUS", command=self.start_refit, style="Accent.TButton")
        self.refit_start_button.pack(side="left", padx=(0, 8))
        self.refit_resume_button = ttk.Button(bar, text="Continue / resume editor", command=self.resume_refit)
        self.refit_resume_button.pack(side="left", padx=8)
        self.refit_confirm_button = ttk.Button(bar, text="Confirm refit", command=self.confirm_refit, style="Accent.TButton")
        self.refit_confirm_button.pack(side="left", padx=8)
        self.refit_cancel_button = ttk.Button(bar, text="Cancel refit", command=self.cancel_refit)
        self.refit_cancel_button.pack(side="left", padx=8)
        self.refit_message = tk.StringVar(value="Ready to refit. Buy spare equipment in Inventory first, or transfer installed parts between ships.")
        ttk.Label(self.refit_tab, textvariable=self.refit_message, foreground=ACCENT, wraplength=1100).pack(anchor="w", pady=10)
        ttk.Label(self.refit_tab, text="Proposed changes", style="Sub.TLabel").pack(anchor="w", pady=8)
        self.refit_changes = self.tree(self.refit_tab, ("ship", "socket", "old", "new"), ("SHIP", "SOCKET", "REMOVE", "INSTALL"), (180, 150, 360, 360), height=6)
        self.refit_inventory_text = tk.StringVar()
        ttk.Label(self.refit_tab, textvariable=self.refit_inventory_text, style="Muted.TLabel", wraplength=1100).pack(anchor="w", pady=8)

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
            self.inventory_note.set("Inventory locked until the pending mission, refit or battle finishes." if locked else f"{sum(owned.values())} spare components owned. " + (f"{len(catalog)} stock components exported by NEBULOUS." if catalog else "Start NEBULOUS with bridge 0.5.0 to export the component catalogue."))
            query = self.catalog_search.get().casefold()
            entries = {k: v for k, v in catalog.items()}
            for key in owned.keys() | fitted.keys():
                entries.setdefault(key, {"Name": key.replace("Stock/", ""), "Type": "Existing equipment"})
            rows = [(key, (v["Name"], v.get("Type", ""), owned.get(key, 0), fitted.get(key, 0))) for key, v in sorted(entries.items(), key=lambda x: x[1]["Name"]) if query in (v["Name"] + key).casefold()]
            if rows == self.catalog_rows:
                return
            selection = self.stock.selection()
            key = self.catalog_rows[int(selection[0])][0] if selection and int(selection[0]) < len(self.catalog_rows) else None
            self.catalog_rows = rows
            self.stock.delete(*self.stock.get_children())
            for i, (rowkey, values) in enumerate(rows):
                self.stock.insert("", "end", iid=str(i), values=values)
                if key == rowkey:
                    self.stock.selection_set(str(i))
        except Exception as exc:
            self.inventory_note.set(str(exc))

    def trade_inventory(self, buy):
        def action():
            selection = self.stock.selection()
            if not selection:
                raise ValueError("Select a component in the equipment store.")
            key = self.catalog_rows[int(selection[0])][0]
            quantity = int(self.trade_quantity.get())
            self.refits.trade(key, quantity, buy)
            self.refresh_inventory()
            self.status.set(f"{'Bought' if buy else 'Sold'} {quantity} × {key.replace('Stock/', '')}. No money charged.")
        self.guarded(action)

    def refresh_refit(self):
        active = self.refits.active
        busy = bool(self.campaign.pending_action())
        roster = self.campaign.player
        whole_ready = bool(roster and 1 <= len(roster.ships) <= 10)
        selected_ready = bool(roster and self.selected is not None and 0 <= self.selected < len(roster.ships))
        self.refit_start_button.configure(state="disabled" if busy or not whole_ready else "normal")
        self.refit_selected_button.configure(state="disabled" if busy or not selected_ready else "normal")
        self.refit_resume_button.configure(state="normal" if active else "disabled")
        self.refit_cancel_button.configure(state="normal" if active else "disabled")
        self.refit_confirm_button.configure(state="normal" if active and active["stage"] == "review" else "disabled")
        self.refit_changes.delete(*self.refit_changes.get_children())
        self.refit_inventory_text.set("")
        if active and active["stage"] == "review":
            try:
                _, inventory, changes = self.refits.review()
                for row in changes:
                    self.refit_changes.insert("", "end", values=tuple(x.replace("Stock/", "") for x in row))
                self.refit_message.set(f"Draft ready: {len(changes)} equipment/identity changes. Confirm to apply, continue editing, or cancel.")
                self.refit_inventory_text.set(f"Spare inventory after confirmation: {sum(inventory.values())} components. Ammunition and missile/craft construction are outside inventory tracking in this prototype.")
            except Exception as exc:
                self.refit_message.set(f"Draft rejected: {exc}")
                self.refit_confirm_button.configure(state="disabled")
        elif active:
            self.refit_message.set(self.refits.status())
        else:
            self.refit_message.set("Ready. Your approved fleet and spare inventory are saved. Buy equipment or open a refit.")

    def start_refit(self):
        def action():
            self.refits.start()
            self.refresh()
            self.tabs.select(self.refit_tab)
            self.status.set("Starting NEBULOUS and opening your fleet editor…")
        self.guarded(action)

    def resume_refit(self):
        def action():
            if self.refits.active and self.refits.active["stage"] != "review" and not messagebox.askyesno("Resume editor", "Reopen the last exported draft/input? Unsaved changes in the current game editor will be discarded.", parent=self):
                return
            self.refits.resume()
            self.refresh()
            self.status.set("Refit resumed. An interrupted editor restarts from the last exported draft/input.")
        self.guarded(action)

    def confirm_refit(self):
        def action():
            self.refits.confirm()
            self.refresh()
            self.status.set("Refit confirmed. Fleet and spare inventory saved together.")
        self.guarded(action)

    def cancel_refit(self):
        if self.refits.active and messagebox.askyesno("Cancel refit", "Discard this refit? Your approved fleet and spare inventory will be preserved.", parent=self):
            self.guarded(self.refits.cancel)
            self.refresh()

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
            f"{meta['starting_points']:,} points and {meta['credits']:,} credits."
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
        for c, h, w in zip(columns, headings, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, minwidth=50)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        return tree

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
            self.campaign_info.set(f"{meta.get('name', 'Campaign')}  /  {meta.get('commander') or 'No commander'}  /  {meta.get('faction', '').replace('Stock/', '')}  /  {int(meta.get('credits', 0)):,} credits")
        else:
            self.campaign_info.set("No campaign created. Use New Campaign to begin.")
        self.ships.delete(*self.ships.get_children())
        f = self.campaign.player
        if f and f.ships:
            states = self.campaign.state.get("ship_state", {})
            ready = 0
            for i, s in enumerate(f.ships):
                key = s.findtext("Key", "")
                item = states.get(key, {})
                status = item.get("status", "ready")
                condition = float(item.get("condition", 1.0) or 0)
                if status == "ready" and condition < 1:
                    status = "damaged"
                if status == "ready":
                    ready += 1
                label = "Lost" if status == "lost" else f"Damaged {condition:.0%}" if status == "damaged" else status.title()
                self.ships.insert("", "end", iid=str(i), values=(s.findtext("Name"), s.findtext("HullType", "").replace("Stock/", ""), s.findtext("Number", ""), label, s.findtext("Cost")))
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
        self.enemies.delete(*self.enemies.get_children())
        if self.campaign.enemy:
            e = self.campaign.enemy
            self.enemy_info.configure(text=f"{len(e.ships)} hostile ships  /  {e.points:,} points  /  {e.faction.replace('Stock/', '')}")
            for s in e.ships:
                self.enemies.insert("", "end", values=(s.findtext("Name"), s.findtext("HullType", "").replace("Stock/", ""), s.findtext("Cost")))
        else:
            self.enemy_info.configure(text="Generate an enemy to continue.")
        self.history.delete(*self.history.get_children())
        for b in reversed(self.campaign.state["battles"]):
            self.history.insert("", "end", iid=b["id"], values=(b["created"][:19].replace("T", " ") + " UTC", b.get("outcome", b["status"]), b.get("duration", "—"), b["id"]))
        busy = bool(self.campaign.pending_action())
        self.run_button.configure(state="disabled" if busy or not self.campaign.can_deploy() or not self.campaign.enemy else "normal")
        self.generate_button.configure(state="disabled" if busy or not self.campaign.has_campaign else "normal")
        self._refresh_menu()
        self.refresh_inventory()
        self.refresh_refit()

    def select_ship(self, event=None):
        selection = self.ships.selection()
        if not selection or not self.campaign.player:
            if hasattr(self, "refit_selected_button"):
                self.refit_selected_button.configure(state="disabled")
            return
        self.selected = int(selection[0])
        if not 0 <= self.selected < len(self.campaign.player.ships):
            return
        ship = self.campaign.player.ships[self.selected]
        self.name.set(ship.findtext("Name", ""))
        self.number.set(ship.findtext("Number", "0"))
        self.loadout_options = [(f, i) for f in self.templates if f.faction == self.campaign.player.faction for i, s in enumerate(f.ships) if s.findtext("HullType") == ship.findtext("HullType")]
        self.loadouts["values"] = [f"{f.name} / {f.ships[i].findtext('Name')}" for f, i in self.loadout_options]
        self.loadouts.set("")
        self.equipment.delete(*self.equipment.get_children())
        for i, socket in enumerate(ship.findall("SocketMap/HullSocket")):
            loads = socket.findall(".//MagSaveData")
            contents = ", ".join(f"{m.findtext('Quantity', '?')} {m.findtext('MunitionKey', '').replace('Stock/', '')}" for m in loads)
            self.equipment.insert("", "end", iid=str(i), values=(socket.findtext("ComponentName", "").replace("Stock/", ""), contents))
        if hasattr(self, "refit_selected_button"):
            self.refit_selected_button.configure(state="disabled" if self.campaign.pending_action() else "normal")

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
        ship = empty_ship(hull, name, number)
        key = self.campaign.add_owned_hull(faction, ship, name=name, number=number)
        self.selected = next((i for i, s in enumerate(self.campaign.player.ships) if s.findtext("Key") == key), 0)
        self.refresh()
        self.status.set(f"{name} added to the campaign roster. Refit the hull to install equipment.")

    def sell_hull(self):
        def action():
            self.editable()
            if self.selected is None or not self.campaign.player:
                raise ValueError("Select a hull to sell.")
            ship = self.campaign.player.ships[self.selected]
            name = ship.findtext("Name", "this hull")
            if not messagebox.askyesno("Sell hull", f"Sell {name}? Its installed components will be returned to spare inventory. No credits are refunded.", parent=self):
                return
            returned = self.campaign.sell_owned_hull(ship.findtext("Key"))
            self.selected = min(self.selected, max(0, len(self.campaign.player.ships) - 1))
            self.refresh()
            self.status.set(f"Sold {name}. Returned {sum(returned.values())} installed component(s) to inventory.")
        self.guarded(action)

    def start_selected_refit(self):
        def action():
            if self.selected is None or not self.campaign.player:
                raise ValueError("Select a hull to refit.")
            ship = self.campaign.player.ships[self.selected]
            self.refits.start(ship_keys=[ship.findtext("Key")])
            self.refresh()
            self.tabs.select(self.refit_tab)
            self.status.set("Starting NEBULOUS and opening the selected hull in the fleet editor…")
        self.guarded(action)

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".fleet", initialfile="NBC Player.fleet", filetypes=[("NEBULOUS fleet", "*.fleet")])
        if path:
            def action():
                if not self.campaign.player or not self.campaign.can_deploy():
                    raise ValueError("Export requires 1–10 ships. Mission deployment selection is not implemented yet.")
                self.campaign.player.save(path)
            self.guarded(action)
            self.status.set(f"Exported {path}")

    def generate(self):
        def action():
            self.editable()
            budget = int(self.budget.get())
            if not 1 <= budget <= 30000:
                raise ValueError("Choose a target from 1 to 30,000 points.")
            record = self.generation.start(self.faction.get(), budget)
            self.refresh()
            self.status.set(f"Randomly fitted enemy ready: {record['ships']} ships / {record['actual_points']:,} points.")
        self.guarded(action)

    def launch(self):
        def action():
            self.campaign.launch()
            self.refresh()
            self.status.set("Battle requested. Waiting for NEBULOUS to load the bridge…")
        self.guarded(action)

    def cancel(self):
        if self.campaign.state.get("active_battle") and messagebox.askyesno("Cancel battle request", "Stop tracking this battle? The game will stay open. No result or reward will be applied."):
            self.guarded(self.campaign.cancel)
            self.refresh()

    def poll(self):
        try:
            self.campaign.pump_game()
            if self.campaign.runtime.purpose:
                self.status.set(self.campaign.runtime.message)
            if self.refits.active:
                changed = self.refits.collect()
                if changed:
                    self.refresh()
                    self.tabs.select(self.refit_tab)
                    self.deiconify()
                    self.lift()
                    self.attributes("-topmost", True)
                    self.after(700, lambda: self.attributes("-topmost", False))
                    self.focus_force()
                    self.status.set("Refit draft ready for review." if self.refits.active else "Refit cancelled. Approved fleet and inventory preserved.")
                elif self.refits.active["stage"] != "review":
                    self.status.set(self.refits.status())
                    self.refit_message.set(self.refits.status())
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
            f"{campaign.credits:,} credits  /  {campaign.ships} ships  /  "
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
            text=f"Showing {self.faction.replace('Stock/', '')} hulls only, the faction selected when this campaign began. Hulls are added empty; no credits are charged in this prototype.",
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

        self.hulls = self.app.tree(shell, ("name", "class", "points", "sockets"), ("HULL", "CLASS", "POINTS", "SOCKETS"), (330, 180, 90, 90), height=8)
        self.hulls.bind("<<TreeviewSelect>>", self._select_hull)
        self.message = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.message, foreground=ACCENT, wraplength=800).pack(anchor="w", pady=8)

        actions = ttk.Frame(shell)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        self.add_button = ttk.Button(actions, text="Buy hull", command=self.add, style="Accent.TButton")
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
        faction = self.catalog.factions.get(self.faction)
        if faction is None:
            self.message.set("The campaign faction is missing from the native fitting catalogue.")
            self.add_button.configure(state="disabled")
            return
        self.hull_rows = sorted(faction.hulls, key=lambda h: (h.class_name.casefold(), h.name.casefold(), h.points))
        for index, hull in enumerate(self.hull_rows):
            self.hulls.insert("", "end", iid=str(index), values=(hull.name, hull.class_name, hull.points, len(hull.sockets)))
        if self.hull_rows:
            self.hulls.selection_set("0")
            self._select_hull()
            self.message.set("")
            self.add_button.configure(state="normal")
        else:
            self.message.set("This faction has no usable hulls in the native catalogue.")
            self.add_button.configure(state="disabled")

    def _select_hull(self, event=None):
        selection = self.hulls.selection()
        if not selection:
            return
        index = int(selection[0])
        if not 0 <= index < len(self.hull_rows):
            return
        hull = self.hull_rows[index]
        self.name.set(self._unique_name(hull.name))
        self.number.set(str(self._unique_number()))

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
        self.busy = True
        self.add_button.configure(state="disabled")
        try:
            self.app.create_hull(self.faction, hull, name, number)
        except Exception as exc:
            self.busy = False
            self.add_button.configure(state="normal")
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    App(args.game, args.data).mainloop()

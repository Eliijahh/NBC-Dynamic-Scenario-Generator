"""Run with Python 3.10+; uses only the Python standard library."""
import copy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from nbc.core import Campaign, Fleet, DEFAULT_GAME, DEFAULT_STARTING_CREDITS, discover, put
from nbc.generation import GenerationService
from nbc.refit import RefitService, installed

BG, PANEL, TEXT, MUTED, ACCENT = "#101820", "#182530", "#e4edf4", "#9aafbf", "#53d4b1"


class App(tk.Tk):
    def __init__(self, game=DEFAULT_GAME, data=None):
        super().__init__()
        self.title("NEBULOUS · Campaign Command / Proof of Concept")
        self.geometry("1260x820")
        self.minsize(1080, 700)
        self.configure(bg=BG)
        self.campaign = Campaign(game, data)
        # Only one UI may mutate a given save; independent --data saves remain usable.
        import msvcrt
        self.save_lock = (self.campaign.data / "app.lock").open("a+b")
        self.save_lock.seek(0, 2)
        if self.save_lock.tell() == 0:
            self.save_lock.write(b"\0")
            self.save_lock.flush()
        self.save_lock.seek(0)
        try:
            msvcrt.locking(self.save_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.save_lock.close()
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
        self.ships = self.tree(left, ("name", "hull", "points"), ("SHIP", "HULL", "POINTS"), (170, 180, 65))
        self.ships.bind("<<TreeviewSelect>>", self.select_ship)
        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=10)
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
        ttk.Button(right, text="Refit fleet → NEBULOUS", command=self.start_refit, style="Accent.TButton").pack(anchor="w", pady=8)
        ttk.Label(right, text="Buy spare equipment in Inventory before opening a refit.", style="Muted.TLabel").pack(anchor="w")
        self.equipment = self.tree(right, ("component", "contents"), ("INSTALLED EQUIPMENT", "LOAD"), (250, 150), height=4)
        self.build_refit_tabs()

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
            self.menu_campaign_info.set("No saved campaign detected.")

    def show_menu(self):
        self.main.pack_forget()
        self.menu.pack(fill="both", expand=True)
        self._refresh_menu()

    def show_main(self):
        self.menu.pack_forget()
        self.main.pack(fill="both", expand=True)

    def continue_campaign(self):
        if not self.campaign.has_campaign or self.campaign.player is None:
            self._refresh_menu()
            return
        self.show_main()
        self.refresh()
        self.tabs.select(self.fleet_tab)
        self.status.set("Campaign loaded. Ready to continue.")

    def load_campaign(self):
        messagebox.showinfo(
            "Load Campaign",
            "Loading multiple saved campaigns is the next roadmap step (1.2). Use Continue to open the current campaign.",
            parent=self,
        )

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
            locked = bool(self.refits.active or self.campaign.state.get("active_battle"))
            self.buy_button.configure(state="disabled" if locked or not catalog else "normal")
            self.sell_button.configure(state="disabled" if locked else "normal")
            self.inventory_note.set("Inventory locked until the pending refit/battle finishes." if locked else f"{sum(owned.values())} spare components owned. " + (f"{len(catalog)} stock components exported by NEBULOUS." if catalog else "Start NEBULOUS with bridge 0.5.0 to export the component catalogue."))
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
        busy = bool(active or self.campaign.state.get("active_battle"))
        self.refit_start_button.configure(state="disabled" if busy else "normal")
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
        if self.refits.active or self.campaign.state.get("active_battle"):
            messagebox.showerror("New campaign", "Finish or cancel the pending battle or refit first.", parent=self)
            return
        if self.campaign.has_campaign:
            if not messagebox.askyesno(
                "Start a new campaign",
                "Replace the current campaign with a new one?\n\nThe current campaign files will be backed up under data/campaign-backups before the new save is created.",
                parent=self,
            ):
                return
        NewCampaignDialog(self)

    def create_new_campaign(self, name, commander, faction, credits, starter):
        self.status.set("Creating campaign…")
        self.update_idletasks()
        meta = self.campaign.start_new_campaign(name, commander, faction, credits, starter)
        self.selected = 0
        self.refresh()
        self.show_main()
        self.tabs.select(self.fleet_tab)
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
        if f:
            self.fleet_info.configure(text=f"{f.name}  /  {f.faction.replace('Stock/', '')}  /  {len(f.ships)} ships  /  ~{f.points:,} points")
            for i, s in enumerate(f.ships):
                self.ships.insert("", "end", iid=str(i), values=(s.findtext("Name"), s.findtext("HullType", "").replace("Stock/", ""), s.findtext("Cost")))
            if f.ships:
                self.ships.selection_set(str(min(self.selected or 0, len(f.ships) - 1)))
        else:
            self.fleet_info.configure(text="No campaign fleet loaded.")
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
        busy = bool(self.campaign.state.get("active_battle") or self.refits.active)
        self.run_button.configure(state="disabled" if busy or not self.campaign.player or not self.campaign.enemy else "normal")
        self.generate_button.configure(state="disabled" if busy or not self.campaign.has_campaign else "normal")
        self._refresh_menu()
        self.refresh_inventory()
        self.refresh_refit()

    def select_ship(self, event=None):
        selection = self.ships.selection()
        if not selection:
            return
        self.selected = int(selection[0])
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
            self.campaign.save_fleets()
            self.refresh()
        self.guarded(action)

    def save_ship(self):
        def action():
            self.editable()
            if self.selected is None:
                return
            number = int(self.number.get())
            if not 0 <= number <= 2147483647:
                raise ValueError("Hull number must be a non-negative integer.")
            f = self.campaign.player.clone()
            put(f.ships[self.selected], "Name", self.name.get().strip())
            put(f.ships[self.selected], "Number", number)
            f.validate()
            self.campaign.player = f
            self.campaign.save_fleets()
            self.refresh()
            self.status.set("Ship details saved.")
        self.guarded(action)

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".fleet", initialfile="NBC Player.fleet", filetypes=[("NEBULOUS fleet", "*.fleet")])
        if path:
            self.guarded(lambda: self.campaign.player.save(path))
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

    def close(self):
        self.guarded(self.campaign.save_fleets)
        self.save_lock.close()
        self.destroy()


class NewCampaignDialog(tk.Toplevel):
    """Modal setup screen for the single active campaign save."""

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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    App(args.game, args.data).mainloop()

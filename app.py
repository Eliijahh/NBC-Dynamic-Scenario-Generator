"""Run with Python 3.10+; uses only the Python standard library."""
import copy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import xml.etree.ElementTree as ET

from nbc.core import Campaign, Fleet, DEFAULT_GAME, discover, generate_enemy, put

BG, PANEL, TEXT, MUTED, ACCENT = "#101820", "#182530", "#e4edf4", "#9aafbf", "#53d4b1"


class App(tk.Tk):
    def __init__(self, game=DEFAULT_GAME, data=None):
        super().__init__()
        self.title("NEBULOUS · Campaign Command / Proof of Concept")
        self.geometry("1260x820")
        self.minsize(1080, 700)
        self.configure(bg=BG)
        self.campaign = Campaign(game, data)
        self.templates = discover(game)
        self.selected = None
        self.loading = False
        self._style()
        self._build()
        if not self.campaign.player and self.templates:
            default = next((f for f in self.templates if f.source and f.source.parent.name == "Starter Fleets - Alliance"), self.templates[0])
            self.campaign.player = default.clone()
            self.campaign.save_fleets()
        self.refresh()
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
        shell = ttk.Frame(self, padding=24)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="CAMPAIGN COMMAND", foreground=ACCENT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        ttk.Label(shell, text="Your fleet. Every engagement.", style="Title.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(shell, text="Proof of concept  /  Native fleet editing → live skirmish → battle results", style="Muted.TLabel").pack(anchor="w")
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
        for frame, title in [(self.fleet_tab, "01  Fleet"), (self.battle_tab, "02  Battle"), (self.result_tab, "03  Debrief")]:
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
        ttk.Button(buttons, text="Duplicate ship", command=lambda: self.edit_composition("duplicate")).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Remove ship", command=lambda: self.edit_composition("remove")).pack(side="left")
        ttk.Label(right, text="SHIP DETAILS", foreground=ACCENT).pack(anchor="w")
        row = ttk.Frame(right)
        row.pack(fill="x", pady=8)
        self.name = tk.StringVar()
        self.number = tk.StringVar()
        ttk.Entry(row, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Entry(row, textvariable=self.number, width=7).pack(side="left")
        ttk.Button(right, text="Save ship details", command=self.save_ship).pack(anchor="w", pady=8)
        ttk.Label(right, text="Complete loadout · same hull", style="Muted.TLabel").pack(anchor="w", pady=(8, 4))
        self.loadouts = ttk.Combobox(right, state="readonly", width=45)
        self.loadouts.pack(fill="x")
        ttk.Button(right, text="Apply selected loadout", command=self.apply_loadout).pack(anchor="w", pady=8)
        ttk.Button(right, text="Edit selected component…", command=self.edit_equipment).pack(anchor="w", pady=6)
        ttk.Label(right, text="Known socket-compatible parts; NEBULOUS recalculates points.", style="Muted.TLabel").pack(anchor="w")
        self.equipment = self.tree(right, ("component", "contents"), ("INSTALLED EQUIPMENT", "LOAD"), (250, 150), height=4)
        self.equipment.bind("<Double-1>", self.edit_equipment)

        ttk.Label(self.battle_tab, text="Prepare an engagement", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(self.battle_tab, text="Enemy composition is sampled from installed stock fleet designs, near your selected point budget.", style="Muted.TLabel").pack(anchor="w", pady=8)
        controls = ttk.Frame(self.battle_tab)
        controls.pack(fill="x", pady=12)
        self.faction = ttk.Combobox(controls, state="readonly", values=["Stock/Protectorate", "Stock/Alliance"], width=23)
        self.faction.current(0)
        self.faction.pack(side="left", padx=(0, 10))
        self.budget = tk.StringVar(value="700")
        ttk.Entry(controls, textvariable=self.budget, width=9).pack(side="left", padx=(0, 8))
        ttk.Label(controls, text="target points", style="Muted.TLabel").pack(side="left", padx=(0, 14))
        ttk.Button(controls, text="Generate enemy", command=self.generate).pack(side="left")
        self.enemy_info = ttk.Label(self.battle_tab, text="Generate an enemy to continue.", foreground=ACCENT)
        self.enemy_info.pack(anchor="w", pady=8)
        self.enemies = self.tree(self.battle_tab, ("name", "hull", "points"), ("ENEMY SHIP", "HULL", "POINTS"), (260, 400, 100), height=6)
        launchbar = ttk.Frame(self.battle_tab)
        launchbar.pack(fill="x", pady=18)
        self.run_button = ttk.Button(launchbar, text="Run battle  →  NEBULOUS", style="Accent.TButton", command=self.launch)
        self.run_button.pack(side="left")
        ttk.Button(launchbar, text="Cancel pending request", command=self.cancel).pack(side="left", padx=12)
        ttk.Label(self.battle_tab, text="First run: install the bridge, then enable “NBC Campaign Bridge” in the game's Mods menu.\nThe bridge creates a local skirmish and assigns both fleets. You choose deployment and play.\nResults appear here automatically when the battle ends. Damage is recorded for review; it is not restored in the next battle yet.", style="Muted.TLabel", wraplength=1050).pack(anchor="w")

        ttk.Label(self.result_tab, text="Battle history", style="Sub.TLabel").pack(anchor="w", pady=(0, 12))
        self.history = self.tree(self.result_tab, ("time", "outcome", "duration", "id"), ("ENGAGEMENT", "OUTCOME", "SECONDS", "BATTLE ID"), (230, 140, 100, 330), height=5)
        self.history.bind("<<TreeviewSelect>>", self.select_result)
        self.results = self.tree(self.result_tab, ("side", "name", "status", "condition", "crew", "damage"), ("SIDE", "SHIP", "STATUS", "CONDITION", "CREW", "DAMAGE DEALT"), (90, 230, 140, 100, 100, 130), height=7)
        ttk.Label(self.result_tab, text="Raw battle reports and input fleets are archived under data/battles/<battle ID>/.", style="Muted.TLabel").pack(anchor="w", pady=10)

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
        if self.campaign.state.get("active_battle"):
            raise ValueError("Finish or cancel the pending battle before changing fleets.")

    def refresh(self):
        self.ships.delete(*self.ships.get_children())
        f = self.campaign.player
        if f:
            self.fleet_info.configure(text=f"{f.name}  /  {f.faction.replace('Stock/', '')}  /  {len(f.ships)} ships  /  ~{f.points:,} points")
            for i, s in enumerate(f.ships):
                self.ships.insert("", "end", iid=str(i), values=(s.findtext("Name"), s.findtext("HullType", "").replace("Stock/", ""), s.findtext("Cost")))
            if f.ships:
                self.ships.selection_set(str(min(self.selected or 0, len(f.ships) - 1)))
        self.enemies.delete(*self.enemies.get_children())
        if self.campaign.enemy:
            e = self.campaign.enemy
            self.enemy_info.configure(text=f"{len(e.ships)} hostile ships  /  {e.points:,} points  /  {e.faction.replace('Stock/', '')}")
            for s in e.ships:
                self.enemies.insert("", "end", values=(s.findtext("Name"), s.findtext("HullType", "").replace("Stock/", ""), s.findtext("Cost")))
        self.history.delete(*self.history.get_children())
        for b in reversed(self.campaign.state["battles"]):
            self.history.insert("", "end", iid=b["id"], values=(b["created"][:19].replace("T", " ") + " UTC", b.get("outcome", b["status"]), b.get("duration", "—"), b["id"]))
        self.run_button.configure(state="disabled" if self.campaign.state.get("active_battle") else "normal")

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

    def edit_equipment(self, event=None):
        def action():
            self.editable()
            if self.selected is None or not self.equipment.selection():
                raise ValueError("Select an installed component first.")
            socket = self.campaign.player.ships[self.selected].findall("SocketMap/HullSocket")[int(self.equipment.selection()[0])]
            key = socket.findtext("Key")
            options, seen = [], set()
            for f, i in self.loadout_options:
                candidate = f.ships[i].find(f"SocketMap/HullSocket[Key='{key}']")
                if candidate is not None:
                    signature = ET.tostring(candidate)
                    if signature not in seen:
                        seen.add(signature)
                        options.append((f, i, candidate.findtext("ComponentName", "")))
            if not options:
                raise ValueError("No compatible parts found in the imported fleet library.")
            dialog = tk.Toplevel(self)
            dialog.title("Edit component")
            dialog.configure(bg=BG)
            dialog.geometry("640x210")
            dialog.transient(self)
            ttk.Label(dialog, text="Choose a known loadout for this exact hull socket", style="Sub.TLabel").pack(anchor="w", padx=20, pady=18)
            choice = ttk.Combobox(dialog, state="readonly", values=[f"{part.replace('Stock/', '')} / {f.name}" for f, _, part in options], width=70)
            choice.pack(fill="x", padx=20)
            choice.current(0)
            ttk.Label(dialog, text="Associated ammunition and missile designs are copied with the component.", style="Muted.TLabel").pack(anchor="w", padx=20, pady=10)
            def apply():
                def change():
                    f, i, _ = options[choice.current()]
                    self.campaign.player.apply_component(self.selected, key, f, i)
                    self.campaign.save_fleets()
                    dialog.destroy()
                    self.refresh()
                    self.status.set("Component replaced. Points will be recalculated by the game.")
                self.guarded(change)
            ttk.Button(dialog, text="Apply component", command=apply).pack(anchor="e", padx=20)
            dialog.grab_set()
        self.guarded(action)

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

    def edit_composition(self, method):
        def action():
            self.editable()
            if self.selected is not None:
                getattr(self.campaign.player, method)(self.selected)
                self.campaign.save_fleets()
                self.refresh()
        self.guarded(action)

    def apply_loadout(self):
        def action():
            self.editable()
            idx = self.loadouts.current()
            if idx < 0 or self.selected is None:
                raise ValueError("Choose a ship and a loadout first.")
            f, ship_index = self.loadout_options[idx]
            self.campaign.player.apply_loadout(self.selected, f, ship_index)
            self.campaign.save_fleets()
            self.refresh()
            self.status.set("Complete equipment loadout applied; ship identity preserved.")
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
            enemy, source = generate_enemy(self.templates, self.faction.get(), budget)
            self.campaign.enemy = enemy
            self.campaign.save_fleets()
            self.refresh()
            self.status.set(f"Enemy generated from {source}. Target {budget:,}; actual {enemy.points:,} points.")
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
            if self.campaign.state.get("active_battle"):
                result = self.campaign.collect()
                if result:
                    self.refresh()
                    self.tabs.select(self.result_tab)
                    self.history.selection_set(result["id"])
                    self.select_result()
                    self.status.set(f"Battle imported: {result['outcome']}. Your debrief is ready.")
                    self.bell()
                else:
                    self.status.set(self.campaign.bridge_status())
        except Exception as exc:
            self.status.set(f"Result import: {exc}")
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
        self.destroy()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    App(args.game, args.data).mainloop()

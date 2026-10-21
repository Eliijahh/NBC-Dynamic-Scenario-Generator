# NBC Dynamic Scenario Generator

A Windows desktop proof of concept with a shared equipment inventory, credit-priced hulls and components, native in-game fleet refitting, offline random ship fitting, local skirmishes and automatic battle-report import.

## Offline enemy generation (bridge 0.5)

1. Install bridge 0.5 and launch NEBULOUS once with **NBC Campaign Bridge** enabled. The bridge writes `Saves/NBCBridge/fitting-catalog.xml`, containing the stock hulls, sockets, components, munitions and fitting metadata.
2. On **Battle**, choose a faction and target points, then **Generate enemy**. The app fits individual ships and their component loadouts locally; it does not open the fleet editor or launch NEBULOUS.
3. The fitter uses slot type and size, overhang and rotation rules, tiling, power, crew, bulk-magazine volume and point costs from the exported catalogue. It builds up to 24 candidate ships, then selects up to 10 closest to the target budget.
4. **v1 scope:** direct-fire weapons and bulk-magazine ammunition only. Missile launchers, hangars and craft are excluded. Generated fleets are loaded by the normal battle handoff and checked again by the game at that point.

## Native refitting and inventory (bridge 0.5)

1. Close NEBULOUS and run **Install Bridge.ps1** to update the bridge. Keep **NBC Campaign Bridge** enabled in Mods. Enable the bridge once in the game; the app's Inventory tab also has **Connect / open NEBULOUS**.
2. Open **Start Campaign.cmd** in this project folder. The campaign roster is stored as owned hulls. Use **Buy hull** to choose a stock hull from the campaign faction, filter by hull class, and create it empty; native points, credit price and owned count are shown, and the purchase is confirmed before credits are deducted. Or use **Create 3-ship test fleet** to create a disposable three-frigate roster. Roster replacement actions back up the previous roster under `data/roster-backups`.
3. In **Inventory**, search or filter by component type, then select equipment and a quantity. The store shows native points, owned spares, credit price and 10% sale value, plus whether the selected quantity is affordable. **Buy** asks for confirmation, adds spares and debits credits; **Sell** removes spares and returns 10% of their value. Store stock is unlimited. Ammunition is free for the PoC. Installed components cannot be sold directly. Hulls can be renamed, sold back to components and refitted individually; selling a hull returns 10% of its value plus its installed components. Click any Fleet or Inventory column header to cycle through descending, ascending and unsorted order; the header shows ▼ or ▲ while a sort is active.
4. Click **Refit selected hull → NEBULOUS** to edit one owned hull, or **Refit whole fleet → NEBULOUS** when the roster has ten or fewer ships. The app launches the game automatically and waits for the live bridge. From its main menu the bridge loads the selected scope into the normal game editor. The component palette shows available equipment and spare counts. Removing a part makes it available on other ships. Normal socket compatibility and design calculations remain in the game.
5. Use the overlay's **Review refit → Campaign** button to export a draft. After the app validates and saves the draft, NEBULOUS closes automatically. The app opens its Refit tab with a list of proposed component changes. Choose **Confirm refit**, **Continue / resume editor**, or **Cancel refit**. Continuing launches NEBULOUS again with the draft. Discarding a refit, either in game or in the app, closes its game session and preserves the approved fleet.

The game uses the normal editor, with additional inventory checks on installation, component paste and undo/redo. If a component removed from Ship A has been installed on Ship B, undoing the removal on A is blocked until another copy is available. Native per-ship history is retained; it is not replaced with a global undo stack. Whole-fitting paste, roster changes and normal save/load/test actions are blocked during campaign refits. Ordinary editor sessions and skirmishes do not use these restrictions.

Buying/selling, roster edits and refits are locked while a mission, battle or refit is pending. Purchases before a session survive cancellation. The original fleet and spare inventory remain approved until confirmation. Confirmation atomically updates both in `data/campaign.json`; `data/player.fleet` is a derived export. Restarting the app retains the pending session. **Continue / resume editor** restarts from the last exported draft, or from the session input if no draft was exported. Unsaved in-game changes are not recovered after a crash. Use the overlay to cancel in game, or cancel from the app.

This milestone tracks component **types, quantities and destruction**, not per-component hit points. Ammunition, missile/craft construction and component repair condition are outside inventory tracking. Existing ammunition/design data remains native game data. The store catalogue comes from installed stock component assets; owned hulls and generated-enemy fitting statistics come from the bridge's exported catalogue rather than being hard-coded in Python. Hull ownership is unlimited; the native game's ten-ship limit applies only when a fleet is exported for a battle. Mission deployment selection above ten hulls remains future work, so battles and exports are disabled above ten until that screen exists. Imported fleets and the test-fleet generator remain sandbox setup tools.

## Campaign economy and prices

`prices.json` is the separate economy data file. It contains formulas, fallback prices and optional per-item overrides keyed by native catalogue key. The default formulas are:

- Components: `1,000 + 100 × native points²`, minimum `2,000`, rounded to the nearest `1,000`.
- Hulls: `100,000 + 4,000 × native points`, minimum `100,000`, rounded to the nearest `5,000`.
- Unknown catalogue entries fall back to `5,000` for components and `100,000` for hulls.
- Sales return `10%` of the hull or component value. Ammunition has no campaign price.

Every stock hull and component in the bridge catalogue is priced through the file; an override wins over the formula. Credit changes are integer-only, cannot make the wallet negative, and are applied in the same save transaction as the roster or inventory change. Each change appends an entry to the campaign's `credit_ledger` with the amount, resulting balance, kind, description, timestamp and item details. The PoC does not include maintenance, repair costs, bankruptcy rescue or deployment limits yet.

With a campaign open, go to **Settings** and press **SHIFT-X**. A hidden debug section appears with **Open price debug (Hulls / Components)**. The resulting screen has two tabs that show each native hull or component's faction, native point value, credit price and 10% sale value side by side. The shortcut only reveals the section while Settings is visible; it is not a normal player-facing screen.

## Campaign save data

Campaign saves use schema version 2. On open, version 1 saves are first copied to `campaign-backups/<timestamp>-schema-v1-to-v2/`, then migrated in place. A current-version save that needs new fields is also backed up before normalization. Existing rosters with no owned-hull records are backfilled into `owned_hulls` after a `campaign-backups/<timestamp>-owned-hulls/` backup. The JSON save includes campaign metadata and credits, the credit ledger, campaign status, per-ship roster state, authoritative owned hulls, mission offers, an accepted mission, inventory, battle/refit history and pending operations. `player.fleet` and `enemy.fleet` remain derived exports; `campaign.json` is authoritative.

State changes, including credit debits, refunds and ledger entries, commit atomically, and a failed commit restores the previous in-memory state. **Save now** and **Save and exit** also create a timestamped backup under the campaign's `campaign-backups/` folder. Pending battles, refits and accepted missions survive restarts, and **Continue** returns to the relevant tab. Missions themselves remain future work; the save fields and resume hook are in place.

The runtime patches use vendored **Harmony 2.3.3**, net472 merged assembly, from `https://www.nuget.org/packages/Lib.Harmony/2.3.3`. Its MIT license is included in `bridge/lib/Harmony-LICENSE.txt`; no game libraries are distributed. Game updates can require adapting editor hooks and rebuilding the bridge.

## Run

Requirements: Windows, Python 3.10+ with Tkinter, NEBULOUS: Fleet Command, and Steam. Building the bridge also requires a .NET SDK. The Python app has no third-party dependencies.

1. Close NEBULOUS. In PowerShell, from this directory, run:

   ```powershell
   & '.\Install Bridge.ps1' -GamePath 'R:\SteamLibrary\steamapps\common\Nebulous'
   ```

2. Start NEBULOUS normally. Open **Mods**, enable **NBC Campaign Bridge**, apply the change, and return to the main menu. This is a one-time step. A running game must be restarted after updating the bridge DLL. Returning to the main menu is enough for the bridge to export the fitting catalogue.
3. Double-click **Start Campaign.cmd**, or run `python app.py`. The app opens on the main menu with **Continue**, **New Campaign**, **Load Campaign** and **Exit**. **Continue** is shown only when the last-played campaign is valid. **New Campaign** creates an independent save under `data/campaigns/<id>/`, with its own fleet, inventory, credit wallet and ledger, battle history and pending operations. The existing `data/campaign.json` save remains available as the legacy **root** campaign. **Load Campaign** lists saved campaigns with commander, faction, credits, fleet size, last-played time and save status; select one and choose **Load Selected**. A campaign with a pending mission, battle or refit must be finished or cancelled before another campaign can be loaded. The open campaign is locked, and the lock moves with you when you switch saves.
4. On **Fleet**, buy hulls from the native catalogue (credits are deducted), load an installed fleet or import a `.fleet` file. Rename hulls, change hull numbers or sell a hull for 10% of its value and return its installed components to inventory. Use the native refit workflow above to change equipment. Original fleet files are preserved.
5. On **Battle**, choose an enemy faction and target point budget, then **Generate enemy**. The app fits new ships and loadouts from the native catalogue. The actual total may differ from the target.
6. Click **Run battle → NEBULOUS**. The game opens if necessary. If it is already running, return to its main menu. The bridge creates a local skirmish, assigns your fleet and one AI enemy, validates settings and launches it. Deploy your ships and play normally.
7. When the battle ends, **Debrief** fills automatically. Return to the external app to view the outcome, ship condition, casualties and damage statistics. The app applies the report to the roster: ships become ready or damaged, ships lost with their reactor are removed, and components reduced to zero HP are scrapped. NEBULOUS closes automatically after the app validates and saves the report, and the app brings the Debrief forward.
8. Use the gear at the top-right of the campaign header to open **Settings**. **Save now** forces a save and backs up the previous save, **Resume** returns to the previous tab, and **Save and exit** / **Exit without saving** close Campaign Command with or without a final save. The campaign still saves automatically as you play.

To use another game location:

```powershell
python app.py --game 'D:\SteamLibrary\steamapps\common\Nebulous'
```

## Scope of this prototype

- Native `.fleet` import/export, names, hull numbers, fleet composition, equipment substitutions and complete loadouts.
- Unlimited owned hulls stored in the campaign save, including credit-priced empty-hull purchase from the native catalogue, unique identity/name/number, individual refits, readiness display and hull sale that returns 10% of value plus installed components.
- A credit wallet with a persistent audit ledger, formula-based prices in `prices.json`, per-item overrides, fallback prices, atomic purchases/sales and a non-negative balance guard. Hull and component stores show native points, credit prices, owned counts and affordability, and confirm purchases before debiting. Ammunition is free for the PoC.
- Versioned, atomically written local saves with schema migration, previous-save backups and per-battle archives. Closing and reopening the app retains pending missions, battles and refits and their eventual results.
- Multiple independent campaigns selected from the main menu, with metadata, last-played tracking and corrupt-save reporting.
- Initial Settings screen with manual save, resume, save-and-exit and exit-without-saving controls.
- Offline random enemy ships and component loadouts generated from native stock fitting data. v1 supports direct-fire weapons and bulk magazines; missiles, hangars and craft are excluded.
- Automatic handoff to a real local skirmish and automatic capture of a full native after-action report.
- Report records include a battle ID and player ship IDs; importing a completed battle twice is prevented.
- **Battle damage is applied after import.** Ship condition is stored for readiness and the debrief, ships lost with their reactor are removed from the roster, and zero-HP components are scrapped permanently. Repair actions, maintenance, repair costs, bankruptcy rescue and deployment blocking for damaged ships remain future campaign features; the credit wallet and purchase/sale economy are in place.
- Refits still use the native editor, which handles socket compatibility, power, magazines, faction restrictions and design warnings. The offline fitter mirrors those socket, overhang, tiling, power, crew, magazine and point rules for generated enemies. The campaign bridge adds owned-component limits. Imported designs can contain cached point values until the game recalculates them.
- The game bridge currently uses the game's default map/scenario. Deployment and tactical play remain under player control.
- Use for local single-player testing. The app does not modify multiplayer sessions or automate combat.

The bridge was built against the local NEBULOUS 0.6.2.6 installation. Rebuild against your installed assemblies after a game update. It waits for the local host to gain authority over the AI slot before assigning its fleet, then checks that both selections were retained. It reads the game's private `_fullAAR` field because there is no verified external battle-report API; a game update may require adapting that access.

## Files and handoff

```text
app.py                       Native Tkinter UI
prices.json                  Credit formulas, fallbacks and per-item price overrides
nbc/core.py                  XML editing, fleet/campaign state, wallet and bridge exchange
nbc/economy.py               Price book, credit trades and sale values
nbc/library.py               Campaign discovery, metadata and last-played tracking
nbc/fitting.py               Native catalogue parser and offline ship fitter
nbc/generation.py            Candidate selection and enemy persistence
nbc/refit.py                 Inventory, draft validation and atomic refit confirmation
nbc/runtime.py               Nonblocking startup, process identity and export acknowledgements
bridge/NBCBridge.cs          Code-only IModEntryPoint mod
bridge/FittingCatalog.cs     Full stock fitting catalogue exporter
bridge/RefitBridge.cs        Scoped native editor hooks and refit exchange
bridge/LifecycleBridge.cs    Live heartbeat and acknowledged graceful shutdown
bridge/lib/                  Pinned Harmony library and MIT license
bridge/ModInfo.xml           Local mod manifest
scripts/Build Bridge.ps1     Compile against installed game libraries
Install Bridge.ps1          Install only this mod's files
data/player.fleet           Legacy root campaign's working player fleet (ignored by Git)
data/enemy.fleet            Legacy root campaign's generated enemy (ignored by Git)
data/campaign.json          Legacy root campaign metadata, credit ledger, battle history and ship status
data/campaigns/<id>/         Independent campaign saves with their own state and archives
data/campaigns.json         Last-played campaign pointer
data/campaign-backups/      Top-level files backed up before migration, replacement or a manual save
data/battles/<id>/           Input fleets, native report.xml and result.xml
data/refits/<id>/            Original fleet, revision inputs and exported drafts
tests/fixtures/              Synthetic fitting catalogue used by offline tests
docs/REFIT-VALIDATION.md      Automated and live refit checks
```

The app exports generated fleets into `NEBULOUS/Saves/Fleets/NBC-POC/`. The file exchange lives in `NEBULOUS/Saves/NBCBridge/`:

- `fitting-catalog.xml`: Format 1 stock hull, socket, component, munition and fitting-data export used by the offline fitter.
- `request.xml`: versioned request containing a GUID, absolute fleet paths, SHA-256 hashes and a result-directory path.
- `status.xml`: bridge progress or an actionable failure message, scoped to the battle ID.
- `cancel.xml`: explicit cancellation. Cancelling stops result tracking; it does not terminate a live game.
- `result.xml` in the archived battle folder: written atomically after `report.xml`, serving as the completion marker.

The bridge refuses modified input files after a request is submitted and rejects designs with missing component assets. Player and enemy inputs are archived before battle. Keep ship names unique within each fleet, because native reports identify ships by name; the bridge accounts for the player's fleet prefix (for example `ANS`) when mapping those names back to their fleet GUIDs. No game assemblies or extracted game assets are checked into this repository.

Refitting uses `refit-request.xml`, `refit-status.xml` and `refit-cancel.xml` in the same exchange directory. Each request has a session GUID, revision, hashed input and a shared supply equal to original installed components plus owned spares. `ready-<revision>.xml` is published after `draft-<revision>.fleet` and includes its hash and socket labels. The app validates the fleet against the original roster and reconciles component quantities again before confirmation. Only one app window may edit a given save directory; different campaigns can be opened in separate windows.

For a separate fitting fixture without replacing the default save:

```powershell
python scripts/create_refit_demo.py --data data/my-refit-demo
python app.py --data data/my-refit-demo
```

The fixture generator refuses to overwrite an existing save. The live validation notes describe the separate test save already used on this machine.

## Game lifecycle

Refit and battle actions launch the configured game executable with the game directory as its working directory. The app waits up to three minutes for bridge 0.5 to reach the main menu before publishing a request. Enemy generation is local and does not launch the game. A fresh heartbeat must match the executable path, process ID and process start time; stale status files cannot satisfy startup. An existing game in another editor or battle is left alone until you return it to the main menu. An operation waiting for export acknowledgement, or a process shutting down, is allowed to finish before the next operation starts.

The bridge writes the draft/report before its completion marker. The app validates the export and durably records an acknowledgement in the campaign save before publishing `operation-ack.xml`. Only the matching game process, operation and revision can use it to quit normally. Failed saves/imports leave the game open; exchange-write failures retry on subsequent app polls and after an app restart. No process is force-killed. Cancelling battle tracking still leaves a live battle open. Cancelling a startup can close only the idle game launched by that request, using a unique launch token. **Connect / open NEBULOUS** refreshes the equipment catalogue and closes an idle game it launched itself.

`runtime-status.xml` contains the live process heartbeat; `runtime-command.xml` contains a launch-token-scoped idle shutdown request. Refits remain drafts until confirmed in the external app. Closing or crashing the game without exporting does not recover unsaved editor changes. If startup times out, check Steam and whether the bridge is enabled, then use **Continue / resume editor** for a refit, or cancel and relaunch a battle request.

## Troubleshooting

- **Bridge not connected:** check that NBC Campaign Bridge is enabled, apply changes, and return to the game main menu. Installation alone does not enable mods.
- **Fleet incompatible:** open and re-save an old design in the current game Fleet Editor, then import it again. Load any mods required by that fleet.
- **Bridge error:** inspect the status in the external app and `Player.log` under `%USERPROFILE%\AppData\LocalLow\Eridanus Industries\Nebulous`. Cancel the pending request in the app before retrying.
- **Fitting catalogue missing:** update to bridge 0.5, launch NEBULOUS, enable the bridge, and return to the main menu once. If the export failed, `Player.log` contains a `[NBC Fitting Catalog]` error.
- **Game shows missing asset files:** launch from Steam or with the game directory as the working directory. The app does the latter automatically.
- **Game updated:** close it, rerun the bridge installer and restart.
- **Old Save Fleet State installed:** that older mod targets serialization interfaces removed in the installed game version. Do not enable it for this prototype's test.

## Checks

```powershell
python -m unittest discover -s tests -v
& '.\scripts\Build Bridge.ps1'
```

Tests cover XML preservation, ship identities, compatible/incompatible edits, socket fitting, catalogue parsing, ammunition loading, EWar role classification, deterministic offline generation, budget selection, campaign discovery, last-played tracking, corrupt-save reporting, credit formulas and overrides, wallet and ledger accounting, atomic purchase/sale rollback, battle locking, cancellation, result matching, duplicate import prevention, condition persistence, reactor-loss removal, destroyed-component scrapping, atomic battle-import rollback and round-trips of installed fleet files when available. Live game validation is a separate check; these tests use a synthetic fitting catalogue and, when present, the locally exported real catalogue. They do not simulate the Unity engine.

Earlier live validation on 16 October 2026 against NEBULOUS 0.6.2.6 confirmed the battle handoff and native report import with matching ship GUIDs; it did not verify damage carryover or a full combat engagement. On 20 October 2026, bridge 0.5 exported the real fitting catalogue, and a generated three-ship 3,000-point fleet loaded through the normal battle handoff and reached deployment against a current starter fleet without a point-cost discrepancy. The test was closed at deployment, so a completed generated-fleet battle is still a manual check.

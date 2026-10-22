# NBC Dynamic Scenario Generator

A Windows desktop proof of concept with a shared equipment inventory, credit-priced hulls and components, per-component battle damage, permanent ship loss and automatic battle-report import.

**Download:** grab the latest `NBC-Campaign-Command.exe` from the [Releases](https://github.com/Eliijahh/NBC-Dynamic-Scenario-Generator/releases/latest) page — see [Run](#run) for first-time setup.

> **Interface status (21 October 2026):** the campaign editor is being reworked around a mission generator. The app exposes four tabs — **01 Fleet**, **02 Inventory**, **03 Missions** and **04 Debrief**. The **03 Missions** board generates and lists offers, shows enemy previews and rewards, supports accept, cancel and regenerate, switches to an active-mission view once one is accepted, and opens a **deployment screen** for choosing the ships to send. The **native refit workspace is available again from the Fleet tab** (select one or more hulls and edit them in the in-game editor); the old battle controls were replaced by the mission board.

## Offline enemy generation (bridge 0.5)

1. Install bridge 0.5 and launch NEBULOUS once with **NBC Campaign Bridge** enabled. The bridge writes `Saves/NBCBridge/fitting-catalog.xml`, containing the stock hulls, sockets, components, munitions and fitting metadata.
2. The fitter fits individual ships and their component loadouts locally; it does not open the fleet editor or launch NEBULOUS. It was previously driven from the **Battle** tab, which is now the empty **03 Missions** placeholder, so it is currently reached from code and tests only.
3. The fitter uses slot type and size, overhang and rotation rules, tiling, power, crew, bulk-magazine volume and point costs from the exported catalogue. It builds up to 24 candidate ships, then selects up to 10 closest to the target budget.
4. **v1 scope:** direct-fire weapons and bulk-magazine ammunition only. Missile launchers, hangars and craft are excluded. Generated fleets are loaded by the normal battle handoff and checked again by the game at that point.

## Native refitting and inventory (bridge 0.5)

1. Close NEBULOUS and run **Install Bridge.ps1** to update the bridge. Keep **NBC Campaign Bridge** enabled in Mods. Enable the bridge once in the game; the app's Inventory tab also has **Connect / open NEBULOUS**.
2. Open **Start Campaign.cmd** in this project folder. The campaign roster is stored as owned hulls. Use **Buy hull** to choose a stock hull from the campaign faction, filter by hull class, and create it empty; native points, credit price and owned count are shown, and the purchase is confirmed before credits are deducted. Or use **Create 3-ship test fleet** to create a disposable three-frigate roster. Roster replacement actions back up the previous roster under `data/roster-backups`.
3. In **Inventory**, search or filter by component type, then select equipment and a quantity. The store shows native points, owned spares, credit price and 10% sale value, plus whether the selected quantity is affordable. **Buy** asks for confirmation, adds spares and debits credits; **Sell** removes spares and returns 10% of their value. Store stock is unlimited. Ammunition is free for the PoC. Installed components cannot be sold directly. Hulls can be renamed, sold back to components and refitted individually; selling a hull returns 10% of its value plus its installed components. Click any Fleet or Inventory column header to cycle through descending, ascending and unsorted order; the header shows ▼ or ▲ while a sort is active.
4. On **Fleet**, select one or more hulls (Ctrl/Shift+click) and click **Refit selected → NEBULOUS**, or use **Refit whole fleet → NEBULOUS** when the roster has ten or fewer ships. The app launches the game automatically and waits for the live bridge. From its main menu the bridge loads the selected scope into the normal game editor. The component palette shows available equipment and spare counts. Removing a part makes it available on other ships. Normal socket compatibility and design calculations remain in the game.
5. Use the overlay's **Review refit → Campaign** button to export a draft. After the app validates and saves the draft, NEBULOUS closes automatically and the **Refit** screen opens with the proposed component changes. Choose **Confirm refit**, **Continue / resume editor**, or **Cancel refit**. Continuing launches NEBULOUS again with the draft. Discarding a refit, either in game or in the app, closes its game session and preserves the approved fleet.

The game uses the normal editor, with additional inventory checks on installation, component paste and undo/redo. If a component removed from Ship A has been installed on Ship B, undoing the removal on A is blocked until another copy is available. Native per-ship history is retained; it is not replaced with a global undo stack. Whole-fitting paste, roster changes and normal save/load/test actions are blocked during campaign refits. Ordinary editor sessions and skirmishes do not use these restrictions.

Buying/selling, roster edits and refits are locked while a refit or battle is pending, but an accepted mission does not lock them. Purchases before a session survive cancellation. The original fleet and spare inventory remain approved until confirmation. Confirmation atomically updates both in `data/campaign.json`; `data/player.fleet` is a derived export. Restarting the app keeps a pending refit and reopens the **Refit** screen; **Continue / resume editor** restarts from the last exported draft, or from the session input if no draft was exported. Unsaved in-game changes are not recovered after a crash.

This milestone tracks component **types and quantities** in inventory, and **damage percentages** on installed hulls. Ships themselves have no health: a ship is simply **Ready** or **Damaged** according to its components. Ammunition, missile/craft construction and per-component hit-point values are outside inventory tracking. Existing ammunition/design data remains native game data. The store catalogue comes from installed stock component assets; owned hulls and generated-enemy fitting statistics come from the bridge's exported catalogue rather than being hard-coded in Python. Hull ownership is unlimited; the native game's ten-ship limit applies only when a fleet is exported for a battle. Mission deployment selection above ten hulls remains future work, so battles and exports are disabled above ten until that screen exists. Imported fleets and the test-fleet generator remain sandbox setup tools.

## Campaign economy and prices

`prices.json` is the separate economy data file. It contains formulas, fallback prices and optional per-item overrides keyed by native catalogue key. The default formulas are:

- Components: `1,000 + 100 × native points²`, minimum `2,000`, rounded to the nearest `1,000`.
- Hulls: `100,000 + 4,000 × native points`, minimum `100,000`, rounded to the nearest `5,000`.
- Unknown catalogue entries fall back to `5,000` for components and `100,000` for hulls.
- Repairs cost the damaged fraction of each component's value with a flat `30%` discount (`0.70 × value × damage`); any positive repair costs at least `1` credit, so repairing always costs less than a replacement. Destroyed components are not restored.
- Sales return `10%` of the hull or component value. Ammunition has no campaign price.

Every stock hull and component in the bridge catalogue is priced through the file; an override wins over the formula. Credit changes are integer-only, cannot make the wallet negative, and are applied in the same save transaction as the roster or inventory change. Each change appends an entry to the campaign's `credit_ledger` with the amount, resulting balance, kind, description, timestamp and item details. A ship is **Damaged** while any installed component is damaged or destroyed, which blocks battle deployment; repairing partial damage or scrapping/refitting a destroyed component makes it **Ready**. The PoC does not include maintenance or bankruptcy rescue.

With a campaign open, go to **Settings** and press **SHIFT-X**. A hidden debug section appears with **Open price debug (Hulls / Components)**. The resulting screen has two tabs that show each native hull or component's faction, native point value, credit price and 10% sale value side by side. The shortcut only reveals the section while Settings is visible; it is not a normal player-facing screen.

## Campaign save data

Campaign saves use schema version 2. On open, version 1 saves are first copied to `campaign-backups/<timestamp>-schema-v1-to-v2/`, then migrated in place. A current-version save that needs new fields is also backed up before normalization. Existing rosters with no owned-hull records are backfilled into `owned_hulls` after a `campaign-backups/<timestamp>-owned-hulls/` backup. The JSON save includes campaign metadata and credits, the credit ledger, campaign status, per-ship readiness and per-component damage, authoritative owned hulls, mission offers, an accepted mission, the selected deployment fleet, inventory, battle/refit history and pending operations. Older saves that stored a whole-ship condition and destroyed-component list are migrated to the component shape; destroyed records carry over, while a partial whole-ship condition cannot be attributed to individual components and is dropped. `player.fleet` and `enemy.fleet` remain derived exports; `campaign.json` is authoritative.

State changes, including credit debits, refunds and ledger entries, commit atomically, and a failed commit restores the previous in-memory state. **Save now** and **Save and exit** also create a timestamped backup under the campaign's `campaign-backups/` folder. Pending battles, refits and accepted missions survive restarts, and **Continue** returns to the relevant screen (the **Refit** screen for a refit, the **03 Missions** board for a mission). Mission generation and offer persistence live in `nbc/missions.py`; the **03 Missions** board consumes them.

## Mission generation

Mission offers are generated offline from the bridge catalogue and stored in the campaign save. A batch contains a random **5–10** offers and is generated when a campaign starts (Continue, New Campaign or Load Campaign), when the board is empty, and whenever **Regenerate offers** is pressed. Each offer carries an ID, name, mission type, map, enemy faction, difficulty, target budget, actual enemy points, a ship preview, a fixed enemy snapshot, a credit reward, a status and timestamps. The enemy faction is rolled **independently of the player's faction**. The only mission type for now is **annihilation**, on **Abyssal (4p)** or **The Cliff (4p)**; both are shown in the mission details. Difficulty scales the enemy budget to **0.7× / 1.0× / 1.3×** of the player's current fleet points, clamped to 500–30,000 points. The reward is **100 credits per actual enemy point**, minimum 5,000, rounded to the nearest 1,000. Offers expire after **24 hours**; duplicate or expired entries are pruned on load, and the board regenerates when the catalogue changes or the pool is empty. Rerolling is free while no mission is accepted. Accepting a mission freezes its enemy snapshot and removes it from the offer pool; refits, repairs, purchases and roster edits remain available. Cancelling returns it to the board with no credit penalty. Generation runs on a worker thread with progress and retry state. The **03 Missions** board lists each offer's faction, difficulty, points, ships, reward and expiry, shows the mission type and map plus the enemy hull preview, and provides **Accept mission** and **Regenerate offers**. Once a mission is accepted the board switches to an **active mission** view showing its name, type, map, faction, difficulty, enemy points, reward, status and full enemy fleet, with **Start mission** and **Cancel accepted mission**. **Start mission** opens the deployment screen described below.

## Deployment

**Start mission** on the accepted mission opens a separate deployment screen. It shows the accepted enemy fleet (hulls and points), the mission type and map, and your full roster with readiness. Only ready ships can be selected; damaged or lost ships are shown but cannot be included. Select ships with the **DEPLOY** cell (or Space), or use **Select all ready** / **Clear selection**. The screen shows the selected ship count and points; the native skirmish accepts at most 10 ships. **Confirm deployment** builds a temporary fleet from only the selected ships and starts NEBULOUS; **Back to mission** returns to the board without launching. The mission's **map and type are sent to the game**, which selects them in the skirmish lobby (for example **The Cliff (4P)** with the **Annihilation** scenario). The battle exports and archives only the selected ships, and the after-action report updates only those ships — unselected ships and their components are left untouched. A confirmed deployment survives an app restart and reopens on the deployment screen.

## Mission completion

When the battle ends the report is imported and the mission resolves automatically: a **victory** marks it **completed** and pays its credit reward once; a **defeat** or **draw** marks it **failed** and pays nothing. The active mission and deployment are cleared, the mission is recorded in battle history, and the Debrief shows the result and payout. Only the deployed ships are affected by damage and permanent losses.

The runtime patches use vendored **Harmony 2.3.3**, net472 merged assembly, from `https://www.nuget.org/packages/Lib.Harmony/2.3.3`. Its MIT license is included in `bridge/lib/Harmony-LICENSE.txt`; no game libraries are distributed. Game updates can require adapting editor hooks and rebuilding the bridge.

## Run

### Packaged release (recommended)

1. Download **NBC-Campaign-Command.exe** from the GitHub Release and put it in a folder you can write to. It keeps `config.json` and its campaign saves in a `data/` folder next to the executable (portable).
2. Run the exe. On first launch it finds NEBULOUS through Steam, copies its bundled bridge into `<game>\Mods\NBCBridge\`, and enables it in the game's active-mod list, then asks you to launch NEBULOUS once so the bridge can export the fitting catalogue.
3. From then on, running the exe opens Campaign Command directly. **Settings** on the main menu opens the dedicated **NEBULOUS SETUP** screen: it shows the game folder, bridge install/version, whether it is enabled and whether the catalogue is present, with **Detect**, **Change folder…**, **Install / repair bridge** and **Open NEBULOUS**. **Install / repair bridge** asks for confirmation, installs and enables the bridge, launches NEBULOUS so it can load the bridge and export the catalogue, then closes the game and reports success. The in-game gear opens campaign Settings, which links to the same setup screen.

The exe bundles the bridge, so you do not need Python, the .NET SDK or the source. The build is not code-signed, so Windows SmartScreen may warn on first run (**More info → Run anyway**), and some antivirus tools flag PyInstaller one-file builds as a false positive.

### From source (development)

Requirements: Windows, Python 3.10+ with Tkinter, NEBULOUS: Fleet Command, and Steam. Building the bridge also requires a .NET SDK. The Python app has no third-party dependencies.

1. Close NEBULOUS. In PowerShell, from this directory, run:

   ```powershell
   & '.\Install Bridge.ps1' -GamePath 'R:\SteamLibrary\steamapps\common\Nebulous'
   ```

2. Start NEBULOUS normally. Open **Mods**, enable **NBC Campaign Bridge**, apply the change, and return to the main menu. A running game must be restarted after updating the bridge DLL. Returning to the main menu is enough for the bridge to export the fitting catalogue.
3. Double-click **Start Campaign.cmd**, or run `python app.py`. The app uses a `data/` folder next to the source tree; pass `--game` and `--data` to override the paths.

### Building the release

```powershell
& '.\scripts\Build Release.ps1'
```

This builds the bridge and then produces `build/release/NBC-Campaign-Command.exe`, a portable one-file executable with the bridge and `prices.json` bundled. It needs the game installed on the build machine (for the bridge's game assemblies) and downloads PyInstaller into `.tools/venv-build`.

### Publishing a release

```powershell
& '.\scripts\Publish Release.ps1' -Tag v0.1.0
```

Install the GitHub CLI once (`winget install --id GitHub.cli`) and sign in (`gh auth login`). The script requires a clean working tree, builds the release, writes a SHA-256 checksum, tags the current commit, pushes the tag, and creates the GitHub Release with the exe and checksum attached. Use `-DryRun` to preview the steps, `-SkipBuild` to reuse an existing exe, and edit `docs/RELEASE-NOTES.md` for the release notes.

## Playing a campaign

1. The app opens on the main menu with **Continue**, **New Campaign**, **Load Campaign**, **Settings** and **Exit**. **Continue** is shown only when the last-played campaign is valid. **Settings** opens the dedicated NEBULOUS setup screen even when no campaign is loaded. **New Campaign** creates an independent save under `data/campaigns/<id>/`, with its own fleet, inventory, credit wallet and ledger, battle history and pending operations. The existing `data/campaign.json` save remains available as the legacy **root** campaign. **Load Campaign** lists saved campaigns with commander, faction, credits, fleet size, last-played time and save status; select one and choose **Load Selected**. A campaign with a pending battle or refit must be finished or cancelled before another campaign can be loaded; an accepted mission does not block switching and is restored with its save. The open campaign is locked, and the lock moves with you when you switch saves.
2. On **Fleet**, buy hulls from the native catalogue (credits are deducted), load an installed fleet or import a `.fleet` file. Rename hulls, change hull numbers or sell a hull for 10% of its value and return its installed components to inventory. The fleet list shows **Ready** or a weighted damage percentage for each hull: component damage is summed and spread across all installed components, so one of ten components at 90% damage reads as **9% Damaged**. The equipment list shows each damaged component's damage percentage and marks destroyed ones as **DESTROYED**. The repair panel shows the selected hull's damaged components and repair cost, with **Repair selected** and **Repair all** actions; destroyed components must be scrapped or refitted instead. Select an installed component and use **Scrap component** to discard it permanently without credits or a spare-parts return. Select one or more hulls and use **Refit selected → NEBULOUS** (or **Refit whole fleet → NEBULOUS** for up to ten hulls) to change equipment in the native editor. Original fleet files are preserved.
3. **03 Missions** shows a generated batch of 5–10 offers. Select one to inspect its enemy hulls, points, reward, faction and expiry. **Accept mission** freezes that enemy snapshot and removes the offer from the pool, **Cancel accepted mission** returns it to the board without a reward, and **Regenerate offers** replaces the batch (disabled while a mission is accepted). A batch is generated automatically on campaign start and whenever the board is empty.
4. Press **Start mission** to open the deployment screen. Select the ships to send (only ready ships can be chosen), then **Confirm deployment** to build the selected fleet and start NEBULOUS. Only the selected ships are exported to the skirmish, and only they receive battle results. **Back to mission** returns without launching. The native skirmish accepts at most 10 ships.
5. When a battle ends, **Debrief** fills automatically. Return to the external app to view the outcome, mission result and payout, ship condition, casualties and damage statistics. A victory completes the mission and pays its reward once; a defeat or draw fails it with no payout, and the active mission is cleared. The app applies the report to the selected ships only: each installed component's damage percentage is stored, a ship becomes **Damaged** while any component is damaged or destroyed, ships lost with their reactor are removed, and components reduced to zero HP are flagged as destroyed. The **Losses** panel lists every destroyed ship, whether it was lost to a reactor detonation or an outright hull kill, and the components lost with it. The debrief's **Condition** column is the game's native whole-ship value. Destroyed components stay installed until you scrap or replace them. NEBULOUS closes automatically after the app validates and saves the report, and the app brings the Debrief forward.
6. Use the gear at the top-right of the campaign header to open **Settings**. **Save now** forces a save and backs up the previous save, **Resume** returns to the previous tab, and **Save and exit** / **Exit without saving** close Campaign Command with or without a final save. The campaign still saves automatically as you play.

## Scope of this prototype

- Native `.fleet` import/export, names, hull numbers, fleet composition, equipment substitutions and complete loadouts.
- Unlimited owned hulls stored in the campaign save, including credit-priced empty-hull purchase from the native catalogue, unique identity/name/number, individual refits, readiness display and hull sale that returns 10% of value plus installed components.
- A credit wallet with a persistent audit ledger, formula-based prices in `prices.json`, per-item overrides, fallback prices, atomic purchases/sales and a non-negative balance guard. Hull and component stores show native points, credit prices, owned counts and affordability, and confirm purchases before debiting. Ammunition is free for the PoC.
- Versioned, atomically written local saves with schema migration, previous-save backups and per-battle archives. Closing and reopening the app retains pending battles, refits and accepted missions and their eventual results.
- Multiple independent campaigns selected from the main menu, with metadata, last-played tracking and corrupt-save reporting.
- Initial Settings screen with manual save, resume, save-and-exit and exit-without-saving controls.
- Offline random enemy ships and component loadouts generated from native stock fitting data. v1 supports direct-fire weapons and bulk magazines; missiles, hangars and craft are excluded.
- Automatic handoff to a real local skirmish and automatic capture of a full native after-action report.
- Report records include a battle ID and player ship IDs; importing a completed battle twice is prevented.
- **Battle damage is applied after import.** The after-action report supplies each installed component's remaining health, and the ship state stores the damaged fraction of every component that took damage. A ship is **Ready** only when all components are at full health; any damaged or destroyed component makes it **Damaged**, which blocks deployment. Repairs restore every partially damaged component instantly for `0.70 × value × damage` from the Fleet repair panel. Destroyed components cannot be repaired, are shown as **DESTROYED**, and keep the ship **Damaged** until scrapped or replaced by refitting. Ships lost with their reactor are removed from the roster. **Scrap component** permanently discards the selected installed or destroyed component without credits or a spare-parts return. Maintenance and bankruptcy rescue remain future campaign features; the credit wallet and purchase/sale economy are in place.
- **Permanent loss.** A ship destroyed outright or left without reactors is removed from the roster for good, and its installed components are lost with it rather than returned to spare inventory. The roster and battle result are written in the same atomic save, the **Losses** panel in the Debrief reports the destroyed ships and the components lost with them, and a lost ship never appears in a later deployment.
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
nbc/missions.py              Mission offers, difficulty, rewards and lifecycle
nbc/refit.py                 Inventory, draft validation and atomic refit confirmation
nbc/runtime.py               Nonblocking startup, process identity and export acknowledgements
nbc/setup.py                 Portable config, Steam detection and automatic bridge install
bridge/NBCBridge.cs          Code-only IModEntryPoint mod
bridge/FittingCatalog.cs     Full stock fitting catalogue exporter
bridge/RefitBridge.cs        Scoped native editor hooks and refit exchange
bridge/LifecycleBridge.cs    Live heartbeat and acknowledged graceful shutdown
bridge/lib/                  Pinned Harmony library and MIT license
bridge/ModInfo.xml           Local mod manifest
scripts/Build Bridge.ps1     Compile against installed game libraries
scripts/Build Release.ps1    Package the portable one-file executable with the bridge bundled
scripts/Publish Release.ps1  Tag and publish a GitHub Release with the exe and checksum
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
docs/RELEASE-NOTES.md         Release notes used by Publish Release.ps1
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

`runtime-status.xml` contains the live process heartbeat; `runtime-command.xml` contains a launch-token-scoped idle shutdown request. Refits remain drafts until confirmed in the external app. Closing or crashing the game without exporting does not recover unsaved editor changes. If startup times out, check Steam and whether the bridge is enabled. The app currently has no refit or battle-startup controls while the mission generator is being built.

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
& '.\scripts\Build Release.ps1'
```

Tests cover XML preservation, ship identities, compatible/incompatible edits, socket fitting, catalogue parsing, ammunition loading, EWar role classification, deterministic offline generation, budget selection, campaign discovery, last-played tracking, corrupt-save reporting, credit formulas and overrides, wallet and ledger accounting, per-component repair quotes, selected/all repair charging and rollback, manual component scrapping, damaged-ship deployment blocking, atomic purchase/sale rollback, battle locking, cancellation, result matching, duplicate import prevention, per-component damage persistence, ship-state migration, reactor-loss removal, destroyed-component scrapping, atomic battle-import rollback, mission list generation, mission persistence across restart, accept/cancel/reroll rules, background mission generation, deployment selection, selected-fleet export and scoped report import, mission resolution and payout, portable config and automatic bridge installation, and round-trips of installed fleet files when available. Live game validation is a separate check; these tests use a synthetic fitting catalogue and, when present, the locally exported real catalogue. They do not simulate the Unity engine.

Earlier live validation on 16 October 2026 against NEBULOUS 0.6.2.6 confirmed the battle handoff and native report import with matching ship GUIDs; it did not verify damage carryover or a full combat engagement. On 20 October 2026, bridge 0.5 exported the real fitting catalogue, and a generated three-ship 3,000-point fleet loaded through the normal battle handoff and reached deployment against a current starter fleet without a point-cost discrepancy. The test was closed at deployment, so a completed generated-fleet battle is still a manual check.

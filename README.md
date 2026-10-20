# NBC Dynamic Scenario Generator

A Windows desktop proof of concept with a shared equipment inventory, native in-game fleet refitting, random opposing forces, local skirmishes and automatic battle-report import.

## Native refitting and inventory (bridge 0.3)

1. Close NEBULOUS and run **Install Bridge.ps1** to update the bridge. Keep **NBC Campaign Bridge** enabled in Mods. Enable the bridge once in the game; the app's Inventory tab also has **Connect / open NEBULOUS**.
2. Open **Start Campaign.cmd** in this project folder. Load/import a current fleet, or use **Create 3-ship test fleet** to create a disposable three-frigate roster. That action backs up the previous roster under `data/roster-backups`.
3. In **Inventory**, select equipment and a quantity. **Buy** adds spares, **Sell** removes spares. Store stock is unlimited. There is no currency, price, installation cost or shipyard requirement. Installed components cannot be sold directly.
4. Click **Refit fleet → NEBULOUS**. The app launches the game automatically and waits for the live bridge. From its main menu the bridge loads the entire campaign fleet into the normal game editor. The component palette shows available equipment and spare counts. Removing a part makes it available on other ships. Normal socket compatibility and design calculations remain in the game.
5. Use the overlay's **Review refit → Campaign** button to export a draft. After the app validates and saves the draft, NEBULOUS closes automatically. The app opens its Refit tab with a list of proposed component changes. Choose **Confirm refit**, **Continue / resume editor**, or **Cancel refit**. Continuing launches NEBULOUS again with the draft. Discarding a refit, either in game or in the app, closes its game session and preserves the approved fleet.

The game uses the normal editor, with additional inventory checks on installation, component paste and undo/redo. If a component removed from Ship A has been installed on Ship B, undoing the removal on A is blocked until another copy is available. Native per-ship history is retained; it is not replaced with a global undo stack. Whole-fitting paste, roster changes and normal save/load/test actions are blocked during campaign refits. Ordinary editor sessions and skirmishes do not use these restrictions.

Buying/selling and roster edits are locked while a battle or refit is pending. Purchases before a session survive cancellation. The original fleet and spare inventory remain approved until confirmation. Confirmation atomically updates both in `data/campaign.json`; `data/player.fleet` is a derived export. Restarting the app retains the pending session. **Continue / resume editor** restarts from the last exported draft, or from the session input if no draft was exported. Unsaved in-game changes are not recovered after a crash. Use the overlay to cancel in game, or cancel from the app.

This milestone tracks component **types and quantities**, not individual component condition. Ammunition, missile/craft construction and damage carryover are outside inventory tracking. Existing ammunition/design data remains native game data. The store catalogue comes from installed stock component assets; no prices or fitting statistics are reconstructed in Python. Imported fleets and the test-fleet generator are sandbox setup tools, not inventory-controlled ways to purchase hulls.

The runtime patches use vendored **Harmony 2.3.3**, net472 merged assembly, from `https://www.nuget.org/packages/Lib.Harmony/2.3.3`. Its MIT license is included in `bridge/lib/Harmony-LICENSE.txt`; no game libraries are distributed. Game updates can require adapting editor hooks and rebuilding the bridge.

## Run

Requirements: Windows, Python 3.10+ with Tkinter, NEBULOUS: Fleet Command, and Steam. Building the bridge also requires a .NET SDK. The Python app has no third-party dependencies.

1. Close NEBULOUS. In PowerShell, from this directory, run:

   ```powershell
   & '.\Install Bridge.ps1' -GamePath 'R:\SteamLibrary\steamapps\common\Nebulous'
   ```

2. Start NEBULOUS normally. Open **Mods**, enable **NBC Campaign Bridge**, apply the change, and return to the main menu. This is a one-time step. A running game must be restarted after updating the bridge DLL.
3. Double-click **Start Campaign.cmd**, or run `python app.py`.
4. On **Fleet**, load an installed fleet or import a `.fleet` file. Rename ships or change hull numbers. Use the native refit workflow above to change equipment. Original fleet files are preserved.
5. On **Battle**, choose an enemy faction and target point budget, then **Generate enemy**. The generator samples subsets of complete stock designs. The actual total may differ from the target.
6. Click **Run battle → NEBULOUS**. The game opens if necessary. If it is already running, return to its main menu. The bridge creates a local skirmish, assigns your fleet and one AI enemy, validates settings and launches it. Deploy your ships and play normally.
7. When the battle ends, **Debrief** fills automatically. Return to the external app to view the outcome, ship condition, casualties and damage statistics. NEBULOUS closes automatically after the app validates and saves the report, and the app brings the Debrief forward.

To use another game location:

```powershell
python app.py --game 'D:\SteamLibrary\steamapps\common\Nebulous'
```

## Scope of this prototype

- Native `.fleet` import/export, names, hull numbers, fleet composition, equipment substitutions and complete loadouts.
- Persistent local app saves and per-battle archives. Closing and reopening the app retains the pending battle and its eventual result.
- Random enemy composition from available stock fleet designs, preserving missile/craft definitions.
- Automatic handoff to a real local skirmish and automatic capture of a full native after-action report.
- Report records include a battle ID and player ship IDs; importing a completed battle twice is prevented.
- **Damage and casualties are recorded for review, but are not yet reapplied to ships in subsequent battles.** Ships currently launch with the condition described by ordinary fleet designs. Repairs, money, reputation and permanent losses are future campaign features.
- The native editor handles socket compatibility, power, magazines, faction restrictions and design warnings. The campaign bridge adds owned-component limits. Imported designs can contain cached point values until the game recalculates them.
- The game bridge currently uses the game's default map/scenario. Deployment and tactical play remain under player control.
- Use for local single-player testing. The app does not modify multiplayer sessions or automate combat.

The bridge was built against the local NEBULOUS 0.6.2.6 installation. Rebuild against your installed assemblies after a game update. It waits for the local host to gain authority over the AI slot before assigning its fleet, then checks that both selections were retained. It reads the game's private `_fullAAR` field because there is no verified external battle-report API; a game update may require adapting that access.

## Files and handoff

```text
app.py                       Native Tkinter UI
nbc/core.py                  XML editor, generator, campaign state and bridge exchange
nbc/refit.py                 Inventory, draft validation and atomic refit confirmation
nbc/runtime.py               Nonblocking startup, process identity and export acknowledgements
bridge/NBCBridge.cs          Code-only IModEntryPoint mod
bridge/RefitBridge.cs        Scoped native editor hooks and refit exchange
bridge/LifecycleBridge.cs    Live heartbeat and acknowledged graceful shutdown
bridge/lib/                  Pinned Harmony library and MIT license
bridge/ModInfo.xml           Local mod manifest
scripts/Build Bridge.ps1     Compile against installed game libraries
Install Bridge.ps1          Install only this mod's files
data/player.fleet           Working player fleet (ignored by Git)
data/enemy.fleet            Generated enemy (ignored by Git)
data/campaign.json          Battle history and last reported ship status
data/battles/<id>/           Input fleets, native report.xml and result.xml
data/refits/<id>/            Original fleet, revision inputs and exported drafts
docs/REFIT-VALIDATION.md      Automated and live refit checks
```

The app exports generated fleets into `NEBULOUS/Saves/Fleets/NBC-POC/`. The file exchange lives in `NEBULOUS/Saves/NBCBridge/`:

- `request.xml`: versioned request containing a GUID, absolute fleet paths, SHA-256 hashes and a result-directory path.
- `status.xml`: bridge progress or an actionable failure message, scoped to the battle ID.
- `cancel.xml`: explicit cancellation. Cancelling stops result tracking; it does not terminate a live game.
- `result.xml` in the archived battle folder: written atomically after `report.xml`, serving as the completion marker.

The bridge refuses modified input files after a request is submitted and rejects designs with missing component assets. Player and enemy inputs are archived before battle. Keep ship names unique within each fleet, because native reports identify ships by name; the bridge accounts for the player's fleet prefix (for example `ANS`) when mapping those names back to their fleet GUIDs. No game assemblies or extracted game assets are checked into this repository.

Refitting uses `refit-request.xml`, `refit-status.xml` and `refit-cancel.xml` in the same exchange directory. Each request has a session GUID, revision, hashed input and a shared supply equal to original installed components plus owned spares. `ready-<revision>.xml` is published after `draft-<revision>.fleet` and includes its hash and socket labels. The app validates the fleet against the original roster and reconciles component quantities again before confirmation. Only one app window may edit a given save directory.

For a separate fitting fixture without replacing the default save:

```powershell
python scripts/create_refit_demo.py --data data/my-refit-demo
python app.py --data data/my-refit-demo
```

The fixture generator refuses to overwrite an existing save. The live validation notes describe the separate test save already used on this machine.

## Game lifecycle

Refit and battle actions launch the configured game executable with the game directory as its working directory. The app waits up to three minutes for bridge 0.3 to reach the main menu before publishing a request. A fresh heartbeat must match the executable path, process ID and process start time; stale status files cannot satisfy startup. An existing game in another editor or battle is left alone until you return it to the main menu. An operation waiting for export acknowledgement, or a process shutting down, is allowed to finish before the next operation starts.

The bridge writes the draft/report before its completion marker. The app validates the export and durably records an acknowledgement in the campaign save before publishing `operation-ack.xml`. Only the matching game process, operation and revision can use it to quit normally. Failed saves/imports leave the game open; exchange-write failures retry on subsequent app polls and after an app restart. No process is force-killed. Cancelling battle tracking still leaves a live battle open. Cancelling a startup can close only the idle game launched by that request, using a unique launch token. **Connect / open NEBULOUS** refreshes the equipment catalogue and closes an idle game it launched itself.

`runtime-status.xml` contains the live process heartbeat; `runtime-command.xml` contains a launch-token-scoped idle shutdown request. Refits remain drafts until confirmed in the external app. Closing or crashing the game without exporting does not recover unsaved editor changes. If startup times out, check Steam and whether the bridge is enabled, then use **Continue / resume editor** for a refit, or cancel and relaunch a battle request.

## Troubleshooting

- **Bridge not connected:** check that NBC Campaign Bridge is enabled, apply changes, and return to the game main menu. Installation alone does not enable mods.
- **Fleet incompatible:** open and re-save an old design in the current game Fleet Editor, then import it again. Load any mods required by that fleet.
- **Bridge error:** inspect the status in the external app and `Player.log` under `%USERPROFILE%\AppData\LocalLow\Eridanus Industries\Nebulous`. Cancel the pending request in the app before retrying.
- **Game shows missing asset files:** launch from Steam or with the game directory as the working directory. The app does the latter automatically.
- **Game updated:** close it, rerun the bridge installer and restart.
- **Old Save Fleet State installed:** that older mod targets serialization interfaces removed in the installed game version. Do not enable it for this prototype's test.

## Checks

```powershell
python -m unittest discover -s tests -v
& '.\scripts\Build Bridge.ps1'
```

Tests cover XML preservation, ship identities, compatible/incompatible edits, enemy generation, battle locking, cancellation, result matching, duplicate import prevention and round-trips of installed fleet files when available. Live game validation is a separate check; these tests do not simulate the Unity engine.

Validated locally on 16 October 2026 against NEBULOUS 0.6.2.6: all 13 automated tests passed, and a real skirmish launched with the selected Axford heavy cruiser and generated Bulk Feeder enemy. Ending the disposable test by surrender produced a native report, which the running app imported automatically with both ship GUIDs matched to the archived fleets. This checks the battle handoff and report import; it does not verify damage carryover or a full combat engagement. The earlier diagnostic battle is retained in local history and contains results from before the fleet-selection and name-mapping fixes.

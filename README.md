# NBC Dynamic Scenario Generator

A Windows desktop proof of concept that edits native NEBULOUS fleets, generates an opposing force, launches a local skirmish through a small game mod, and imports the game's battle report.

## Run

Requirements: Windows, Python 3.10+ with Tkinter, NEBULOUS: Fleet Command, and Steam. Building the bridge also requires a .NET SDK. The Python app has no third-party dependencies.

1. Close NEBULOUS. In PowerShell, from this directory, run:

   ```powershell
   & '.\Install Bridge.ps1' -GamePath 'R:\SteamLibrary\steamapps\common\Nebulous'
   ```

2. Start NEBULOUS normally. Open **Mods**, enable **NBC Campaign Bridge**, apply the change, and return to the main menu. This is a one-time step. A running game must be restarted after updating the bridge DLL.
3. Double-click **Start Campaign.cmd**, or run `python app.py`.
4. On **Fleet**, load an installed fleet or import a `.fleet` file. Rename ships, change hull numbers, duplicate/remove ships, apply complete same-hull loadouts, or select an equipment row and click **Edit selected component**. The parts library comes from your existing fleets and includes only components seen in that exact hull socket. Import more fleets to expand it. Original fleet files are preserved.
5. On **Battle**, choose an enemy faction and target point budget, then **Generate enemy**. The generator samples subsets of complete stock designs. The actual total may differ from the target.
6. Click **Run battle → NEBULOUS**. The game opens if necessary. If it is already running, return to its main menu. The bridge creates a local skirmish, assigns your fleet and one AI enemy, validates settings and launches it. Deploy your ships and play normally.
7. When the battle ends, **Debrief** fills automatically. Return to the external app to view the outcome, ship condition, casualties and damage statistics. The game stays open; report capture does not close it or interrupt the end screen.

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
- The external editor does not know every game asset, power limit, magazine capacity or faction restriction. It offers parts from known socket-compatible designs and lets the game recalculate costs. Modified component combinations still need testing in the game. Displayed point values are estimates until then.
- The game bridge currently uses the game's default map/scenario. Deployment and tactical play remain under player control.
- Use for local single-player testing. The app does not modify multiplayer sessions or automate combat.

The bridge was built against the local NEBULOUS 0.6.2.6 installation. Rebuild against your installed assemblies after a game update. It waits for the local host to gain authority over the AI slot before assigning its fleet, then checks that both selections were retained. It reads the game's private `_fullAAR` field because there is no verified external battle-report API; a game update may require adapting that access.

## Files and handoff

```text
app.py                       Native Tkinter UI
nbc/core.py                  XML editor, generator, campaign state and bridge exchange
bridge/NBCBridge.cs          Code-only IModEntryPoint mod
bridge/ModInfo.xml           Local mod manifest
scripts/Build Bridge.ps1     Compile against installed game libraries
Install Bridge.ps1          Install only this mod's files
data/player.fleet           Working player fleet (ignored by Git)
data/enemy.fleet            Generated enemy (ignored by Git)
data/campaign.json          Battle history and last reported ship status
data/battles/<id>/           Input fleets, native report.xml and result.xml
```

The app exports generated fleets into `NEBULOUS/Saves/Fleets/NBC-POC/`. The file exchange lives in `NEBULOUS/Saves/NBCBridge/`:

- `request.xml`: versioned request containing a GUID, absolute fleet paths, SHA-256 hashes and a result-directory path.
- `status.xml`: bridge progress or an actionable failure message, scoped to the battle ID.
- `cancel.xml`: explicit cancellation. Cancelling stops result tracking; it does not terminate a live game.
- `result.xml` in the archived battle folder: written atomically after `report.xml`, serving as the completion marker.

The bridge refuses modified input files after a request is submitted and rejects designs with missing component assets. Player and enemy inputs are archived before battle. Keep ship names unique within each fleet, because native reports identify ships by name; the bridge accounts for the player's fleet prefix (for example `ANS`) when mapping those names back to their fleet GUIDs. No game assemblies or extracted game assets are checked into this repository.

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

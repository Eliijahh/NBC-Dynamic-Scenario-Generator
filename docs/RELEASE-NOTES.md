# NBC Campaign Command v0.1.0

A single-player campaign proof of concept for **NEBULOUS: Fleet Command**.

## Download

Download **NBC-Campaign-Command.exe** below and put it in a folder you can write to. It is portable: it keeps `config.json` and its `data/` folder (campaign saves) next to itself.

## First run

1. Run the exe. It finds NEBULOUS through Steam, copies its bundled bridge into `<game>\Mods\NBCBridge\`, and enables it in the game's active-mod list automatically.
2. Launch NEBULOUS once and return to its main menu so the bridge can export the fitting catalogue.
3. Run the exe again and start or continue a campaign. **Settings → NEBULOUS SETUP** shows the game folder, bridge version, whether it is enabled and whether the catalogue is present.

## What's included

- Credit-priced hulls and components, a shared spare inventory, repairs and per-component damage.
- Permanent ship loss: a destroyed ship leaves the roster and takes its installed components with it.
- Native in-game refitting for one or more selected hulls, with a review-and-confirm step.
- A mission board: generated offers with a type and map, an active-mission view, deployment selection, and mission completion with a credit reward.
- Local skirmish handoff with automatic after-action report import, scoped to the ships you deployed.

## Known limitations

- The build is **not code-signed**. Windows SmartScreen may warn on first run (**More info → Run anyway**), and some antivirus tools flag PyInstaller one-file builds as a false positive.
- Requires NEBULOUS: Fleet Command and Steam, on Windows.
- Direct-fire weapons and bulk-magazine ammunition only; no missiles, hangars or craft.
- Single-player only. It does not touch multiplayer sessions.
- The bridge is compiled against NEBULOUS 0.6.2.6; a game update may require a bridge rebuild.

## Verification

Compare the downloaded file against `NBC-Campaign-Command.exe.sha256` (SHA-256).

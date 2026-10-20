# Native refit proof of concept — 20 October 2026

Environment: Windows, Python 3.12, NEBULOUS 0.6.2.6:260903-1430, bridge 0.2.0.

## Automated checks

All 23 Python tests passed from the GitHub checkout. The bridge compiled against the installed game assemblies. `git diff --check` passed.

The new tests cover free buying/selling, inventory conservation, overspending rejection, roster/hull identity validation, cancellation, pending-session locking, continuation revisions, stale/tampered drafts, failed confirmation, and recovering an authoritative commit when the derived fleet export is interrupted. Existing fleet/battle tests also passed. These tests do not simulate Unity or replace the live checks below.

## Live native-editor checks

Used a separate save at `data/refit-live-test-2026-10-20`; the existing default campaign was not replaced. The fixture contains three current starter Raines frigates, essential systems and one Sarissa turret. It is a fitting fixture, not a combat-ready fleet.

1. Buy one Mk62 cannon in the app: spare quantity changed from 0 to 1. Sell it: 1 to 0. Buy again: 0 to 1. No currency exists.
2. Click **Refit fleet → NEBULOUS**: the bridge entered the native editor with all three ships.
3. Remove the Sarissa turret from Ship 1: its palette availability on Ship 2 became 1. Install it on Ship 2.
4. Attempt undo on Ship 1: blocked with an insufficient-spares explanation, preserving the removal and the transferred turret. Undo and redo on Ship 2 both worked.
5. Fit the purchased Mk62 cannon on Ship 3. A second empty mount no longer offered either weapon because both owned units were installed.
6. Use **Review refit → Campaign**: the game returned to its main menu and the external app came forward with three socket changes.
7. Use **Continue / resume editor**: revision 2 loaded the exported draft with the transfer and new cannon retained. Return again and confirm.
8. Restart the app: all three ship identities/configurations persisted; no spare cannon remained. Confirmed session: `93bad142-11dc-4d5b-bd2c-143406c62cc0`.
9. Open another refit, remove Ship 1's CIC, then cancel and discard through the overlay. The app cleared the pending refit, retained all three original CICs and preserved spare inventory.

The catalogue and palette use the game's localization parser. Empty palette categories are removed, and the campaign overlay is hidden while a component palette is open so it cannot cover native controls.

## Limits

Component types and quantities are tracked; individual condition, repairs, ammunition stock, missile/craft construction, economic prices and facilities are outside this milestone. Whole-fitting paste and roster changes are blocked in campaign sessions. Native undo remains per ship, with inventory validation before moving a history event. An interrupted editor can reopen its last exported draft/input; unsaved editor changes are not recovered. Changes have not been committed or pushed.


## Automatic lifecycle update — bridge 0.3.0

The Python suite now contains 41 passing tests. New coverage includes delayed publication until a live bridge is ready, exact process identity and stale/reused-PID rejection, existing-game reuse, unrelated editor preservation, shutdown/startup sequencing, startup timeout and early exit, launch-token cancellation, resuming the matching live editor, battle/refit acknowledgements after durable import, failed-save/discard rollback, and acknowledgement retry after restarting the app. Bridge 0.3 compiles against the installed game assemblies.

Live checks use the separate `data/lifecycle-test-2026-10-20` save; the default campaign remains unchanged:

1. With NEBULOUS closed, click the Fleet tab's refit button. The app launched the game, waited through asset loading and entered the native editor with all three ships.
2. Review the draft. The app durably reached review stage, came forward, and the matching game process exited normally. Refit session `6376616f-7093-4a71-8783-65592c575a0d`, revision 1.
3. Continue editing. A fresh game process loaded revision 2 with all three ships.
4. Cancel and discard using the in-game overlay. The app cleared the pending session and retained the approved fleet/inventory. NEBULOUS exited normally.

5. Generate a 780-point Protectorate enemy and run a battle with NEBULOUS closed. The game launched, configured the three-frigate player fleet against three enemies, and reached deployment. A disposable surrender produced the full native report and all six ship records; the app opened Debrief and NEBULOUS exited normally. Battle `30b554b4-accb-4789-ba60-9b8df6617d43`. This verifies completion/report handoff, not a full combat engagement.

The previous bridge 0.2 checks above remain historical evidence of inventory/editor behavior; their main-menu-only return behavior is superseded by acknowledged automatic shutdown.

# NBC Dynamic Scenario Generator

A Windows desktop proof of concept with a shared equipment inventory, credit-priced hulls and components, per-component battle damage, permanent ship loss and automatic battle-report import.

**Download:** grab the latest `NBC-Campaign-Command.exe` from the [Releases](https://github.com/Eliijahh/NBC-Dynamic-Scenario-Generator/releases/latest) page — see [Run](#run) for first-time setup.

## STATUS LEGEND

- `[x]` Done
- `[~]` In progress / partially working
- `[ ]` Pending
- `[!]` Blocked
- `[-]` Deferred / out of scope for this PoC

## LOCKED POC RULES AND DECISIONS

- [x] Single-player campaign only.
- [x] Enemy generation stays offline and uses the bridge-exported catalogue.
- [x] Bridge catalogue remains Format 1 at NEBULOUS/Saves/NBCBridge/fitting-catalog.xml.
- [x] v1 fitting scope: direct-fire weapons and bulk-magazine ammunition only.
- [x] No missile launchers, hangars or craft in v1.
- [x] Campaign uses separate credits, initially derived from native point costs.
- [x] Credit scale target: cheap components cost a few thousand; very expensive components can reach about one million; hulls range from about 100,000 to many millions.
- [x] Destroyed ships are permanently lost.
- [x] Installed components on destroyed ships are lost with the ship.
- [x] Any damage prevents a ship from launching until repaired.
- [x] No maintenance costs.
- [x] No bankruptcy or fail-state rescue system.
- [x] A dead campaign is allowed; no recovery mechanics are required for this PoC.
- [x] No deployment limits.
- [x] Mission flow: accept mission -> start mission -> select ships -> confirm -> only the selected ships appear in battle.

## 1. CAMPAIGN SHELL AND PERSISTENCE

### 1.1 New Campaign

- [x] Main menu with Continue, New Campaign, Load Campaign and Exit.
- [x] Continue appears only when a valid existing campaign is detected.
- [x] New Campaign is available only from the main menu.
- [x] New Campaign screen.
- [x] Campaign name and commander/faction setup.
- [x] Starting credits stored with the campaign (default value; not exposed in the setup popup).
- [x] Starting roster and starting loadouts.
- [x] Create a new campaign save from the setup screen.
- [x] Prevent accidental overwrite of an existing campaign.
- [x] Show a loading/progress state while the campaign is created.

### 1.2 Load Campaign

- [x] Load Campaign entry point on the main menu.
- [x] Load Campaign screen.
- [x] List saved campaigns with useful metadata.
- [x] Show campaign name, credits, fleet size and last played time.
- [x] Load a selected campaign.
- [x] Show a loading/progress state while the campaign loads.
- [x] Handle missing, corrupt or incompatible saves with a clear message.
- [x] Keep the existing root save loadable as a legacy campaign.
- [x] Track and open the last-played campaign from Continue.
- [x] Move the single-writer save lock when switching campaigns.
- [x] Automated coverage for campaign discovery, last-played tracking and corrupt saves.

### 1.3 Save data

- [x] Existing campaign/battle state persists across app restarts.
- [x] Extend the save schema with credits, owned hulls, ship condition, mission offers, accepted mission and campaign status.
- [x] Save after every economic or campaign-state transaction.
- [x] Atomic save writes.
- [x] Backup of the previous save.
- [x] Initial Settings tab with manual save, resume, save-and-exit and exit-without-saving actions for Campaign Command.
- [x] Schema version and migration handling.
- [x] Resume an accepted mission/deployment after an app restart.
- [x] Lock incompatible actions while a refit or battle is pending (an accepted mission does not lock the campaign).

## 2. ECONOMY AND CREDITS

- [x] Separate credits table decided.
- [x] Initial price scale agreed.
- [x] Credits wallet stored in the campaign save.
- [x] Integer-only credit amounts and consistent formatting.
- [x] Transaction ledger or audit trail for credits.
- [x] Atomic money changes for purchasing and selling.
- [x] Atomic money changes for repair.
- [x] Atomic money changes for rewards.
- [x] Guard against negative credits.
- [x] Price data file separate from native point costs.
- [x] Per-item override support in the price table.
- [x] Baseline formula that derives credits from native point costs.
- [x] Price every hull in the catalogue.
- [x] Price every component in the catalogue.
- [x] Fallback price for catalogue items added by a game update.
- [x] Validate that no hull or component is missing a price.
- [x] Purchases decrease player's money.
- [x] Ammunition does not cost credits for PoC
- [x] Selling returns 10% of hull or component value
- [x] Optional price-debug view showing native points and credits side by side added in debug view when the player uses SHIFT-X while in the settings screen, debug section appears and one of the buttons opens a screen showing two tabs "Hulls" and "Components" which then show the price and the point value one next to another

## 3. HULL AND COMPONENT OWNERSHIP

### 3.1 Existing inventory

- [x] Store component types and quantities.
- [x] Add and remove spare components.
- [x] Enforce owned-component limits during refits.
- [x] Keep installed components out of the sellable spares pool.
- [x] Lock buying/selling during a pending battle or refit.
- [x] Preserve approved inventory until refit confirmation.

### 3.2 Hull ownership

- [x] Track owned hulls separately from the exported fleet file.
- [x] Buy a hull and add it to the campaign roster.
- [x] Ability to first select which faction the hull should come from
- [x] Have a list of all available hulls for each faction that can be purchased
- [x] Assign a unique ship identity, name and hull number.
- [x] Ability to rename hulls
- [x] Hulls start empty with no components
- [x] Allow a purchased hull to be fitted through the native editor.
- [x] Player can have as many hulls as they want, as the number limitation of hulls will only come into play when selecting which hulls should be taken in a mission
- [x] Show owned hulls and their readiness in the roster UI.
- [x] Ability to sell a hull which removes all components and put them into the inventory

### 3.3 Component ownership

- [x] Buy components with credits.
- [x] Sell components for credits, if selling is kept in scope.
- [x] Show owned quantity and cost in the store.
- [x] Show whether the player can afford the selected quantity.
- [x] Add purchased components to spare inventory atomically with the credit debit.

### 3.4 Store / shipyard

- [x] Purchase screen for hulls and components.
- [x] Hull-type filter.
- [x] Component-type filter.
- [x] Search or name filter.
- [x] Show native points and credit cost.
- [x] Show owned quantity and affordability.
- [x] Confirm purchase before spending credits.
- [x] Purchase success and failure messages.
- [x] Prevent purchases during missions, battles or pending refits.
- [x] Refresh the catalogue after a game/bridge update.

## 4. DAMAGE, REPAIR AND PERMANENT LOSS

### 4.1 Battle-report state

- [x] Native report captures ship condition, casualties and damage statistics
- [x] Persist per-ship condition into the campaign roster
- [x] Mark a ship as ready or damaged
- [x] A completely destroyed ship happens when the reactor completely explodes in the battle
- [x] A completely destroyed ship should be removed from the fleet list
- [x] Keep unselected ships completely unaffected by a mission report
- [x] Component that have their HP be equal to 0 should be flagged as destroyed, and must be removed and scrapped and cannot be repaired

### 4.2 Repair

- [x] Any component damage above zero or a destroyed component blocks mission deployment
- [x] Repair action for an individual ship
- [x] Repair all action
- [x] Repair screen or repair panel
- [x] Show each damaged component's damage percentage and the repair cost
- [x] Show a weighted damage percentage before the Damaged status on the fleet list
- [x] Repairing a damaged component should cost less than buying a new one, so the repair cost formula should account for that
- [x] Repair is instant for PoC
- [x] Repair consumes credits, a percentage of total component cost based on the percentage of damage of that component
- [x] Revalidate that the ship is ready before confirming deployment.
- [x] Show damaged ships as unavailable on the deployment screen.

### 4.3 Permanent loss

- [x] A destroyed ship is removed from the roster permanently.
- [x] Installed components on a destroyed ship are removed from owned inventory.
- [x] Reconcile the roster and inventory in the same atomic save as the battle result.
- [x] Show destroyed ships and lost components in the debrief.
- [x] Ensure a lost ship cannot appear in a later mission.

## 5. MISSIONS

### 5.1 Mission generation

- [x] Offline fleet-vs-fleet enemy ship generation.
- [x] Mission data model: ID, name, enemy fleet, reward, status and timestamps.
- [x] Mission list generated by mission logic rather than direct random battle setup.
- [x] Fleet-vs-fleet missions with a credit reward.
- [x] Generate the enemy fleet in the background.
- [x] Show generation progress and retry on failure.
- [x] Persist generated mission offers in the campaign save.
- [x] Avoid duplicate or stale mission entries after restart.
- [x] Define mission difficulty and enemy strength relative to the player's fleet.
- [x] Define the mission reward formula.
- [x] Define mission reroll rules.
- [x] Define mission expiry rules, if any.
- [x] Define whether canceling an accepted mission has a penalty.
- [x] Generate a random batch of 5–10 offers on campaign start and on every regeneration.
- [x] Roll the enemy faction independently of the player's faction.
- [x] Store mission type and map (annihilation; Abyssal (4p) / The Cliff (4p)).

### 5.2 Mission board

- [x] Mission board screen.
- [x] Show mission name, enemy faction and enemy fleet composition.
- [x] Show enemy hulls and total points.
- [x] Show the credit reward.
- [x] Show mission type and map in the mission details.
- [x] Show mission state: offered, accepted, deploying, active, completed, failed.
- [x] Accept mission action.
- [x] Cancel accepted mission action.
- [x] Regenerate mission list action.
- [x] Disable regeneration while a mission is accepted.
- [x] Loading/progress state while offers or enemy fleets are generated.
- [x] Clear failure messages if generation fails.
- [x] Home screen summary of the active mission.

### 5.3 Mission acceptance

- [x] Accepting a mission stores a fixed snapshot of the enemy fleet.
- [x] Accepting a mission removes it from the normal offer pool.
- [x] Accepted mission survives app restart.
- [x] Start Mission action appears after acceptance.
- [x] Cancel accepted mission returns the player to the mission board.
- [x] Cancel accepted mission does not award credits.
- [x] Player refits, repairs, purchases and roster edits remain allowed after acceptance.

### 5.4 Deployment / ship selection

- [x] Start Mission opens a separate deployment screen.
- [x] Show the accepted enemy fleet on the deployment screen.
- [x] Show the player's full roster and readiness.
- [x] Allow selecting a subset of ready ships.
- [x] Prevent selecting damaged or lost ships.
- [x] Require at least one ready ship before confirmation.
- [x] Show selected ship count.
- [x] Show selected fleet points for information.
- [x] No deployment point or ship limit for this PoC (the native skirmish still accepts at most 10 ships).
- [x] Confirm selection action.
- [x] Back/cancel action returns to the mission without launching.
- [x] Confirm builds a temporary selected player fleet.
- [x] Confirm is blocked if any selected ship is damaged.
- [x] Confirm is blocked if no ships are selected.
- [x] Only selected ships are exported to the battle.
- [x] Unselected ships do not appear in the battle.
- [x] Unselected ships are not modified by the battle report.
- [x] Preserve selected ship IDs for report mapping.
- [x] Start NEBULOUS only after the player confirms deployment.

### 5.5 Mission completion

- [x] Read the native battle outcome automatically.
- [x] Determine mission won, lost, drawn or surrendered.
- [x] Mark the mission completed or failed.
- [x] Award the correct credit reward exactly once.
- [x] Apply damage and permanent losses only to selected ships.
- [x] Remove destroyed ships and their installed components.
- [x] Record the mission in battle history.
- [x] Record the payout in the transaction ledger.
- [x] Clear the active mission after resolution.
- [x] Make completion idempotent if the report is imported more than once.
- [x] Define payout for a loss, draw, surrender or retreat (victory pays; anything else pays nothing).
- [x] Show the payout and consequences in the debrief.

## 6. BATTLE HANDOFF AND REPORT INTEGRATION

- [x] Versioned bridge request/result exchange.
- [x] Whole-fleet battle handoff to a local skirmish.
- [x] Native report import and result validation.
- [x] Duplicate battle import prevention.
- [x] Battle ID and player ship ID matching.
- [x] Change battle preparation from the whole fleet to a selected fleet subset.
- [x] Archive only the selected player fleet with the battle.
- [x] Scope report ship matching to the selected ship IDs.
- [x] Apply report consequences only to selected ships.
- [x] Verify that unselected ships and their components are untouched.
- [x] Select the mission's map and scenario in the in-game skirmish lobby.

## 8. INSTALLER AND DISTRIBUTION

- [x] Manual Install Bridge.ps1 copies the bridge files.
- [x] Runtime waits for the bridge and rejects versions below 0.5.0.
- [x] Real catalogue export verified against NEBULOUS 0.6.2.6.
- [x] First-run installer logic that copies the bridge automatically.
- [x] First-run game-path selection.
- [x] Launch-time check for NEBULOUS installation.
- [x] Launch-time check for bridge installation.
- [x] Launch-time check for bridge version.
- [x] Launch-time check for the fitted catalogue.
- [x] Prompt to select the game path when it is missing.
- [x] Prompt to install, update or repair the bridge.
- [x] Show whether the bridge mod is enabled.
- [x] Bundle bridge files so users do not need the .NET SDK.
- [x] Keep bridge installation idempotent.
- [x] Verify write permissions before installing.
- [x] Portable data folder next to the executable (user-selected), with config written beside it.
- [x] Package a portable executable.
- [x] Update README installation and first-run instructions.

## 12. POST POC

- [ ] Detect that the campaign has no usable ships and cannot recover.
- [ ] Show a clear campaign-over state.
- [ ] Offer New Campaign / Load Campaign from that state.
- [ ] Add automated campaign-over detection coverage.
- [ ] Live-check a dead campaign and the campaign-over screen.
- [ ] Missile generation
- [ ] Ammunition costs

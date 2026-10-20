// Compiled against the user's installed game assemblies; no proprietary files distributed.
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Xml;
using Bundles;
using Game;
using Game.Reports;
using Modding;
using Networking;
using Ships;
using UnityEngine;
using UnityEngine.SceneManagement;
using Utility;

namespace NBCBridge
{
    public sealed class EntryPoint : IModEntryPoint
    {
        public void PreLoad() { }
        public void PostLoad()
        {
            if (UnityEngine.Object.FindObjectOfType<BattleBridge>() != null) return;
            var obj = new GameObject("NBC Campaign Bridge");
            UnityEngine.Object.DontDestroyOnLoad(obj);
            obj.AddComponent<LifecycleBridge>();
            obj.AddComponent<BattleBridge>();
            obj.AddComponent<RefitBridge>();
            Debug.Log("[NBC] Campaign bridge loaded.");
        }
    }

    public sealed class BattleBridge : MonoBehaviour
    {
        private readonly string exchange = Path.GetFullPath(Path.Combine("Saves", "NBCBridge"));
        private XmlDocument request;
        private string id = "", output = "", stage = "idle", lastMessage = "";
        private float nextPoll, started, configured;
        private bool launched, enteredBattle;
        private Fleet.FleetSummary playerFleet, enemyFleet;
        private static readonly FieldInfo ReportField = typeof(SkirmishGameManager).GetField("_fullAAR", BindingFlags.NonPublic | BindingFlags.Instance);

        private void Start()
        {
            Directory.CreateDirectory(exchange);
            Status("Ready for a campaign battle request.");
        }

        private static XmlDocument Read(string path)
        {
            var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null };
            var doc = new XmlDocument { XmlResolver = null };
            using (var reader = XmlReader.Create(path, settings)) doc.Load(reader);
            return doc;
        }

        private string Value(string name) { return request.DocumentElement[name]?.InnerText ?? ""; }

        private static string Hash(string path)
        {
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static void AtomicWrite(string path, XmlDocument doc)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temp = path + ".tmp";
            doc.Save(temp);
            if (File.Exists(path)) File.Replace(temp, path, null);
            else File.Move(temp, path);
        }

        private static void Add(XmlNode parent, string name, object value)
        {
            var el = parent.OwnerDocument.CreateElement(name);
            el.InnerText = Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
            parent.AppendChild(el);
        }

        private void Status(string message)
        {
            if (message == lastMessage) return;
            lastMessage = message;
            var doc = new XmlDocument();
            var root = doc.CreateElement("BridgeStatus"); doc.AppendChild(root);
            Add(root, "Id", id); Add(root, "Stage", stage); Add(root, "Message", message);
            Add(root, "Utc", DateTime.UtcNow.ToString("o"));
            AtomicWrite(Path.Combine(exchange, "status.xml"), doc);
            Debug.Log("[NBC] " + message);
        }

        private void Update()
        {
            if (Time.realtimeSinceStartup < nextPoll) return;
            nextPoll = Time.realtimeSinceStartup + 0.5f;
            try { Tick(); }
            catch (Exception ex)
            {
                stage = "error";
                Status("Bridge error: " + ex.Message + ". Cancel the request in Campaign Command before retrying.");
                Debug.LogError("[NBC] " + ex);
            }
        }

        private void Tick()
        {
            if (request != null && File.Exists(Path.Combine(exchange, "cancel.xml")))
            {
                var cancel = Read(Path.Combine(exchange, "cancel.xml"));
                if (cancel.DocumentElement["Id"]?.InnerText == id)
                {
                    string oldId = id;
                    if (File.Exists(Path.Combine(exchange, "request.xml")) && Read(Path.Combine(exchange, "request.xml")).DocumentElement["Id"]?.InnerText == oldId)
                        File.Delete(Path.Combine(exchange, "request.xml"));
                    request = null; stage = "idle"; LifecycleBridge.Instance.Idle();
                    Status("Campaign request cancelled. The current game remains open.");
                    return;
                }
            }
            if (request == null)
            {
                string path = Path.Combine(exchange, "request.xml");
                if (!File.Exists(path)) return;
                var incoming = Read(path);
                string incomingId = incoming.DocumentElement["Id"]?.InnerText;
                Guid parsed;
                if (!Guid.TryParse(incomingId, out parsed)) throw new InvalidDataException("Invalid battle ID");
                if (File.Exists(Path.Combine(exchange, "cancel.xml")) && Read(Path.Combine(exchange, "cancel.xml")).DocumentElement["Id"]?.InnerText == incomingId) return;
                if (!LifecycleBridge.Instance.Accept(incoming.DocumentElement)) return;
                request = incoming; id = incomingId; output = Value("ResultDirectory");
                if (!Path.IsPathRooted(output) || !Directory.Exists(output)) throw new InvalidDataException("Result directory does not exist");
                if (File.Exists(Path.Combine(output, "result.xml"))) { Finish(); return; }
                if (Hash(Value("PlayerFleet")) != Value("PlayerHash") || Hash(Value("EnemyFleet")) != Value("EnemyHash"))
                    throw new InvalidDataException("Fleet changed after battle request was created");
                started = Time.realtimeSinceStartup;
                stage = "waiting-menu"; launched = enteredBattle = false;
                Status("Request received. Waiting for the NEBULOUS main menu.");
            }
            if (stage == "error") return;
            if (stage == "waiting-menu")
            {
                if (UnityEngine.Object.FindObjectOfType<UI.MainMenu>() == null)
                {
                    if (Time.realtimeSinceStartup - started > 15) Status("Return to the main menu to start the requested campaign battle.");
                    return;
                }
                playerFleet = Fleet.GetSummaryForFleet(new FilePath(Value("PlayerFleet")));
                enemyFleet = Fleet.GetSummaryForFleet(new FilePath(Value("EnemyFleet")));
                if (playerFleet == null || enemyFleet == null || !playerFleet.ValidForUse || !enemyFleet.ValidForUse)
                    throw new InvalidDataException("A fleet is incompatible with this game version");
                if (!playerFleet.AllModDependenciesLoaded || !enemyFleet.AllModDependenciesLoaded)
                    throw new InvalidDataException("A fleet requires mods that are not loaded");
                foreach (var fleet in new[] { playerFleet, enemyFleet })
                foreach (var ship in fleet.Fleet.Ships)
                foreach (var socket in ship.SocketMap)
                    if (!string.IsNullOrEmpty(socket.ComponentName) && BundleManager.Instance.GetHullComponent(socket.ComponentName) == null)
                        throw new InvalidDataException("Fleet contains a removed or missing component: " + socket.ComponentName + ". Re-save the design in the current game's Fleet Editor");
                CrossScenePipe.SetData(new HostSkirmishLobbyData { LastScene = SceneRegistry.MainMenu, IsSinglePlayer = true });
                stage = "lobby"; started = Time.realtimeSinceStartup;
                Status("Creating local skirmish lobby…");
                SceneManager.LoadScene((int)SceneRegistry.SkirmishLobby, LoadSceneMode.Single);
                return;
            }
            if (stage == "lobby")
            {
                var lobby = GameManager.Instance as SkirmishLobbyManager;
                if (lobby == null || !lobby.IsHost || lobby.LobbyLocalPlayer == null)
                {
                    if (Time.realtimeSinceStartup - started > 120) throw new TimeoutException("Timed out creating the skirmish lobby");
                    return;
                }
                // The vanilla local lobby creates an AI slot asynchronously. Reuse it.
                var bot = lobby.Players.OfType<SkirmishLobbyPlayer>().FirstOrDefault(p => p.IsBot);
                if (bot == null)
                {
                    if (Time.realtimeSinceStartup - started > 120) throw new TimeoutException("Default AI slot was not created");
                    return;
                }
                var local = lobby.LobbyLocalPlayer;
                // Mirror assigns the local host authority over AI slots after spawning.
                // Assigning sooner is rejected, and OnStartAuthority then restores a default fleet.
                if (!local.hasAuthority || !bot.hasAuthority) return;
                local.MovePlayerToTeam(TeamIdentifier.TeamA);
                local.SetSelectedFleet(playerFleet);
                bot.MovePlayerToTeam(TeamIdentifier.TeamB);
                bot.SetSelectedFleet(enemyFleet);
                // Retain the game's known-compatible default map and scenario.
                configured = Time.realtimeSinceStartup;
                stage = "configured";
                Status("Both fleets assigned. Validating skirmish settings…");
                return;
            }
            if (stage == "configured")
            {
                var lobby = GameManager.Instance as SkirmishLobbyManager;
                if (lobby == null) throw new InvalidOperationException("Lobby closed before launch");
                if (Time.realtimeSinceStartup - configured < 3) return;
                var enemy = lobby.Players.OfType<SkirmishLobbyPlayer>().Single(p => p.IsBot);
                if (enemy.SelectedFleet == null || enemy.SelectedFleet.Fleet.Name != enemyFleet.Fleet.Name ||
                    enemy.SelectedFleetFactionKey != enemyFleet.FactionKey ||
                    lobby.LobbyLocalPlayer.SelectedFleet == null || lobby.LobbyLocalPlayer.SelectedFleet.Fleet.Name != playerFleet.Fleet.Name)
                    throw new InvalidOperationException("The game did not retain the requested fleet assignments");
                if (!lobby.AllPlayersValid() || !lobby.AllSettingsValid())
                {
                    if (Time.realtimeSinceStartup - configured > 45) throw new InvalidOperationException("Game rejected the fleet or lobby settings. Inspect the skirmish lobby");
                    return;
                }
                lobby.LobbyLocalPlayer.SetReadyState(true);
                // Normal local lobby launches on ready; only launch explicitly if no transition began.
                if (!lobby.SceneSwitchInProgress && !launched)
                {
                    launched = true;
                    lobby.LaunchGame();
                }
                stage = "battle";
                started = Time.realtimeSinceStartup;
                Status("Battle loading. Deploy your fleet and play; results will return automatically.");
                return;
            }
            if (stage == "battle")
            {
                var game = SkirmishGameManager.Instance;
                if (game == null)
                {
                    if (enteredBattle || Time.realtimeSinceStartup - started > 180)
                        throw new InvalidOperationException("Battle closed without a completed report");
                    return;
                }
                enteredBattle = true;
                if (game.CurrentGameState == SkirmishGameState.Error) throw new InvalidOperationException("NEBULOUS reported a battle loading error");
                if (game.CurrentGameState != SkirmishGameState.Finished)
                {
                    Status("NEBULOUS: " + game.CurrentGameState + ". Waiting for battle completion.");
                    return;
                }
                var report = ReportField?.GetValue(game) as FullAfterActionReport;
                if (report == null || !report.GameFinished) return;
                Export(report);
                Finish();
            }
        }

        private void Export(FullAfterActionReport report)
        {
            // Store the full native report before publishing the result completion marker.
            File.WriteAllBytes(Path.Combine(output, "report.xml.tmp"), report.SerializeToBuffer());
            File.Move(Path.Combine(output, "report.xml.tmp"), Path.Combine(output, "report.xml"));
            var doc = new XmlDocument();
            var root = doc.CreateElement("BattleResult"); doc.AppendChild(root);
            Add(root, "Id", id); Add(root, "RuntimeInstance", LifecycleBridge.Instance.Identity);
            Add(root, "Outcome", report.WinningTeam == TeamIdentifier.None ? "Draw" : report.LocalPlayerWon ? "Victory" : "Defeat");
            Add(root, "Duration", report.GameDuration); Add(root, "WinningTeam", report.WinningTeam);
            Add(root, "CompletedUtc", DateTime.UtcNow.ToString("o"));
            var ships = doc.CreateElement("Ships"); root.AppendChild(ships);
            foreach (var team in report.Teams)
            foreach (var player in team.Players)
            foreach (var ship in player.Ships)
            {
                bool friendly = player.IsLocalPlayer;
                var fleet = friendly ? playerFleet : enemyFleet;
                var original = fleet.Fleet.Ships.FirstOrDefault(s => ship.ShipName == s.Name ||
                    ship.ShipName == (player.Colors.FleetPrefix + " " + s.Name));
                if (original == null) throw new InvalidDataException("Report ship did not match the submitted fleet: " + ship.ShipName);
                var node = doc.CreateElement("Ship"); ships.AppendChild(node);
                Add(node, "Key", original == null ? "" : original.Key.ToString());
                Add(node, "Side", friendly ? "Player" : "Enemy");
                Add(node, "Name", ship.ShipName); Add(node, "Hull", ship.HullKey);
                Add(node, "Status", ship.Eliminated.ToString()); Add(node, "Condition", ship.Condition);
                Add(node, "OriginalCrew", ship.OriginalCrew); Add(node, "FinalCrew", ship.FinalCrew);
                Add(node, "DamageDealt", ship.TotalDamageDealt); Add(node, "DamageReceived", ship.TotalDamageReceived);
                Add(node, "DamageRepaired", ship.TotalDamageRepaired);
                Add(node, "AmmoExpended", ship.AmmoPercentageExpended.HasValue ? ship.AmmoPercentageExpended.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) : "");
            }
            AtomicWrite(Path.Combine(output, "result.xml"), doc);
        }

        private void Finish()
        {
            LifecycleBridge.Instance.Await(request.DocumentElement, "battle");
            stage = "completed";
            Status("Battle exported. Return to Campaign Command to view the debrief.");
            string path = Path.Combine(exchange, "request.xml");
            if (File.Exists(path) && Read(path).DocumentElement["Id"]?.InnerText == id) File.Delete(path);
            request = null;
        }
    }
}

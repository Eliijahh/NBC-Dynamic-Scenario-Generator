using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Xml;
using Bundles;
using FleetEditor;
using Game;
using Munitions;
using Ships;
using Ships.Controls;
using Ships.Serialization;
using UI;
using UnityEngine;
using UnityEngine.SceneManagement;
using Utility;

namespace NBCBridge
{
    // Designs start with bare hulls. Every installed socket component is rolled here.
    public sealed class GenerationBridge : MonoBehaviour
    {
        private readonly string exchange = Path.GetFullPath(Path.Combine("Saves", "NBCBridge"));
        private XmlDocument request;
        private string id, folder, stage = "idle", seen = "";
        private System.Random rng;
        private FleetEditorController editor;
        private SerializedFleet pool;
        private List<BaseHull> hulls;
        private List<HullComponent> parts;
        private int attempts, budget;
        private float next, started;
        private Ship candidate;
        private bool candidateProcessed;

        private XmlDocument Read(string path)
        {
            var doc = new XmlDocument { XmlResolver = null };
            using (var reader = XmlReader.Create(path, new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null })) doc.Load(reader);
            return doc;
        }
        private void Add(XmlElement node, string key, object value)
        {
            var child = node.OwnerDocument.CreateElement(key); child.InnerText = Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture); node.AppendChild(child);
        }
        private void Write(string path, XmlDocument doc)
        {
            doc.Save(path + ".tmp");
            if (File.Exists(path)) File.Replace(path + ".tmp", path, null); else File.Move(path + ".tmp", path);
        }
        private void Status(string message)
        {
            var doc = new XmlDocument(); var root = doc.CreateElement("GenerationStatus"); doc.AppendChild(root);
            Add(root, "Id", id ?? ""); Add(root, "Stage", stage); Add(root, "Message", message);
            Write(Path.Combine(exchange, "generation-status.xml"), doc);
            Debug.Log("[NBC Generation] " + message);
        }

        private void Update()
        {
            if (Time.realtimeSinceStartup < next) return;
            next = Time.realtimeSinceStartup + 0.1f;
            try { Tick(); }
            catch (Exception ex) { stage = "error"; Status("Generation failed: " + ex.Message + ". Cancel the request in the app to return."); Debug.LogError(ex); }
        }

        private void Tick()
        {
            if (request != null)
            {
                string cancel = Path.Combine(exchange, "generation-cancel.xml");
                if (File.Exists(cancel) && Read(cancel).DocumentElement["Id"]?.InnerText == id) { Finish(); return; }
            }
            if (request == null)
            {
                string path = Path.Combine(exchange, "generation-request.xml");
                if (!File.Exists(path)) return;
                var incoming = Read(path); var root = incoming.DocumentElement;
                if (root["Id"]?.InnerText == seen) return;
                Guid parsed;
                if (root.Name != "GenerationRequest" || !Guid.TryParse(root["Id"]?.InnerText, out parsed)) throw new InvalidDataException("Invalid generation request");
                if (!LifecycleBridge.Instance.Accept(root)) return;
                request = incoming; id = root["Id"].InnerText; seen = id; folder = root["Directory"].InnerText;
                budget = int.Parse(root["Budget"].InnerText); rng = new System.Random(int.Parse(root["Seed"].InnerText));
                if (!Path.IsPathRooted(folder) || !Directory.Exists(folder) || budget < 1 || budget > 30000) throw new InvalidDataException("Invalid generation settings");
                pool = Fleet.ReadXMLFleet(new FilePath(Path.Combine(folder, "library.fleet")));
                var faction = BundleManager.Instance.GetFaction(root["Faction"].InnerText);
                if (faction == null || pool.FactionKey != faction.SaveKey) throw new InvalidDataException("Unknown generation faction");
                hulls = BundleManager.Instance.GetFactionHulls(faction).Where(h => h.SaveKey.StartsWith("Stock/") && h.PointCost <= Math.Max(150, budget)).OrderBy(h => h.SaveKey).ToList();
                if (hulls.Count == 0) throw new InvalidDataException("No usable hulls at this budget");
                parts = BundleManager.Instance.AllComponents.Where(c => c.SaveKey.StartsWith("Stock/")).OrderBy(c => c.SaveKey).ToList();
                var bootstrap = new SerializedFleet { Name = "NBC Random Fitting Workshop", Version = FleetVersioning.EditorVersion,
                    FactionKey = faction.SaveKey, MissileTypes = pool.MissileTypes, CraftTypes = pool.CraftTypes };
                var workspaceHull = hulls.OrderBy(h => h.PointCost).First();
                var workspaceShip = new SerializedShip { Key = Guid.NewGuid(), Name = "Generation workspace", Number = 1, HullType = workspaceHull.SaveKey };
                foreach (var socket in workspaceHull.AllSockets.Where(s => !string.IsNullOrEmpty(s.DefaultComponent)))
                    workspaceShip.SocketMap.Add(new SerializedHullSocket { Key = socket.Key, ComponentName = socket.DefaultComponent });
                bootstrap.Ships.Add(workspaceShip);
                Write(Path.Combine(folder, "workspace.fleet"), bootstrap.CreateXmlDocumentWithRoot());
                pool.Name = "NBC Generated Candidates"; pool.Version = FleetVersioning.EditorVersion; pool.Ships.Clear();
                stage = "waiting-menu"; started = Time.realtimeSinceStartup; attempts = 0;
            }
            if (stage == "error") return;
            if (Time.realtimeSinceStartup - started > 240) throw new TimeoutException("Random fitting exceeded four minutes");
            if (stage == "waiting-menu")
            {
                var menu = UnityEngine.Object.FindObjectOfType<global::UI.MainMenu>();
                if (menu == null) return;
                var summary = Fleet.GetSummaryForFleet(new FilePath(Path.Combine(folder, "workspace.fleet")));
                CrossScenePipe.SetData(new EditFleetData { Fleet = summary });
                stage = "loading"; menu.ButtonFleetEditor(); Status("Opening the native fitting workshop…"); return;
            }
            if (stage == "loading")
            {
                editor = UnityEngine.Object.FindObjectOfType<FleetEditorController>();
                if (editor == null || editor.Fleet == null || editor.EditorControllers.Count != 1) return;
                MenuController.Instance.OpenMenu<ModalWorking>("Working").Set(null);
                stage = "building";
            }
            if (stage == "building")
            {
                if (candidate == null)
                {
                    attempts++;
                    candidate = editor.CreateNewShip(hulls[rng.Next(hulls.Count)]);
                    candidate.SetIdentity("Candidate " + attempts, attempts + 100);
                    candidateProcessed = false;
                    SingletonMonobehaviour<FastNameplateBaker>.Instance.BakeAll();
                    next = Time.realtimeSinceStartup + 0.5f;
                    return;
                }
                if (NameplateBaker.Instance.ActiveJobs) return;
                Ship ship = candidate;
                if (!candidateProcessed)
                {
                try
                {
                    foreach (var socket in ship.Hull.AllSockets) ship.SetSocketComponent(socket, null);
                    if (Fit(ship))
                    {
                        var saved = ship.GetSerializable(includeOtherTemplates: true, includeEmpty: false);
                        pool.Ships.Add(saved);
                        foreach (var missile in saved.TemplateMissileTypes)
                        { pool.MissileTypes.RemoveAll(m => m.SaveKey == missile.SaveKey); pool.MissileTypes.Add(missile); }
                        foreach (var craft in saved.TemplateSpacecraftTypes)
                        { pool.CraftTypes.RemoveAll(c => c.SaveKey == craft.SaveKey); pool.CraftTypes.Add(craft); }
                        saved.TemplateMissileTypes.Clear(); saved.TemplateSpacecraftTypes.Clear();
                    }
                }
                catch (InvalidOperationException ex) { Debug.LogWarning("[NBC Generation] Rejected fitting: " + ex.Message); }
                candidateProcessed = true;
                next = Time.realtimeSinceStartup + 0.5f;
                return;
                }
                ship.Hull.RemoveAllPaintableSegmentsFromBaker();
                editor.DeleteShip(ship.GetComponent<EditorShipController>());
                candidate = null;
                Status("Building random ships: " + pool.Ships.Count + "/20 valid designs (" + attempts + " fitting attempts)…");
                if (pool.Ships.Count >= 20 || attempts >= 240)
                {
                    if (pool.Ships.Count == 0) throw new InvalidDataException("No combat-capable random fittings found");
                    string path = Path.Combine(folder, "candidates.fleet");
                    Write(path, pool.CreateXmlDocumentWithRoot());
                    string hash;
                    using (var sha = SHA256.Create()) using (var input = File.OpenRead(path)) hash = BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
                    var doc = new XmlDocument(); var root = doc.CreateElement("GenerationReady"); doc.AppendChild(root);
                    Add(root, "Id", id); Add(root, "RuntimeInstance", LifecycleBridge.Instance.Identity); Add(root, "Hash", hash);
                    Add(root, "Attempts", attempts); Add(root, "Candidates", pool.Ships.Count);
                    Write(Path.Combine(folder, "ready.xml"), doc); Finish();
                }
            }
        }

        private IEnumerable<HullComponent> Fits(Ship ship, HullSocket socket) => parts.Where(c => c.Type == socket.Type && c.TestSocketFit(socket.Size) && ship.Hull.CanMountEquipment(c));

        private bool Install(Ship ship, Func<HullComponent, bool> role, HashSet<string> essential)
        {
            var options = ship.Hull.AllSockets.Where(s => s.Component == null)
                .SelectMany(s => Fits(ship, s).Where(role).Select(c => new KeyValuePair<HullSocket, HullComponent>(s, c))).ToList();
            if (options.Count == 0) return false;
            var option = options[rng.Next(options.Count)]; ship.SetSocketComponent(option.Key, option.Value); essential.Add(option.Key.Key); return true;
        }

        private bool Fit(Ship ship)
        {
            var essential = new HashSet<string>();
            if (!Install(ship, c => c is DriveComponent, essential) ||
                !Install(ship, c => c is PowerplantComponent, essential) ||
                !Install(ship, c => c is CommandComponent command && command.CommandAbility == CommandFunctions.All, essential)) return false;
            Install(ship, c => c is BaseActiveSensorComponent, essential);
            Install(ship, c => c is BulkMagazineComponent, essential);
            if (!Install(ship, c => c is IWeapon, essential)) return false;
            var optional = ship.Hull.AllSockets.Where(s => s.Component == null).OrderBy(s => rng.Next()).ToList();
            foreach (var socket in optional)
            {
                var choices = Fits(ship, socket).ToList();
                if (choices.Count > 0 && rng.NextDouble() < 0.9) ship.SetSocketComponent(socket, choices[rng.Next(choices.Count)]);
            }
            LoadStores(ship);
            ship.EditorRecalcCrewAndResources();
            // Remove optional systems until essential systems have enough crew and power.
            foreach (var socket in optional.OrderBy(s => rng.Next()))
            {
                if (ResourcesOkay(ship)) break;
                if (socket.Component != null) { ship.SetSocketComponent(socket, null); ship.EditorRecalcCrewAndResources(); }
            }
            if (!ResourcesOkay(ship)) return false;
            ship.RebuildAmmoFeeder(); ship.LoadAllExternallyFedWeapons(); ship.BuildUngroupedWeaponGroups(); ship.EditorCollectDesignWarnings();
            if (ship.DesignWarnings.Any(w => w.Severity == WarningType.Critical)) return false;
            var weapons = ship.Hull.CollectComponents<IWeapon>().Where(w => w.Role.IsOffensive()).ToList();
            if (!weapons.Any(w => !RequiresAmmo(w) || ship.Fleet.AvailableMunitions.AllMunitions.Any(a => w.IsAmmoCompatible(a) && ship.CountAmmoType(a) > 0))) return false;
            return ship.GetPointCost() > 0;
        }

        private bool RequiresAmmo(IWeapon weapon) => weapon.NeedsExternalAmmoFeed || weapon.SupportsAmmoSelection || weapon.WepType == WeaponType.Missile;
        private bool ResourcesOkay(Ship ship) => ship.Crew.CrewDemand <= ship.Crew.MaxCrewSize && ship.Resources.All(r => r.EditorSummary.TotalConsumed <= r.EditorSummary.TotalProvided);

        private void LoadStores(Ship ship)
        {
            var weapons = ship.Hull.CollectComponents<IWeapon>();
            var providers = ship.Hull.CollectComponents<IMagazineProvider>();
            foreach (var provider in providers)
            {
                // Externally fed launchers expose a read-only magazine facade.
                if (provider.RemainingCapacity <= 0) continue;
                var choices = provider.AvailableMunitions.AllMunitions.Where(a => ship.Hull.CanMountEquipment(a) && provider.RestrictionCheck(a) && weapons.Any(w => w.IsAmmoCompatible(a))).OrderBy(a => rng.Next()).ToList();
                foreach (var ammo in choices.Take(4))
                {
                    if (provider.RemainingCapacity <= 0) break;
                    int count = Math.Max(1, (int)(provider.RemainingCapacity / Math.Max(0.001f, provider.VolumeBased ? ammo.StorageVolume : 1) / Math.Min(4, choices.Count)));
                    provider.AddToMagazine(ammo, (uint)Math.Min(10000, count));
                }
            }
            foreach (var hangar in ship.Hull.CollectComponents<ICraftHangar>())
            {
                var choices = ship.Fleet.FleetCraftDesigns.AllCraftDesigns.Where(c => hangar.CanFitCraft(c)).ToList();
                int limit = 0;
                while (choices.Count > 0 && limit++ < 24)
                {
                    var craft = choices[rng.Next(choices.Count)];
                    if (!hangar.HasStorageSpaceForCraft(craft)) { choices.Remove(craft); continue; }
                    hangar.AddCraftToStorage(craft);
                }
            }
        }

        private void Finish()
        {
            LifecycleBridge.Instance.Await(request.DocumentElement, "generation");
            string path = Path.Combine(exchange, "generation-request.xml");
            if (File.Exists(path) && Read(path).DocumentElement["Id"]?.InnerText == id) File.Delete(path);
            request = null; stage = "idle"; editor = null; SceneManager.LoadScene("MainMenu", LoadSceneMode.Single);
        }
    }
}

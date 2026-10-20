// Campaign restrictions wrap the native editor; native fitting rules remain in control.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Xml;
using Bundles;
using FleetEditor;
using FleetEditor.FleetCompHistory;
using HarmonyLib;
using Ships;
using Ships.Serialization;
using TMPro;
using UI;
using UnityEngine;
using UnityEngine.SceneManagement;
using Utility;
using Utility.Localization;

namespace NBCBridge
{
    public sealed class RefitBridge : MonoBehaviour
    {
        public static RefitBridge Instance;
        public FleetEditorController Editor;
        public bool Ready;
        public bool PatchesReady => patchesReady;
        private string exchange, id = "", folder, stage = "idle", message = "", seen = "";
        private int revision;
        private float nextPoll, started;
        private XmlDocument request;
        private SerializedFleet baseline;
        private Dictionary<string, int> supply = new Dictionary<string, int>();
        private string pendingAction;
        private bool confirmCancel;
        private bool patchesReady;
        public static bool Scoped => Instance != null && Instance.Ready && Instance.Editor != null;

        private void Start()
        {
            Instance = this;
            exchange = Path.GetFullPath(Path.Combine("Saves", "NBCBridge"));
            Directory.CreateDirectory(exchange);
            try
            {
                new Harmony("nbc.campaign.refit.v1").PatchAll(typeof(RefitBridge).Assembly);
                patchesReady = true;
                ExportCatalog();
                var fittingCatalogExported = false;
                try
                {
                    FittingCatalog.Export(exchange);
                    fittingCatalogExported = true;
                }
                catch (Exception catalogError) { Debug.LogError("[NBC Fitting Catalog] " + catalogError); }
                Status(fittingCatalogExported
                    ? "Refit bridge ready. Fitting catalogue exported."
                    : "Refit bridge ready. Fitting catalogue export failed; see Player.log.");
            }
            catch (Exception ex) { Fail(ex); }
        }

        public static object Field(object target, string name)
        {
            return AccessTools.Field(target.GetType(), name)?.GetValue(target);
        }

        private static XmlDocument Read(string path)
        {
            var doc = new XmlDocument { XmlResolver = null };
            using (var reader = XmlReader.Create(path, new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null })) doc.Load(reader);
            return doc;
        }

        private static string Hash(string path)
        {
            using (var sha = SHA256.Create())
            using (var input = File.OpenRead(path)) return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }

        private static void Write(string path, byte[] bytes)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path + ".tmp", bytes);
            if (File.Exists(path)) File.Replace(path + ".tmp", path, null);
            else File.Move(path + ".tmp", path);
        }

        private static void Write(string path, XmlDocument doc)
        {
            using (var stream = new MemoryStream()) { doc.Save(stream); Write(path, stream.ToArray()); }
        }

        private static void Add(XmlNode node, string key, object value)
        {
            var child = node.OwnerDocument.CreateElement(key);
            child.InnerText = Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
            node.AppendChild(child);
        }

        private void ExportCatalog()
        {
            var doc = new XmlDocument();
            var root = doc.CreateElement("ComponentCatalog"); doc.AppendChild(root);
            Add(root, "GameVersion", Application.version);
            foreach (var component in BundleManager.Instance.AllComponents.Where(c => c.SaveKey.StartsWith("Stock/")).OrderBy(c => c.ComponentName))
            {
                var item = doc.CreateElement("Component"); root.AppendChild(item);
                Add(item, "Key", component.SaveKey); Add(item, "Name", component.ComponentName.Localize());
                Add(item, "Type", component.Type); Add(item, "Category", component.Category);
            }
            Write(Path.Combine(exchange, "catalog.xml"), doc);
        }

        private void Status(string text)
        {
            message = text;
            var doc = new XmlDocument(); var root = doc.CreateElement("RefitStatus"); doc.AppendChild(root);
            Add(root, "Id", id); Add(root, "Revision", revision); Add(root, "Stage", stage); Add(root, "Message", text);
            Write(Path.Combine(exchange, "refit-status.xml"), doc);
            Debug.Log("[NBC Refit] " + text);
        }

        private void Fail(Exception error)
        {
            stage = "error";
            Status("Refit error: " + error.Message + ". Cancel the session in Campaign Command before retrying.");
            Debug.LogError("[NBC Refit] " + error);
        }

        public void Explain(string text) { message = text; }

        private void Update()
        {
            if (Time.realtimeSinceStartup < nextPoll) return;
            nextPoll = Time.realtimeSinceStartup + 0.25f;
            try { Tick(); } catch (Exception ex) { Fail(ex); }
        }

        private void Tick()
        {
            if (request != null)
            {
                string cancel = Path.Combine(exchange, "refit-cancel.xml");
                if (File.Exists(cancel) && Read(cancel).DocumentElement["Id"]?.InnerText == id)
                {
                    Return(false); return;
                }
                string latestPath = Path.Combine(exchange, "refit-request.xml");
                if (File.Exists(latestPath))
                {
                    var latest = Read(latestPath).DocumentElement;
                    if (latest["Id"]?.InnerText == id && latest["Revision"]?.InnerText != revision.ToString())
                    {
                        bool hadEditor = Editor != null;
                        Ready = false; Editor = null; request = null; stage = "idle";
                        if (hadEditor) SceneManager.LoadScene("MainMenu", LoadSceneMode.Single);
                        return;
                    }
                }
            }
            if (request == null)
            {
                string path = Path.Combine(exchange, "refit-request.xml");
                if (!File.Exists(path)) return;
                var incoming = Read(path);
                var root = incoming.DocumentElement;
                string target = root["RuntimeInstance"]?.InnerText;
                if (!string.IsNullOrEmpty(target) && target != LifecycleBridge.Instance.Identity) return;
                string token = root["Id"]?.InnerText + "/" + root["Revision"]?.InnerText;
                if (token == seen) return;
                Guid parsed;
                if (root.Name != "RefitRequest" || !Guid.TryParse(root["Id"]?.InnerText, out parsed)) throw new InvalidDataException("Invalid refit request");
                string cancel = Path.Combine(exchange, "refit-cancel.xml");
                if (File.Exists(cancel) && Read(cancel).DocumentElement["Id"]?.InnerText == root["Id"].InnerText) { seen = token; return; }
                if (!patchesReady) throw new InvalidOperationException("Editor patches did not initialize; restart with the updated bridge");
                if (File.Exists(Path.Combine(exchange, "request.xml")) && Read(Path.Combine(exchange, "request.xml")).DocumentElement["RuntimeInstance"]?.InnerText == LifecycleBridge.Instance.Identity) throw new InvalidOperationException("A battle request is also pending");
                if (!LifecycleBridge.Instance.Accept(root)) return;
                request = incoming; id = root["Id"].InnerText; revision = int.Parse(root["Revision"].InnerText); seen = token;
                folder = root["Directory"].InnerText;
                if (!Path.IsPathRooted(folder) || !Directory.Exists(folder)) throw new InvalidDataException("Invalid refit directory");
                if (File.Exists(Path.Combine(folder, "ready-" + revision + ".xml"))) { request = null; return; }
                if (Hash(root["Fleet"].InnerText) != root["Hash"].InnerText) throw new InvalidDataException("Refit input changed after submission");
                baseline = Fleet.ReadXMLFleet(new FilePath(Path.Combine(folder, "baseline.fleet")));
                if (baseline == null) throw new InvalidDataException("Cannot load original fleet");
                supply.Clear();
                foreach (XmlElement item in root.SelectNodes("Supply/Item"))
                {
                    int count = int.Parse(item.GetAttribute("quantity"));
                    if (count < 0) throw new InvalidDataException("Negative inventory quantity");
                    supply.Add(item.GetAttribute("key"), count);
                }
                stage = "waiting-menu"; started = Time.realtimeSinceStartup; Ready = false; confirmCancel = false;
                Status("Refit requested. Return NEBULOUS to its main menu.");
            }
            if (stage == "error") return;
            if (stage == "waiting-menu")
            {
                if (UnityEngine.Object.FindObjectOfType<global::UI.MainMenu>() == null) return;
                var summary = Fleet.GetSummaryForFleet(new FilePath(request.DocumentElement["Fleet"].InnerText));
                if (summary == null || !summary.ValidForUse || !summary.AllModDependenciesLoaded) throw new InvalidDataException("Fleet is incompatible or needs missing mods");
                CrossScenePipe.SetData(new EditFleetData { Fleet = summary });
                stage = "loading"; started = Time.realtimeSinceStartup;
                Status("Opening your whole fleet in the native editor…");
                UnityEngine.Object.FindObjectOfType<global::UI.MainMenu>().ButtonFleetEditor();
                return;
            }
            if (stage == "loading")
            {
                Editor = UnityEngine.Object.FindObjectOfType<FleetEditorController>();
                if (Editor == null || Editor.Fleet == null || Editor.EditorControllers.Count != baseline.Ships.Count)
                {
                    if (Time.realtimeSinceStartup - started > 120) throw new TimeoutException("Editor did not finish loading");
                    return;
                }
                Validate();
                Ready = true; stage = "editor";
                Status("Campaign refit active. Removed components are shared across the fleet. Use Review refit to return.");
            }
            if (Ready && Editor == null) { Ready = false; throw new InvalidOperationException("Editor closed without exporting. Resume or cancel in the app"); }
            if (Ready && pendingAction != null)
            {
                var action = pendingAction; pendingAction = null;
                Return(action == "review");
            }
        }

        public Dictionary<string, int> Counts()
        {
            var counts = new Dictionary<string, int>();
            foreach (var ship in Editor.Fleet.FleetShips)
            foreach (var socket in ship.Hull.AllSockets)
                if (socket.Component != null)
                {
                    string key = socket.Component.SaveKey;
                    counts[key] = counts.ContainsKey(key) ? counts[key] + 1 : 1;
                }
            return counts;
        }

        public int Available(string key)
        {
            var counts = Counts();
            return (supply.ContainsKey(key) ? supply[key] : 0) - (counts.ContainsKey(key) ? counts[key] : 0);
        }

        public bool CanSet(HullSocket socket, HullComponent component)
        {
            if (component == null || socket.Component?.SaveKey == component.SaveKey) return true;
            if (Available(component.SaveKey) > 0) return true;
            Explain("No spare " + component.ComponentName.Localize() + ". Remove one from another ship or buy it in Campaign Command.");
            return false;
        }

        public bool CanHistory(object history, bool undo)
        {
            var events = Field(history, undo ? "_undoEvents" : "_redoEvents") as IEnumerable;
            object next = events?.Cast<object>().FirstOrDefault();
            if (next == null) return true;
            string name = next.GetType().Name;
            if (name == "PasteWholeFittingEvent") { Explain("Whole-fitting paste is disabled during campaign refits."); return false; }
            if (name != "AddComponentEvent" && name != "RemoveComponentEvent") return true;
            var socket = Field(next, "_socket") as HullSocket;
            string target = null;
            if (name == "RemoveComponentEvent" && undo || name == "AddComponentEvent" && !undo) target = Field(next, "_componentKey") as string;
            if (name == "AddComponentEvent" && undo)
            {
                object replaced = Field(next, "_replacedEvent");
                if (replaced != null) target = Field(replaced, "_componentKey") as string;
            }
            if (socket == null) throw new InvalidOperationException("Cannot validate editor history");
            return target == null || CanSet(socket, BundleManager.Instance.GetHullComponent(target));
        }

        private void Validate()
        {
            var current = Editor.Fleet.GetSerializable();
            if (current.FactionKey != baseline.FactionKey || current.Ships.Count != baseline.Ships.Count || current.Ships.Select(s => s.Key).Distinct().Count() != baseline.Ships.Count)
                throw new InvalidDataException("Fleet composition changed during refit");
            foreach (var original in baseline.Ships)
            {
                var ship = current.Ships.SingleOrDefault(s => s.Key == original.Key);
                if (ship == null || ship.HullType != original.HullType) throw new InvalidDataException("A ship identity or hull changed");
            }
            foreach (var pair in Counts())
                if (!supply.ContainsKey(pair.Key) || pair.Value > supply[pair.Key]) throw new InvalidDataException("Owned quantity exceeded: " + pair.Key);
        }

        private void Return(bool review)
        {
            if (review)
            {
                Validate();
                string path = Path.Combine(folder, "draft-" + revision + ".fleet");
                Write(path, Editor.Fleet.GetSerializable().CreateXmlDocumentWithRoot());
                var doc = new XmlDocument(); var root = doc.CreateElement("RefitReady"); doc.AppendChild(root);
                Add(root, "RuntimeInstance", LifecycleBridge.Instance.Identity);
                Add(root, "Id", id); Add(root, "Revision", revision); Add(root, "Hash", Hash(path));
                var labels = doc.CreateElement("Sockets"); root.AppendChild(labels);
                foreach (var ship in Editor.Fleet.FleetShips)
                foreach (var socket in ship.Hull.AllSockets)
                {
                    var label = doc.CreateElement("Socket"); labels.AppendChild(label);
                    label.SetAttribute("ship", ship.Key.ToString()); label.SetAttribute("key", socket.Key);
                    label.SetAttribute("name", socket.FullName.Localize());
                }
                Write(Path.Combine(folder, "ready-" + revision + ".xml"), doc);
            }
            else
            {
                var doc = new XmlDocument(); var root = doc.CreateElement("RefitReady"); doc.AppendChild(root);
                Add(root, "RuntimeInstance", LifecycleBridge.Instance.Identity);
                Add(root, "Id", id); Add(root, "Revision", revision); Add(root, "Cancelled", "true");
                Write(Path.Combine(folder, "ready-" + revision + ".xml"), doc);
            }
            LifecycleBridge.Instance.Await(request.DocumentElement, "refit");
            bool hadEditor = Editor != null;
            Ready = false; stage = review ? "review" : "cancelled";
            Status(review ? "Draft exported. Review and confirm in Campaign Command." : "Refit cancelled. Campaign fleet and inventory preserved.");
            string pathRequest = Path.Combine(exchange, "refit-request.xml");
            if (File.Exists(pathRequest))
            {
                var latest = Read(pathRequest).DocumentElement;
                if (latest["Id"]?.InnerText == id && latest["Revision"]?.InnerText == revision.ToString()) File.Delete(pathRequest);
            }
            request = null; Editor = null;
            if (hadEditor) SceneManager.LoadScene("MainMenu", LoadSceneMode.Single);
        }

        private void OnGUI()
        {
            if (!Scoped) return;
            if (UnityEngine.Object.FindObjectsOfType<ShipEditorPane>().Any(p => p.ComponentPaletteOpen)) return;
            int oldLabelSize = GUI.skin.label.fontSize, oldButtonSize = GUI.skin.button.fontSize;
            GUI.skin.label.fontSize = 17; GUI.skin.button.fontSize = 18;
            GUILayout.BeginArea(new Rect(Screen.width / 2 - 360, Screen.height - 245, 720, 135), GUI.skin.box);
            GUILayout.Label("CAMPAIGN REFIT — shared fleet inventory; no costs or facility restrictions");
            GUILayout.Label(message, GUILayout.Height(42));
            GUILayout.BeginHorizontal();
            if (!confirmCancel)
            {
                if (GUILayout.Button("Review refit → Campaign", GUILayout.Height(34))) pendingAction = "review";
                if (GUILayout.Button("Cancel refit", GUILayout.Height(34))) confirmCancel = true;
            }
            else
            {
                GUILayout.Label("Discard this session?");
                if (GUILayout.Button("Discard changes", GUILayout.Height(34))) pendingAction = "cancel";
                if (GUILayout.Button("Keep editing", GUILayout.Height(34))) confirmCancel = false;
            }
            GUILayout.EndHorizontal(); GUILayout.EndArea();
            GUI.skin.label.fontSize = oldLabelSize; GUI.skin.button.fontSize = oldButtonSize;
        }

        public void RequestExit() { confirmCancel = true; Explain("Use Review refit to keep your draft, or discard this session."); }
    }

    [HarmonyPatch(typeof(ComponentPalette), "GetItemsForSocket")]
    static class PaletteInventoryPatch
    {
        static void Postfix(HullSocket socket, ref List<SelectableListItem> __result)
        {
            if (!RefitBridge.Scoped) return;
            var bridge = RefitBridge.Instance;
            __result.RemoveAll(item => item is PaletteItem p && p.Component != null && bridge.Available(p.Component.SaveKey) <= 0 && socket.Component?.SaveKey != p.Component.SaveKey);
            for (int i = __result.Count - 1; i >= 0; i--)
                if (!(__result[i] is PaletteItem) && (i == __result.Count - 1 || !(__result[i + 1] is PaletteItem))) __result.RemoveAt(i);
            foreach (var item in __result.OfType<PaletteItem>().Where(p => p.Component != null))
            {
                var label = RefitBridge.Field(item, "_componentName") as TextMeshProUGUI;
                if (label != null) label.text = item.Component.ComponentName.Localize() + "  [spares: " + bridge.Available(item.Component.SaveKey) + "]";
            }
        }
    }

    [HarmonyPatch(typeof(EditorShipController), "SetSocketComponent")]
    static class InstallInventoryPatch
    {
        static bool Prefix(HullSocket socket, HullComponent component) => !RefitBridge.Scoped || RefitBridge.Instance.CanSet(socket, component);
    }

    [HarmonyPatch(typeof(Ship), "SetSocketComponent")]
    static class LowLevelInventoryPatch
    {
        static bool Prefix(Ship __instance, HullSocket socket, HullComponent component) => !RefitBridge.Scoped || __instance.Fleet != RefitBridge.Instance.Editor.Fleet || RefitBridge.Instance.CanSet(socket, component);
    }

    [HarmonyPatch]
    static class HistoryInventoryPatch
    {
        static IEnumerable<MethodBase> TargetMethods()
        {
            yield return AccessTools.Method(typeof(UndoHistory<FleetCompositionHistoryEvent>), "Undo");
            yield return AccessTools.Method(typeof(UndoHistory<FleetCompositionHistoryEvent>), "Redo");
        }
        static bool Prefix(object __instance, MethodBase __originalMethod) => !RefitBridge.Scoped || RefitBridge.Instance.CanHistory(__instance, __originalMethod.Name == "Undo");
    }

    [HarmonyPatch(typeof(EditorShipController), "PasteWholeFitting")]
    static class NoWholePastePatch
    {
        static bool Prefix()
        {
            if (!RefitBridge.Scoped) return true;
            RefitBridge.Instance.Explain("Fit individual components; whole-fitting paste is disabled during campaign refits."); return false;
        }
    }

    [HarmonyPatch]
    static class RosterLockPatch
    {
        static IEnumerable<MethodBase> TargetMethods()
        {
            foreach (var name in new[] { "NewFleet", "LoadFleet", "SaveFleet", "SaveFleetAs", "QuickTestBattle", "CreateNewShip", "CreateNewShipFromBlueprint", "DeleteShip" })
                foreach (var method in typeof(FleetEditorController).GetMethods().Where(m => m.Name == name)) yield return method;
            foreach (var name in new[] { "CreateShip", "DuplicateShip", "DeleteShip" })
                yield return AccessTools.Method(typeof(FleetCompositionSubmodeController), name);
        }
        static bool Prefix()
        {
            if (!RefitBridge.Scoped) return true;
            RefitBridge.Instance.Explain("Campaign roster is locked. Use Review refit to export your draft."); return false;
        }
    }

    [HarmonyPatch(typeof(FleetEditorController), "Exit")]
    static class RefitExitPatch
    {
        static bool Prefix()
        {
            if (!RefitBridge.Scoped) return true;
            RefitBridge.Instance.RequestExit(); return false;
        }
    }
}

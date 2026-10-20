using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Xml;
using UnityEngine;

namespace NBCBridge
{
    // A fresh process identity prevents stale exchange files from controlling a later game.
    public sealed class LifecycleBridge : MonoBehaviour
    {
        public static LifecycleBridge Instance;
        public readonly string Identity = Guid.NewGuid().ToString();
        private readonly string exchange = Path.GetFullPath(Path.Combine("Saves", "NBCBridge"));
        private string token = "", phase = "starting", kind = "", operation = "", revision = "0";
        private double processStart;
        private int pid;
        private float next;
        private static double Epoch(DateTime date) => (date.ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;

        private void Awake()
        {
            Instance = this;
            using (var process = Process.GetCurrentProcess()) { pid = process.Id; processStart = Epoch(process.StartTime); }
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i + 1 < args.Length; i++) if (args[i] == "-nbc-launch") token = args[i + 1];
        }

        public bool Accept(XmlElement request)
        {
            string target = request["RuntimeInstance"]?.InnerText;
            if (!string.IsNullOrEmpty(target) && target != Identity) return false;
            if (phase == "awaiting-ack" || phase == "quitting") return false;
            phase = request.Name == "RefitRequest" ? "refit" : "battle";
            return true;
        }

        public void Await(XmlElement request, string type)
        {
            if (request["CloseGame"]?.InnerText != "true") { phase = "starting"; return; }
            operation = request["Id"].InnerText; revision = request["Revision"]?.InnerText ?? "0";
            kind = type; phase = "awaiting-ack";
        }

        public void Idle() { if (phase != "awaiting-ack" && phase != "quitting") phase = "starting"; }

        private XmlElement Read(string path)
        {
            var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null };
            var doc = new XmlDocument { XmlResolver = null };
            using (var reader = XmlReader.Create(path, settings)) doc.Load(reader);
            return doc.DocumentElement;
        }

        private void Quit() { phase = "quitting"; Heartbeat(); Application.Quit(); }

        private void Update()
        {
            if (Time.realtimeSinceStartup < next) return;
            next = Time.realtimeSinceStartup + 0.5f;
            try
            {
                if (phase == "awaiting-ack")
                {
                    string path = Path.Combine(exchange, "operation-ack.xml");
                    if (File.Exists(path))
                    {
                        var ack = Read(path);
                        if (ack.Name == "OperationAck" && ack["Instance"]?.InnerText == Identity && ack["Id"]?.InnerText == operation &&
                            ack["Kind"]?.InnerText == kind && ack["Revision"]?.InnerText == revision) { Quit(); return; }
                    }
                }
                bool menu = UnityEngine.Object.FindObjectOfType<global::UI.MainMenu>() != null;
                if (phase == "starting" || phase == "ready") phase = menu && RefitBridge.Instance != null && RefitBridge.Instance.PatchesReady ? "ready" : "starting";
                string command = Path.Combine(exchange, "runtime-command.xml");
                if (phase == "ready" && token.Length > 0 && File.Exists(command))
                {
                    var root = Read(command);
                    if (root["Action"]?.InnerText == "QuitIfIdle" && root["LaunchToken"]?.InnerText == token) { Quit(); return; }
                }
                Heartbeat();
            }
            catch (Exception ex) { UnityEngine.Debug.LogError("[NBC Lifecycle] " + ex); }
        }

        private void Heartbeat()
        {
            Directory.CreateDirectory(exchange);
            var doc = new XmlDocument(); var root = doc.CreateElement("GameRuntime"); doc.AppendChild(root);
            Action<string, string> add = (key, value) => { var child = doc.CreateElement(key); child.InnerText = value; root.AppendChild(child); };
            add("Protocol", "1"); add("Instance", Identity); add("Pid", pid.ToString());
            add("StartedEpoch", processStart.ToString("R", CultureInfo.InvariantCulture));
            add("HeartbeatEpoch", Epoch(DateTime.UtcNow).ToString("R", CultureInfo.InvariantCulture));
            add("LaunchToken", token); add("Phase", phase); add("Ready", phase == "ready" ? "true" : "false");
            add("Message", phase == "awaiting-ack" ? "Waiting for Campaign Command to safely import the export…" : "Waiting for NEBULOUS to reach its main menu…");
            string path = Path.Combine(exchange, "runtime-status.xml"); doc.Save(path + ".tmp");
            if (File.Exists(path)) File.Replace(path + ".tmp", path, null); else File.Move(path + ".tmp", path);
        }
    }
}

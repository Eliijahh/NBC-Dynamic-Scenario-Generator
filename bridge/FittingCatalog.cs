// Exports the native fitting rules and stock parts used by the offline fleet generator.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Xml;
using Bundles;
using Factions;
using Munitions;
using Ships;
using Ships.Controls;
using UnityEngine;
using Utility;

namespace NBCBridge
{
    internal static class FittingCatalog
    {
        private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;

        public static void Export(string exchange)
        {
            var manager = BundleManager.Instance;
            if (manager == null) throw new InvalidOperationException("The game bundle manager is not ready.");

            var doc = new XmlDocument();
            var root = doc.CreateElement("FittingCatalog");
            doc.AppendChild(root);
            Add(root, "Format", 1);
            Add(root, "GameVersion", Application.version);
            Add(root, "GeneratedUtc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture));

            foreach (var key in new[] { "Stock/Alliance", "Stock/Protectorate" })
            {
                var faction = manager.GetFaction(key);
                if (faction == null) continue;
                ExportFaction(doc, root, manager, faction);
            }

            Write(Path.Combine(exchange, "fitting-catalog.xml"), doc);
        }

        private static void ExportFaction(XmlDocument doc, XmlElement root, BundleManager manager, FactionDescription faction)
        {
            var factionNode = doc.CreateElement("Faction");
            root.AppendChild(factionNode);
            Add(factionNode, "Key", faction.SaveKey);
            Add(factionNode, "Name", faction.LocalizedName);

            var munitions = manager.AllMunitions
                .Where(m => !string.IsNullOrEmpty(m.SaveKey) && m.SaveKey.StartsWith("Stock/", StringComparison.Ordinal) && m.UseableByFaction(faction))
                .OrderBy(m => m.SaveKey, StringComparer.Ordinal)
                .ToList();

            var munitionKeys = new HashSet<string>(munitions.Select(m => m.SaveKey), StringComparer.Ordinal);
            var munitionNode = doc.CreateElement("Munitions");
            factionNode.AppendChild(munitionNode);
            foreach (var munition in munitions)
            {
                var item = doc.CreateElement("Munition");
                munitionNode.AppendChild(item);
                Add(item, "Key", munition.SaveKey);
                Add(item, "Name", munition.MunitionName);
                Add(item, "Storage", munition.StorageVolume);
                Add(item, "Points", munition.PointCost);
                Add(item, "Division", munition.PointDivision);
                Add(item, "SimMethod", munition.SimMethod);
                Add(item, "Role", munition.Role);
                Add(item, "Type", munition.Type);
                Add(item, "Class", munition.Tags.Class);
                Add(item, "Subclass", munition.Tags.Subclass);
            }

            var hullNode = doc.CreateElement("Hulls");
            factionNode.AppendChild(hullNode);
            foreach (var hull in manager.GetFactionHulls(faction)
                .Where(h => !string.IsNullOrEmpty(h.SaveKey) && h.SaveKey.StartsWith("Stock/", StringComparison.Ordinal))
                .OrderBy(h => h.SaveKey, StringComparer.Ordinal))
            {
                var item = doc.CreateElement("Hull");
                hullNode.AppendChild(item);
                Add(item, "Key", hull.SaveKey);
                Add(item, "Name", hull.name);
                Add(item, "Class", hull.ClassName);
                Add(item, "Points", hull.PointCost);
                Add(item, "Crew", hull.BaseCrewComplement);
                var sockets = doc.CreateElement("Sockets");
                item.AppendChild(sockets);
                foreach (var socket in hull.AllSockets.OrderBy(s => s.Key, StringComparer.Ordinal))
                {
                    var s = doc.CreateElement("Socket");
                    sockets.AppendChild(s);
                    var size = socket.Size.Dimensions;
                    Add(s, "Key", socket.Key);
                    Add(s, "Type", (int)socket.Type);
                    Add(s, "X", size.x);
                    Add(s, "Y", size.y);
                    Add(s, "Z", size.z);
                    Add(s, "Overhang", (int)socket.Size.InteriorOverhangSpace);
                    Add(s, "Default", socket.DefaultComponent ?? "");
                }
            }

            var componentNode = doc.CreateElement("Components");
            factionNode.AppendChild(componentNode);
            foreach (var component in manager.GetFactionComponents(faction)
                .Where(c => !string.IsNullOrEmpty(c.SaveKey) && c.SaveKey.StartsWith("Stock/", StringComparison.Ordinal))
                .OrderBy(c => c.SaveKey, StringComparer.Ordinal))
            {
                var item = doc.CreateElement("Component");
                componentNode.AppendChild(item);
                Add(item, "Key", component.SaveKey);
                Add(item, "Name", component.name);
                Add(item, "Category", component.Category ?? "");
                Add(item, "Type", (int)component.Type);
                var size = component.Size;
                Add(item, "X", size.x);
                Add(item, "Y", size.y);
                Add(item, "Z", size.z);
                Add(item, "Overhang", Convert.ToInt32(Reflect(component, "_interiorOverhang") ?? 0));
                Add(item, "Rotate", (int)component.RotateToFit);
                Add(item, "CanTile", component.CanTile ? 1 : 0);
                Add(item, "ConsistentCost", Convert.ToInt32(Reflect(component, "_consistentCostForTiled") ?? 0));
                Add(item, "Points", component.PointCost);
                Add(item, "Compounding", component.CompoundingCost ? 1 : 0);
                Add(item, "CompoundingClass", component.CompoundingCostClass ?? "");
                Add(item, "FirstFree", component.FirstInstanceFree ? 1 : 0);
                Add(item, "CompoundingMultiplier", Reflect(component, "_compoundingMultiplier") ?? 1f);
                Add(item, "Crew", component is CrewOperatedComponent crew ? crew.GetCrewRequirement() : 0);
                Add(item, "Drive", component is DriveComponent ? 1 : 0);
                Add(item, "Powerplant", component is PowerplantComponent ? 1 : 0);
                Add(item, "Command", component is CommandComponent ? 1 : 0);
                Add(item, "CommandAll", component is CommandComponent command && command.CommandAbility == CommandFunctions.All ? 1 : 0);
                Add(item, "Sensor", component is BaseActiveSensorComponent ? 1 : 0);
                Add(item, "BulkMagazine", component is BulkMagazineComponent ? 1 : 0);
                Add(item, "Hangar", component is ICraftHangar ? 1 : 0);

                var weapon = component as IWeapon;
                Add(item, "Weapon", weapon != null ? 1 : 0);
                if (weapon != null)
                {
                    Add(item, "WepType", weapon.WepType);
                    Add(item, "Role", RoleName(weapon));
                    Add(item, "NeedsAmmo", weapon.NeedsExternalAmmoFeed ? 1 : 0);
                    Add(item, "AmmoSelection", weapon.SupportsAmmoSelection ? 1 : 0);
                    Add(item, "AllowGrouping", weapon.AllowGrouping ? 1 : 0);
                    Add(item, "GroupingName", weapon.GroupingName ?? "");
                    var ammoNode = doc.CreateElement("Ammo");
                    item.AppendChild(ammoNode);
                    foreach (var munition in munitions)
                    {
                        if (!munitionKeys.Contains(munition.SaveKey)) continue;
                        try
                        {
                            if (!weapon.IsAmmoCompatible(munition)) continue;
                        }
                        catch { continue; }
                        Add(ammoNode, "Munition", munition.SaveKey);
                    }
                }

                if (component is BulkMagazineComponent magazine)
                {
                    var magNode = doc.CreateElement("Magazine");
                    item.AppendChild(magNode);
                    Add(magNode, "Capacity", Convert.ToInt32(Reflect(magazine, "_availableVolume") ?? 0));
                    var allowed = doc.CreateElement("Ammo");
                    magNode.AppendChild(allowed);
                    foreach (var munition in munitions)
                    {
                        try
                        {
                            if (!magazine.RestrictionCheck(munition)) continue;
                        }
                        catch { continue; }
                        Add(allowed, "Munition", munition.SaveKey);
                    }
                }

                var provided = doc.CreateElement("ResourcesProvided");
                item.AppendChild(provided);
                foreach (var resource in component.ResourcesProvided ?? new ResourceModifier[0])
                    AddResource(doc, provided, resource);
                var required = doc.CreateElement("ResourcesRequired");
                item.AppendChild(required);
                foreach (var resource in component.ResourcesRequired ?? new ResourceModifier[0])
                    AddResource(doc, required, resource);
            }
        }

        private static void AddResource(XmlDocument doc, XmlElement parent, ResourceModifier resource)
        {
            var node = doc.CreateElement("Resource");
            parent.AppendChild(node);
            Add(node, "Name", resource.ResourceName);
            Add(node, "Amount", resource.Amount);
            Add(node, "PerUnit", resource.PerUnit ? 1 : 0);
            Add(node, "OnlyWhenOperating", resource.OnlyWhenOperating ? 1 : 0);
        }

        private static string RoleName(IWeapon weapon)
        {
            // Some launcher subclasses also have a private string called _role.
            // Walk the hierarchy and use only the actual WeaponRole enum field.
            for (var type = weapon.GetType(); type != null; type = type.BaseType)
            {
                var field = type.GetField("_role", PrivateInstance);
                if (field == null || field.FieldType != typeof(WeaponRole)) continue;
                var value = field.GetValue(weapon);
                return value == null ? "" : value.ToString();
            }
            return "";
        }

        private static FieldInfo FindField(Type type, string name)
        {
            while (type != null)
            {
                var field = type.GetField(name, PrivateInstance);
                if (field != null) return field;
                type = type.BaseType;
            }
            return null;
        }

        private static object Reflect(object target, string name)
        {
            return FindField(target.GetType(), name)?.GetValue(target);
        }

        private static void Add(XmlNode node, string key, object value)
        {
            var child = node.OwnerDocument.CreateElement(key);
            child.InnerText = Convert.ToString(value, CultureInfo.InvariantCulture);
            node.AppendChild(child);
        }

        private static void Write(string path, XmlDocument doc)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            using (var stream = new MemoryStream())
            {
                doc.Save(stream);
                File.WriteAllBytes(path + ".tmp", stream.ToArray());
            }
            if (File.Exists(path)) File.Replace(path + ".tmp", path, null);
            else File.Move(path + ".tmp", path);
        }
    }
}

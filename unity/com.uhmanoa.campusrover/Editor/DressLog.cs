using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace UHManoa.CampusRover.Editor
{
    /// <summary>Writes the Dress Map's choices (model, source, licence, restricted) to
    /// dress_log.json next to the export, so the repo keeps a record of every model in use.</summary>
    public static class DressLog
    {
        [MenuItem("Tools/Campus Rover/Write Dress Log (models, sources, licences)")]
        public static void Write()
        {
            var map = AssetDatabase.LoadAssetAtPath<DressMap>(CampusImporter.DressMapPath);
            if (map == null) { Debug.LogWarning("No Dress Map yet: import the campus first."); return; }
            var kinds = new JObject();
            int missing = 0;
            foreach (var e in map.entries)
            {
                var models = e.models.Where(m => m != null).Select(m => AssetDatabase.GetAssetPath(m)).ToArray();
                if (models.Length > 0 && (string.IsNullOrEmpty(e.source) || string.IsNullOrEmpty(e.licence))) missing++;
                kinds[e.id] = new JObject
                {
                    ["count"] = e.count, ["models"] = new JArray(models), ["fit"] = e.fit.ToString(),
                    ["source"] = e.source, ["licence"] = e.licence, ["restricted"] = e.restricted,
                };
            }
            var materials = new JObject();
            var mm = AssetDatabase.LoadAssetAtPath<MaterialMap>(CampusImporter.MaterialMapPath);
            foreach (var e in mm != null ? mm.entries : new System.Collections.Generic.List<MaterialMap.Entry>())
            {
                if (e.material == null) continue;
                if (string.IsNullOrEmpty(e.source) || string.IsNullOrEmpty(e.licence)) missing++;
                materials[e.className] = new JObject
                {
                    ["material"] = AssetDatabase.GetAssetPath(e.material), ["texture_size_m"] = e.textureSizeM,
                    ["source"] = e.source, ["licence"] = e.licence, ["restricted"] = e.restricted,
                };
            }
            string path = Path.Combine(Path.GetDirectoryName(Application.dataPath), CampusImporter.ExportRoot, "dress_log.json");
            File.WriteAllText(path, new JObject { ["kinds"] = kinds, ["materials"] = materials }.ToString());
            AssetDatabase.Refresh();
            if (missing > 0) Debug.LogWarning($"{missing} models or materials have no source or licence written down.");
            Debug.Log($"Dress log written: {path}");
        }
    }
}

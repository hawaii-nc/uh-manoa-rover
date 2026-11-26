using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace UHManoa.CampusRover.Editor
{
    /// <summary>Builds the campus scene from a `campus-rover build-unity` folder.
    /// Ground, structures and buildings come in as labelled meshes with colliders and friction;
    /// every tree, plant, prop, sculpture and person is placed from placements.json and dressed
    /// with the model chosen in the Dress Map (or the export's fallback model).
    /// Needs glTFast (com.unity.cloud.gltfast) to import the .glb files.</summary>
    public static class CampusImporter
    {
        public const string ExportRoot = "Assets/CampusExport";
        public const string RootName = "UH Manoa (campus-rover)";
        public const string DressMapPath = ExportRoot + "/DressMap.asset";
        public const string MaterialMapPath = ExportRoot + "/MaterialMap.asset";
        static readonly string[] LevelLayers = { "Traversable", "Conditional", "Blocked", "Dynamic" };

        [MenuItem("Tools/Campus Rover/Import Export Folder...")]
        static void ImportMenu()
        {
            string src = EditorUtility.OpenFolderPanel("Folder written by campus-rover build-unity", "", "");
            if (!string.IsNullOrEmpty(src)) Import(src);
        }

        [MenuItem("Tools/Campus Rover/Re-dress Placements")]
        static void RedressMenu()
        {
            var root = GameObject.Find(RootName);
            if (root == null) { Debug.LogWarning("Import the campus first."); return; }
            var site = root.GetComponent<CampusSite>();
            string folder = AssetDatabase.GetAssetPath(site.placements);
            Import(Path.GetDirectoryName(folder).Replace('\\', '/'), copy: false);
        }

        public static GameObject Import(string srcFolder, bool copy = true)
        {
            string dest = ToAssetFolder(srcFolder, copy);
            string abs = Path.GetFullPath(dest);
            var classes = (JObject)JObject.Parse(File.ReadAllText(Path.Combine(abs, "classes.json")))["classes"];
            var placementDoc = JObject.Parse(File.ReadAllText(Path.Combine(abs, "placements.json")));
            var siteDoc = JObject.Parse(File.ReadAllText(Path.Combine(abs, "site.json")));
            EnsureLayers();

            var old = GameObject.Find(RootName);
            if (old != null) Object.DestroyImmediate(old);
            var root = new GameObject(RootName);
            var site = root.AddComponent<CampusSite>();
            FillSite(site, siteDoc, dest);

            // Frame: read the axis markers, then turn the campus so north is +Z and east +X.
            var nodesAsset = LoadGlb(dest + "/placements.glb");
            var nodes = Object.Instantiate(nodesAsset, root.transform);
            nodes.name = "placement nodes (temporary)";
            MeasureFrame(site, nodes.transform);

            var physMats = new Dictionary<string, PhysicsMaterial>();
            string uvPath = Path.Combine(abs, "node_uv.json");
            var nodeUv = File.Exists(uvPath) ? JObject.Parse(File.ReadAllText(uvPath)) : new JObject();
            var matMap = LoadMaterialMap();
            var matCounts = new Dictionary<string, int>();
            var geometry = new GameObject("Geometry").transform;
            geometry.SetParent(root.transform, false);
            foreach (string file in Directory.GetFiles(Path.Combine(abs, "geometry"), "*.glb").OrderBy(f => f))
            {
                var asset = LoadGlb(dest + "/geometry/" + Path.GetFileName(file));
                if (asset == null) continue;
                var go = Object.Instantiate(asset, geometry);
                go.name = Path.GetFileNameWithoutExtension(file);
                LabelGeometry(go.transform, classes, physMats, dest);
                ApplyMaterials(go.transform, matMap, nodeUv, matCounts);
            }

            foreach (var kv in matCounts) matMap.GetOrAdd(kv.Key).nodes = kv.Value;
            matMap.entries.Sort((a, b) => b.nodes.CompareTo(a.nodes));
            EditorUtility.SetDirty(matMap);

            var protoRoot = BuildPrototypes(dest, root.transform);
            var dress = LoadDressMap(placementDoc);
            PlaceAll(root.transform, nodes.transform, placementDoc, classes, protoRoot, dress, physMats, dest);
            Object.DestroyImmediate(nodes);
            DrawRoute(site, Path.Combine(abs, "network.json"));

            EditorSceneManager.MarkSceneDirty(root.scene);
            Selection.activeGameObject = root;
            Debug.Log($"Campus imported from {dest}: see the '{RootName}' object.");
            return root;
        }

        // ---------------------------------------------------------------- files

        static string ToAssetFolder(string src, bool copy)
        {
            src = Path.GetFullPath(src).Replace('\\', '/').TrimEnd('/');
            string assets = Path.GetFullPath(Application.dataPath).Replace('\\', '/');
            if (src.StartsWith(assets + "/") || !copy)
                return "Assets" + src.Substring(assets.Length);
            Directory.CreateDirectory(ExportRoot);
            string dest = ExportRoot + "/" + Path.GetFileName(src);
            if (Directory.Exists(dest)) { FileUtil.DeleteFileOrDirectory(dest); FileUtil.DeleteFileOrDirectory(dest + ".meta"); }
            FileUtil.CopyFileOrDirectory(src, dest);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            return dest;
        }

        static GameObject LoadGlb(string assetPath)
        {
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (go == null)
                Debug.LogError($"Could not load {assetPath} as a model. Is glTFast (com.unity.cloud.gltfast) installed?");
            return go;
        }

        static void FillSite(CampusSite site, JObject doc, string dest)
        {
            site.siteName = (string)doc["site"];
            site.crs = (string)doc["crs"];
            var o = doc["origin_site_xyz"];
            site.originSite = new Vector3((float)o[0], (float)o[1], (float)o[2]);
            var ll = doc["origin_lat_lon"];
            if (ll != null && ll.Type == JTokenType.Array) { site.latitude = (double)ll[0]; site.longitude = (double)ll[1]; }
            site.builtOn = (string)doc["built"];
            site.network = AssetDatabase.LoadAssetAtPath<TextAsset>(dest + "/network.json");
            site.placements = AssetDatabase.LoadAssetAtPath<TextAsset>(dest + "/placements.json");
        }

        static void MeasureFrame(CampusSite site, Transform nodes)
        {
            Transform Find(string n) => nodes.GetComponentsInChildren<Transform>(true).FirstOrDefault(t => t.name == n);
            var o = Find("site|origin"); var e = Find("site|east10"); var u = Find("site|up10"); var s = Find("site|south10");
            if (o == null || e == null || u == null || s == null) { Debug.LogError("Axis markers missing from placements.glb"); return; }
            var root = site.transform;
            Vector3 east = (e.position - o.position) / 10f, south = (s.position - o.position) / 10f;
            float turn = Vector3.SignedAngle(new Vector3(east.x, 0, east.z), Vector3.right, Vector3.up);
            root.rotation = Quaternion.AngleAxis(turn, Vector3.up) * root.rotation;
            Vector3 southNow = Quaternion.AngleAxis(turn, Vector3.up) * south;
            if (Vector3.Dot(southNow.normalized, Vector3.back) < 0.99f)
                Debug.LogWarning("The export frame looks mirrored after import; north may be flipped.");
            site.frameOrigin = root.InverseTransformPoint(o.position);
            site.frameEast = root.InverseTransformVector(e.position - o.position) / 10f;
            site.frameUp = root.InverseTransformVector(u.position - o.position) / 10f;
            site.frameSouth = root.InverseTransformVector(s.position - o.position) / 10f;
        }

        // ---------------------------------------------------------------- labels and physics

        static void EnsureLayers()
        {
            var tm = new SerializedObject(AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset")[0]);
            var layers = tm.FindProperty("layers");
            foreach (string name in LevelLayers)
            {
                bool found = false;
                for (int i = 8; i < layers.arraySize; i++)
                    if (layers.GetArrayElementAtIndex(i).stringValue == name) { found = true; break; }
                if (found) continue;
                for (int i = 8; i < layers.arraySize; i++)
                {
                    var p = layers.GetArrayElementAtIndex(i);
                    if (string.IsNullOrEmpty(p.stringValue)) { p.stringValue = name; break; }
                }
            }
            tm.ApplyModifiedProperties();
        }

        static int LayerOf(Traversability level)
        {
            int l = LayerMask.NameToLayer(SemanticLabel.LayerName(level));
            return l < 0 ? 0 : l;
        }

        static void Label(GameObject go, string cls, string group, string material, JObject classes)
        {
            var c = classes[cls] as JObject;
            if (!go.TryGetComponent(out SemanticLabel lab)) lab = go.AddComponent<SemanticLabel>();
            lab.className = cls; lab.group = group; lab.sourceMaterial = material;
            lab.classId = c != null ? (int)c["id"] : 0;
            lab.level = SemanticLabel.ParseLevel(c != null ? (string)c["level"] : "blocked");
            foreach (var t in go.GetComponentsInChildren<Transform>(true)) t.gameObject.layer = LayerOf(lab.level);
        }

        static PhysicsMaterial MaterialFor(string cls, JObject classes, Dictionary<string, PhysicsMaterial> cache, string dest)
        {
            if (cache.TryGetValue(cls, out var m)) return m;
            var c = classes[cls] as JObject;
            if (c == null) return null;
            string folder = dest + "/PhysicsMaterials";
            Directory.CreateDirectory(folder);
            m = new PhysicsMaterial(cls)
            {
                staticFriction = (float)c["friction_static"],
                dynamicFriction = (float)c["friction_dynamic"],
                frictionCombine = PhysicsMaterialCombine.Average,
                bounceCombine = PhysicsMaterialCombine.Minimum,
            };
            AssetDatabase.CreateAsset(m, $"{folder}/{cls}.physicMaterial");
            cache[cls] = m;
            return m;
        }

        static void LabelGeometry(Transform top, JObject classes, Dictionary<string, PhysicsMaterial> mats, string dest)
        {
            foreach (var t in top.GetComponentsInChildren<Transform>(true))
            {
                var parts = t.name.Split('|');
                if (parts.Length != 3) continue;
                string cls = parts[1];
                Label(t.gameObject, cls, parts[0], parts[2], classes);
                GameObjectUtility.SetStaticEditorFlags(t.gameObject,
                    StaticEditorFlags.BatchingStatic | StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic
                    | StaticEditorFlags.ContributeGI | StaticEditorFlags.ReflectionProbeStatic);
                string coll = (string)(classes[cls]?["collision"]) ?? "mesh";
                if (coll == "none") continue;
                foreach (var mf in t.GetComponentsInChildren<MeshFilter>(true))
                {
                    if (mf.sharedMesh == null) continue;
                    if (!mf.gameObject.TryGetComponent(out MeshCollider mc)) mc = mf.gameObject.AddComponent<MeshCollider>();
                    mc.sharedMesh = mf.sharedMesh;
                    mc.convex = coll == "convexHull";
                    mc.sharedMaterial = MaterialFor(cls, classes, mats, dest);
                }
            }
        }

        // ---------------------------------------------------------------- materials

        static MaterialMap LoadMaterialMap()
        {
            var map = AssetDatabase.LoadAssetAtPath<MaterialMap>(MaterialMapPath);
            if (map != null) return map;
            Directory.CreateDirectory(ExportRoot);
            map = ScriptableObject.CreateInstance<MaterialMap>();
            AssetDatabase.CreateAsset(map, MaterialMapPath);
            return map;
        }

        static readonly Dictionary<string, Material> Variants = new Dictionary<string, Material>();

        /// <summary>Swap the export's material for the Material Map's, scaled so one repeat covers
        /// the entry's real size: the export's UVs are metres / uv_tile_m.</summary>
        static void ApplyMaterials(Transform top, MaterialMap map, JObject nodeUv, Dictionary<string, int> counts)
        {
            foreach (var t in top.GetComponentsInChildren<Transform>(true))
            {
                var parts = t.name.Split('|');
                if (parts.Length != 3) continue;
                string cls = parts[1];
                // Ground and structures list class|material too (brick vs concrete walkway);
                // buildings only by class (each building has its own colour materials).
                bool fine = top.name == "ground" || top.name == "structures";
                string full = cls + "|" + parts[2];
                string key = fine ? full : cls;
                counts[key] = counts.TryGetValue(key, out int n) ? n + 1 : 1;
                var e = map.Find(full);
                if (e == null || e.material == null) e = map.Find(cls);
                if (e == null || e.material == null) continue;
                float tile = nodeUv[t.name]?["uv_tile_m"]?.Type == JTokenType.Float ? (float)nodeUv[t.name]["uv_tile_m"] : 0f;
                float scale = tile > 0f && e.textureSizeM > 0f ? tile / e.textureSizeM : 1f;
                var mat = Variant(e.material, scale);
                foreach (var r in t.GetComponentsInChildren<Renderer>(true))
                {
                    var mats = r.sharedMaterials;
                    for (int i = 0; i < mats.Length; i++) mats[i] = mat;
                    r.sharedMaterials = mats;
                }
            }
        }

        static Material Variant(Material baseMat, float scale)
        {
            if (Mathf.Abs(scale - 1f) < 1e-3f) return baseMat;
            string key = $"{AssetDatabase.GetAssetPath(baseMat)}@{scale:F3}";
            if (Variants.TryGetValue(key, out var m) && m != null) return m;
            string folder = ExportRoot + "/MaterialVariants";
            Directory.CreateDirectory(folder);
            string path = $"{folder}/{baseMat.name}_x{scale:F3}.mat";
            m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(baseMat) { name = $"{baseMat.name}_x{scale:F3}" };
                AssetDatabase.CreateAsset(m, path);
            }
            m.CopyPropertiesFromMaterial(baseMat);
            m.mainTextureScale = baseMat.mainTextureScale * scale;
            m.enableInstancing = true;
            EditorUtility.SetDirty(m);
            Variants[key] = m;
            return m;
        }

        // ---------------------------------------------------------------- placements

        static Transform BuildPrototypes(string dest, Transform root)
        {
            var holder = new GameObject("Fallback models").transform;
            holder.SetParent(root, false);
            holder.gameObject.SetActive(false);
            var asset = LoadGlb(dest + "/prototypes.glb");
            if (asset == null) return holder;
            var inst = Object.Instantiate(asset);
            var groups = new Dictionary<string, Transform>();
            foreach (var t in inst.GetComponentsInChildren<Transform>(true).ToList())
            {
                int bar = t.name.IndexOf('|');
                if (bar < 0 || (t.parent != null && t.parent.name.Contains("|"))) continue;
                string g = t.name.Substring(0, bar);
                if (!groups.TryGetValue(g, out var gt))
                {
                    gt = new GameObject(g).transform;
                    gt.SetParent(holder, false);
                    groups[g] = gt;
                }
                t.SetParent(gt, true);
            }
            Object.DestroyImmediate(inst);
            return holder;
        }

        static DressMap LoadDressMap(JObject placementDoc)
        {
            var map = AssetDatabase.LoadAssetAtPath<DressMap>(DressMapPath);
            if (map == null)
            {
                Directory.CreateDirectory(ExportRoot);
                map = ScriptableObject.CreateInstance<DressMap>();
                AssetDatabase.CreateAsset(map, DressMapPath);
            }
            foreach (var kv in (JObject)placementDoc["prototypes"])
            {
                var p = (JObject)kv.Value;
                var e = map.GetOrAdd(kv.Key);
                e.count = (int)p["count"];
                e.refHeightM = p["height_m"] != null ? (float)p["height_m"] : 0f;
                e.refCrownRadiusM = p["crown_radius_m"] != null ? (float)p["crown_radius_m"] : 0f;
                if (p["size_m"] is JArray sz) e.refSizeM = new Vector3((float)sz[0], (float)sz[2], (float)sz[1]);
            }
            map.entries.Sort((a, b) => b.count.CompareTo(a.count));
            EditorUtility.SetDirty(map);
            AssetDatabase.SaveAssets();
            return map;
        }

        static readonly Dictionary<string, string> KindClass = new Dictionary<string, string>
            { { "tree", "tree" }, { "plant", "tree" }, { "art", "art_piece" } };

        static void PlaceAll(Transform root, Transform nodes, JObject doc, JObject classes, Transform protos,
                             DressMap dress, Dictionary<string, PhysicsMaterial> mats, string dest)
        {
            var records = (JArray)doc["placements"];
            var protoInfo = (JObject)doc["prototypes"];
            var holder = new GameObject("Placements").transform;
            holder.SetParent(root, false);
            var byKind = new Dictionary<string, Transform>();
            var rng = new System.Random(7);
            foreach (var node in nodes.GetComponentsInChildren<Transform>(true))
            {
                var parts = node.name.Split('|');
                if (parts.Length != 4 || parts[0] != "p") continue;
                var rec = (JObject)records[int.Parse(parts[1])];
                string kind = (string)rec["kind"], key = (string)rec["key"], id = kind + ":" + key;
                if (!byKind.TryGetValue(kind, out var kindHolder))
                {
                    kindHolder = new GameObject(kind).transform;
                    kindHolder.SetParent(holder, false);
                    byKind[kind] = kindHolder;
                }
                var go = new GameObject(id);
                go.transform.SetPositionAndRotation(node.position, node.rotation);
                go.transform.SetParent(kindHolder, true);

                var pl = go.AddComponent<CampusPlacement>();
                pl.index = (int)rec["index"]; pl.kind = kind; pl.key = key;
                pl.species = (string)rec["species"];
                pl.heightM = rec["height_m"] != null ? (float)rec["height_m"] : 0f;
                pl.crownRadiusM = rec["crown_radius_m"] != null ? (float)rec["crown_radius_m"] : 0f;
                pl.trunkDiameterM = rec["trunk_diameter_m"] != null ? (float)rec["trunk_diameter_m"] : 0f;
                pl.sizeFrom = (string)rec["size_from"];

                var entry = dress.Find(id);
                var models = entry?.models?.Where(m => m != null).ToList();
                GameObject model;
                if (models != null && models.Count > 0)
                {
                    var prefab = models[rng.Next(models.Count)];
                    model = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                    model.transform.SetParent(go.transform, false);
                    model.transform.localRotation = Quaternion.Euler(0f, entry.yawOffset, 0f);
                    FitModel(model, entry, pl, rec);
                    pl.model = AssetDatabase.GetAssetPath(prefab);
                }
                else
                {
                    var proto = protos.Find("proto:" + id);
                    if (proto == null) { Object.DestroyImmediate(go); continue; }
                    model = Object.Instantiate(proto.gameObject, go.transform, false);
                    model.SetActive(true);
                    model.transform.localPosition = Vector3.zero;
                    model.transform.localRotation = Quaternion.identity;
                    var info = protoInfo[id] as JObject;
                    if (kind == "tree" && info?["height_m"] != null && pl.heightM > 0f)
                    {
                        float sh = pl.crownRadiusM / (float)info["crown_radius_m"], sv = pl.heightM / (float)info["height_m"];
                        model.transform.localScale = new Vector3(sh, sv, sh);
                    }
                    else if (key == "street_light" && info?["height_m"] != null && pl.heightM > 0f)
                        model.transform.localScale = Vector3.one * (pl.heightM / (float)info["height_m"]);
                    pl.model = "fallback";
                }
                model.name = "model";

                string cls = (string)rec["cls"] ?? (KindClass.TryGetValue(kind, out var kc) ? kc : key);
                Label(go, cls, kind, pl.model, classes);
                AddCollider(go, model, kind, cls, pl, classes, mats, dest);
                if (kind != "agent")
                    GameObjectUtility.SetStaticEditorFlags(go, StaticEditorFlags.BatchingStatic | StaticEditorFlags.OccludeeStatic);
            }
        }

        static void FitModel(GameObject model, DressMap.Entry entry, CampusPlacement pl, JObject rec)
        {
            var b = LocalBounds(model.transform.parent, model);
            if (b.size.y < 1e-3f) return;
            float targetH = pl.heightM > 0f ? pl.heightM : (rec["size_m"] is JArray s ? (float)s[2] : entry.refSizeM.y);
            switch (entry.fit)
            {
                case DressMap.Fit.AsAuthored:
                    break;
                case DressMap.Fit.MatchFootprint when pl.crownRadiusM > 0f:
                    float sh = 2f * pl.crownRadiusM / Mathf.Max(b.size.x, b.size.z);
                    float sv = targetH > 0f ? targetH / b.size.y : sh;
                    model.transform.localScale = new Vector3(sh, sv, sh);
                    break;
                default:
                    if (targetH > 0f) model.transform.localScale = Vector3.one * (targetH / b.size.y);
                    break;
            }
            var b2 = LocalBounds(model.transform.parent, model);           // stand it on the ground
            model.transform.localPosition += new Vector3(0f, -b2.min.y, 0f);
        }

        static Bounds LocalBounds(Transform frame, GameObject go)
        {
            var rs = go.GetComponentsInChildren<Renderer>(true);
            if (rs.Length == 0) return new Bounds(Vector3.zero, Vector3.zero);
            var b = new Bounds(frame.InverseTransformPoint(rs[0].bounds.center), Vector3.zero);
            foreach (var r in rs)
            {
                var w = r.bounds;
                for (int i = 0; i < 8; i++)
                {
                    var c = w.center + Vector3.Scale(w.extents, new Vector3((i & 1) == 0 ? -1 : 1, (i & 2) == 0 ? -1 : 1, (i & 4) == 0 ? -1 : 1));
                    b.Encapsulate(frame.InverseTransformPoint(c));
                }
            }
            return b;
        }

        static void AddCollider(GameObject go, GameObject model, string kind, string cls, CampusPlacement pl,
                                JObject classes, Dictionary<string, PhysicsMaterial> mats, string dest)
        {
            if (kind == "plant" || kind == "agent") return;          // ground cover is soft; agents move
            var mat = MaterialFor(cls, classes, mats, dest);
            if (kind == "tree")
            {
                var cap = go.AddComponent<CapsuleCollider>();
                float h = Mathf.Clamp(pl.heightM * 0.45f, 1.5f, 6f);
                cap.radius = Mathf.Max(pl.trunkDiameterM * 0.5f, 0.1f);
                cap.height = h; cap.center = new Vector3(0f, h * 0.5f, 0f);
                cap.sharedMaterial = mat;
                return;
            }
            var b = LocalBounds(go.transform, model);
            if (b.size == Vector3.zero) return;
            var box = go.AddComponent<BoxCollider>();
            box.center = b.center; box.size = b.size; box.sharedMaterial = mat;
        }

        // ---------------------------------------------------------------- route

        static void DrawRoute(CampusSite site, string networkPath)
        {
            if (!File.Exists(networkPath)) return;
            var doc = JObject.Parse(File.ReadAllText(networkPath));
            if (!(doc["routes"] is JObject routes)) return;
            var holder = new GameObject("Routes (planned)");
            holder.transform.SetParent(site.transform, true);
            holder.tag = "EditorOnly";
            var shader = Shader.Find("Universal Render Pipeline/Unlit") ?? Shader.Find("Sprites/Default");
            var ok = new Material(shader) { color = new Color(1f, 0.55f, 0f) };
            var blocked = new Material(shader) { color = new Color(0.9f, 0.1f, 0.1f) };
            foreach (var route in routes)
            {
                var rgo = new GameObject($"{route.Key}: {(string)route.Value["label"]}");
                rgo.transform.SetParent(holder.transform, false);
                foreach (var leg in (JArray)route.Value["legs"])
                {
                    var pts = new List<Vector3>();
                    foreach (var p in (JArray)leg["pts"] ?? new JArray())
                        pts.Add(site.FromExport(new Vector3((float)p[0], (float)p[1] + 0.3f, (float)p[2])));
                    if (pts.Count < 2) continue;
                    var go = new GameObject((string)leg["label"] ?? (string)leg["id"]);
                    go.transform.SetParent(rgo.transform, true);
                    var lr = go.AddComponent<LineRenderer>();
                    lr.useWorldSpace = true; lr.widthMultiplier = 0.4f;
                    lr.positionCount = pts.Count; lr.SetPositions(pts.ToArray());
                    lr.sharedMaterial = (bool?)leg["passable"] == false ? blocked : ok;
                }
            }
        }
    }
}

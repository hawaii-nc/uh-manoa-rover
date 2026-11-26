using System;
using System.Collections.Generic;
using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>Which material each semantic class of the campus meshes gets (walkway, road, grass,
    /// curb, ...). Empty entries keep the export's own material. The importer scales each material
    /// so one texture repeat covers <see cref="Entry.textureSizeM"/> real metres.</summary>
    [CreateAssetMenu(menuName = "Campus Rover/Material Map", fileName = "MaterialMap")]
    public class MaterialMap : ScriptableObject
    {
        [Serializable]
        public class Entry
        {
            [Tooltip("A class (walkway) or class|material from the export (walkway|ground_brick); the more specific entry wins")]
            public string className;
            [Tooltip("Number of mesh nodes with this class in the last import")]
            public int nodes;
            public Material material;
            [Tooltip("Real-world metres covered by one repeat of this material's textures")]
            public float textureSizeM = 2f;
            [Tooltip("Where the material/textures came from (URL)")]
            public string source;
            public string licence;
            [Tooltip("Licence limits use (engine-only, no redistribution, ...)")]
            public bool restricted;
        }

        public List<Entry> entries = new List<Entry>();

        public Entry Find(string cls)
        {
            foreach (var e in entries)
                if (e.className == cls) return e;
            return null;
        }

        public Entry GetOrAdd(string cls)
        {
            var e = Find(cls);
            if (e != null) return e;
            e = new Entry { className = cls };
            entries.Add(e);
            return e;
        }
    }
}

using System;
using System.Collections.Generic;
using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>Which model dresses each kind of placement (tree:umbrella, prop:bench, agent:person...).
    /// Empty entries use the fallback model from the export. Every model must name its source and
    /// licence; restricted licences are flagged so they can be swapped if training moves engines.</summary>
    [CreateAssetMenu(menuName = "Campus Rover/Dress Map", fileName = "DressMap")]
    public class DressMap : ScriptableObject
    {
        public enum Fit { MatchHeight, MatchFootprint, AsAuthored }

        [Serializable]
        public class Entry
        {
            [Tooltip("kind:key from placements.json, e.g. tree:umbrella")]
            public string id;
            public int count;
            [Tooltip("Several models are picked at random per placement, for variety")]
            public List<GameObject> models = new List<GameObject>();
            public Fit fit = Fit.MatchHeight;
            [Tooltip("Extra turn about the up axis if the model faces another way (degrees)")]
            public float yawOffset;
            [Tooltip("Where the model came from (URL)")]
            public string source;
            public string licence;
            [Tooltip("Licence limits use (engine-only, no redistribution, ...)")]
            public bool restricted;
            [Tooltip("Reference size from the export, for MatchHeight / MatchFootprint")]
            public float refHeightM;
            public float refCrownRadiusM;
            public Vector3 refSizeM;
        }

        public List<Entry> entries = new List<Entry>();

        public Entry Find(string id)
        {
            foreach (var e in entries)
                if (e.id == id) return e;
            return null;
        }

        public Entry GetOrAdd(string id)
        {
            var e = Find(id);
            if (e != null) return e;
            e = new Entry { id = id };
            entries.Add(e);
            return e;
        }
    }
}

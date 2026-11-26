using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>What a piece of the campus is, for labels and for the rover.
    /// Class names, ids and levels come from classes.json in the export (repo: traversal.py).</summary>
    public enum Traversability { Blocked = 0, Conditional = 1, Traversable = 2, Dynamic = 3 }

    [DisallowMultipleComponent]
    public class SemanticLabel : MonoBehaviour
    {
        [Tooltip("Semantic class, e.g. walkway, ramp, curb, building, tree")]
        public string className;
        [Tooltip("Class id from classes.json (0 = sky / nothing)")]
        public int classId;
        public Traversability level = Traversability.Blocked;
        [Tooltip("Mesh group from the export, e.g. ground, buildings/holmes_hall")]
        public string group;
        [Tooltip("Material name from the export")]
        public string sourceMaterial;

        public static Traversability ParseLevel(string level)
        {
            switch (level)
            {
                case "traversable": return Traversability.Traversable;
                case "conditional": return Traversability.Conditional;
                case "dynamic": return Traversability.Dynamic;
                default: return Traversability.Blocked;   // unknown is never free
            }
        }

        public static string LayerName(Traversability level) => level switch
        {
            Traversability.Traversable => "Traversable",
            Traversability.Conditional => "Conditional",
            Traversability.Dynamic => "Dynamic",
            _ => "Blocked",
        };
    }
}

using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>One placed thing from placements.json: a tree, plant, prop, sculpture or person.
    /// Position, heading and size come from the open data; the model on it can be swapped.</summary>
    public class CampusPlacement : MonoBehaviour
    {
        public int index;
        [Tooltip("tree, plant, prop, art or agent")]
        public string kind;
        [Tooltip("tree shape (umbrella, coconut, ...), plant kind, prop class, sculpture or agent class")]
        public string key;
        public string species;
        public float heightM;
        public float crownRadiusM;
        public float trunkDiameterM;
        [Tooltip("Where the size came from: surveyed, lidar or default")]
        public string sizeFrom;
        [Tooltip("The model in use: a prefab from the dress map, or the fallback prototype")]
        public string model;

        public string Id => kind + ":" + key;
    }
}

using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>Sits on the campus root. Holds site.json facts and maps the export frame
    /// (metres, x east, y up, z south, relative to the site origin) to this scene.</summary>
    public class CampusSite : MonoBehaviour
    {
        public string siteName;
        public string crs;
        [Tooltip("Site origin in the CRS (UTM metres for UH: EPSG:32604)")]
        public Vector3 originSite;
        public double latitude;
        public double longitude;
        [Tooltip("Hours from UTC (Honolulu: -10, no daylight saving)")]
        public float utcOffsetHours = -10f;
        public string builtOn;
        [Tooltip("network.json from the export: rover network, routes, landmarks")]
        public TextAsset network;
        [Tooltip("placements.json from the export")]
        public TextAsset placements;

        // Export frame -> this transform's local space, measured from the site|* markers.
        public Vector3 frameOrigin;
        public Vector3 frameEast = Vector3.right;
        public Vector3 frameUp = Vector3.up;
        public Vector3 frameSouth = Vector3.back;

        /// <summary>A point in the export frame ([x east, y up, z south] as in the JSON files)
        /// to a world position in this scene.</summary>
        public Vector3 FromExport(Vector3 p)
        {
            Vector3 local = frameOrigin + frameEast * p.x + frameUp * p.y + frameSouth * p.z;
            return transform.TransformPoint(local);
        }

        /// <summary>Site coordinates (CRS metres: east, north, height) to a world position.</summary>
        public Vector3 FromSite(double east, double north, double height)
        {
            return FromExport(new Vector3((float)(east - originSite.x), (float)(height - originSite.z),
                                          (float)-(north - originSite.y)));
        }
    }
}

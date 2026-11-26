using System;
using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>Sun position from latitude, longitude and local time (NOAA approximation,
    /// good to a fraction of a degree, which is plenty for shadows).</summary>
    public static class SolarPosition
    {
        /// <returns>(elevation, azimuth) in degrees; azimuth clockwise from north.</returns>
        public static Vector2 Compute(double latDeg, double lonDeg, DateTime local, double utcOffsetHours)
        {
            int day = local.DayOfYear;
            double hour = local.Hour + local.Minute / 60.0 + local.Second / 3600.0;
            double g = 2.0 * Math.PI / 365.0 * (day - 1 + (hour - 12.0) / 24.0);
            double eqTime = 229.18 * (0.000075 + 0.001868 * Math.Cos(g) - 0.032077 * Math.Sin(g)
                                      - 0.014615 * Math.Cos(2 * g) - 0.040849 * Math.Sin(2 * g));
            double decl = 0.006918 - 0.399912 * Math.Cos(g) + 0.070257 * Math.Sin(g)
                          - 0.006758 * Math.Cos(2 * g) + 0.000907 * Math.Sin(2 * g)
                          - 0.002697 * Math.Cos(3 * g) + 0.00148 * Math.Sin(3 * g);
            double timeOffset = eqTime + 4.0 * lonDeg - 60.0 * utcOffsetHours;
            double tst = hour * 60.0 + timeOffset;
            double ha = (tst / 4.0 - 180.0) * Math.PI / 180.0;
            double lat = latDeg * Math.PI / 180.0;
            double cosZen = Math.Sin(lat) * Math.Sin(decl) + Math.Cos(lat) * Math.Cos(decl) * Math.Cos(ha);
            cosZen = Math.Max(-1.0, Math.Min(1.0, cosZen));
            double zen = Math.Acos(cosZen);
            double az = Math.Atan2(Math.Sin(ha), Math.Cos(ha) * Math.Sin(lat) - Math.Tan(decl) * Math.Cos(lat));
            double azDeg = (az * 180.0 / Math.PI + 180.0) % 360.0;
            return new Vector2((float)(90.0 - zen * 180.0 / Math.PI), (float)azDeg);
        }

        /// <summary>Rotation for a directional light, in a scene where +Z is north and +X east.</summary>
        public static Quaternion LightRotation(float elevationDeg, float azimuthDeg)
        {
            float e = elevationDeg * Mathf.Deg2Rad, a = azimuthDeg * Mathf.Deg2Rad;
            Vector3 toSun = new Vector3(Mathf.Sin(a) * Mathf.Cos(e), Mathf.Sin(e), Mathf.Cos(a) * Mathf.Cos(e));
            return Quaternion.LookRotation(-toSun, Vector3.up);
        }
    }
}

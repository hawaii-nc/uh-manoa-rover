using System;
using System.Collections.Generic;
using UnityEngine;

namespace UHManoa.CampusRover
{
    /// <summary>Varies what the camera sees between training episodes (domain randomization):
    /// real Honolulu sun for a random date and hour, overcast, wet pavement, haze and how many
    /// people are out. Geometry never changes: only looks. Call Randomize() per episode.</summary>
    public class CampusRandomizer : MonoBehaviour
    {
        public CampusSite site;
        public Light sun;
        public int seed = 1;
        public bool randomizeOnStart = true;

        [Header("Time")]
        [Range(0f, 24f)] public float earliestHour = 7f;
        [Range(0f, 24f)] public float latestHour = 18f;

        [Header("Weather")]
        [Range(0f, 1f)] public float overcastChance = 0.35f;
        [Range(0f, 1f)] public float wetChance = 0.25f;
        [Range(0f, 0.05f)] public float maxFogDensity = 0.004f;

        [Header("People")]
        [Range(0f, 1f)] public float minPeopleShare = 0.2f;

        [Header("Last values (read only)")]
        public DateTime when;
        public float sunElevation, sunAzimuth;
        public bool overcast, wet;

        // Hard surfaces that look different wet: the rover drives on them.
        static readonly HashSet<string> Wettable = new HashSet<string>
            { "walkway", "ramp", "road", "road_marking", "curb", "stairs", "building_slab", "sports_court", "stepstone" };
        static readonly int BaseColor = Shader.PropertyToID("_BaseColor");
        static readonly int Smoothness = Shader.PropertyToID("_Smoothness");
        System.Random rng;
        MaterialPropertyBlock block;

        void Start() { if (randomizeOnStart) Randomize(); }

        [ContextMenu("Randomize")]
        public void Randomize()
        {
            rng ??= new System.Random(seed);
            block ??= new MaterialPropertyBlock();
            if (site == null) site = FindAnyObjectByType<CampusSite>();

            int day = rng.Next(1, 366);
            double hour = earliestHour + rng.NextDouble() * (latestHour - earliestHour);
            when = new DateTime(2026, 1, 1).AddDays(day - 1).AddHours(hour);
            overcast = rng.NextDouble() < overcastChance;
            wet = rng.NextDouble() < wetChance;

            if (sun != null && site != null && site.latitude != 0)
            {
                var sp = SolarPosition.Compute(site.latitude, site.longitude, when, site.utcOffsetHours);
                sunElevation = sp.x; sunAzimuth = sp.y;
                // The campus root is turned so +Z is north: rotate the sun with it.
                sun.transform.rotation = site.transform.rotation * SolarPosition.LightRotation(Mathf.Max(sp.x, 2f), sp.y);
                float daylight = Mathf.Clamp01(Mathf.Sin(Mathf.Max(sp.x, 0f) * Mathf.Deg2Rad) * 1.6f);
                sun.intensity = (overcast ? 0.35f : 1.0f) * Mathf.Lerp(0.2f, 1.3f, daylight) * Range(0.85f, 1.15f);
                sun.shadowStrength = overcast ? 0.25f : Range(0.8f, 1f);
                sun.colorTemperature = Mathf.Lerp(3200f, 6000f, daylight) + (overcast ? 800f : 0f);
                sun.useColorTemperature = true;
            }
            RenderSettings.ambientIntensity = (overcast ? 1.2f : 0.9f) * Range(0.85f, 1.15f);
            RenderSettings.fog = maxFogDensity > 0f;
            RenderSettings.fogDensity = Range(0f, maxFogDensity) * (overcast ? 1.5f : 1f);

            ApplyWet();
            ApplyPeople();
        }

        float Range(float a, float b) => a + (float)rng.NextDouble() * (b - a);

        void ApplyWet()
        {
            foreach (var label in FindObjectsByType<SemanticLabel>(FindObjectsSortMode.None))
            {
                if (!Wettable.Contains(label.className)) continue;
                foreach (var r in label.GetComponentsInChildren<Renderer>())
                {
                    r.GetPropertyBlock(block);
                    if (wet)
                    {
                        var m = r.sharedMaterial;
                        Color c = m != null && m.HasProperty(BaseColor) ? m.GetColor(BaseColor) : Color.gray;
                        block.SetColor(BaseColor, c * 0.7f);
                        block.SetFloat(Smoothness, 0.8f);
                    }
                    else block.Clear();
                    r.SetPropertyBlock(block);
                }
            }
        }

        void ApplyPeople()
        {
            float share = Range(minPeopleShare, 1f);
            foreach (var p in FindObjectsByType<CampusPlacement>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                if (p.kind == "agent") p.gameObject.SetActive(rng.NextDouble() < share);
        }
    }
}

"""
Generate a 30-second healthy ECG signal and export in two Apple Watch formats:

1. HKElectrocardiogram API format (developer view, with raw voltage data)
   - This is what an iOS app would receive via HealthKit when calling
     HKElectrocardiogramQuery.voltageMeasurements
   - 512 Hz sampling, 30 seconds, single-lead (Lead I equivalent)
   - Voltage in microvolts (uV)

2. Health.app XML export format (consumer view, no raw waveform)
   - This is what users get when they tap "Export Health Data" in Health.app
   - Classification result + average HR + metadata only
   - No voltage samples; this is the documented limitation
"""

import json
import numpy as np
import neurokit2 as nk
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ---------------------------------------------------------------------------
# 1. Generate healthy 30s ECG @ 512 Hz (Apple Watch native sampling rate)
# ---------------------------------------------------------------------------
SAMPLING_RATE = 512  # Hz, matches Apple Watch ECG app
DURATION_S = 30
HEART_RATE_BPM = 72  # healthy resting HR

ecg_clean = nk.ecg_simulate(
    duration=DURATION_S,
    sampling_rate=SAMPLING_RATE,
    heart_rate=HEART_RATE_BPM,
    method="ecgsyn",  # McSharry et al. realistic synthesis
    random_state=42,
)

# Apple Watch ECG voltage range is typically ±2 mV. NeuroKit2 outputs
# normalized values, so scale to a realistic millivolt range, then convert
# to microvolts (uV) — Apple's API uses HKUnit volts, but documented examples
# typically work in microvolts for readability.
ecg_mv = ecg_clean * 1.5  # scale to ~±1.5 mV peaks (typical Lead I R-wave)
ecg_uv = ecg_mv * 1000.0  # convert mV -> uV

# Compute average HR from the signal (sanity check)
peaks, info = nk.ecg_peaks(ecg_clean, sampling_rate=SAMPLING_RATE)
hr_array = nk.ecg_rate(peaks, sampling_rate=SAMPLING_RATE, desired_length=len(ecg_clean))
avg_hr = float(np.mean(hr_array))

# ---------------------------------------------------------------------------
# Common metadata
# ---------------------------------------------------------------------------
recording_start = datetime(2026, 4, 26, 9, 30, 0, tzinfo=timezone.utc)
recording_end = recording_start + timedelta(seconds=DURATION_S)

# ---------------------------------------------------------------------------
# 2. Format A: HKElectrocardiogram API (developer view, with raw voltage)
# ---------------------------------------------------------------------------
# This mirrors what an app receives when iterating
# HKElectrocardiogramQuery.voltageMeasurements. Each measurement has a
# timeSinceSampleStart and a voltage quantity.
hk_ecg_api = {
    "type": "HKElectrocardiogram",
    "metadata": {
        "HKMetadataKeyDeviceName": "Apple Watch",
        "HKMetadataKeyDeviceManufacturerName": "Apple Inc.",
        "HKMetadataKeyDeviceHardwareVersion": "Watch7,1",
        "HKMetadataKeyDeviceSoftwareVersion": "watchOS 10.4",
        "HKAlgorithmVersion": "1",
    },
    "startDate": recording_start.isoformat(),
    "endDate": recording_end.isoformat(),
    "samplingFrequency": {"value": SAMPLING_RATE, "unit": "Hz"},
    "classification": "sinusRhythm",  # HKElectrocardiogram.Classification
    "averageHeartRate": {"value": round(avg_hr, 1), "unit": "count/min"},
    "symptomsStatus": "none",  # HKElectrocardiogram.SymptomsStatus
    "numberOfVoltageMeasurements": len(ecg_uv),
    "lead": "appleWatchSimilarToLeadI",  # documented Apple lead designation
    # voltageMeasurements: array of {timeSinceSampleStart_s, voltage_uV}
    # Stored as parallel arrays for compactness — 15,360 samples otherwise
    # blow up the JSON.
    "voltageMeasurements": {
        "timeSinceSampleStart_s": [
            round(i / SAMPLING_RATE, 6) for i in range(len(ecg_uv))
        ],
        "voltage_uV": [round(float(v), 3) for v in ecg_uv],
    },
}

with open("/home/claude/apple_watch_ecg_api.json", "w") as f:
    json.dump(hk_ecg_api, f, indent=2)

# Also save a compact NumPy file for fast loading in the actual algorithm
np.savez(
    "/home/claude/apple_watch_ecg_api.npz",
    voltage_uV=ecg_uv.astype(np.float32),
    sampling_rate=SAMPLING_RATE,
    average_hr_bpm=avg_hr,
    start_date=recording_start.isoformat(),
)

# ---------------------------------------------------------------------------
# 3. Format B: Health.app XML export (consumer view, no waveform)
# ---------------------------------------------------------------------------
# This mirrors export.xml from "Export Health Data" in Health.app.
# Real exports contain many <Record> elements; we include just the relevant
# pieces: the ElectrocardiogramRecord (no voltage), an HKQuantityTypeIdentifier
# HeartRate sample for the same window, and the HRV SDNN companion value.
def hk_date(dt: datetime) -> str:
    # Health.app uses "YYYY-MM-DD HH:MM:SS ±ZZZZ"
    return dt.strftime("%Y-%m-%d %H:%M:%S +0000")


root = ET.Element("HealthData", attrib={"locale": "en_US"})
ET.SubElement(
    root,
    "ExportDate",
    attrib={"value": hk_date(datetime.now(timezone.utc))},
)
ET.SubElement(
    root,
    "Me",
    attrib={
        "HKCharacteristicTypeIdentifierBiologicalSex": "HKBiologicalSexMale",
        "HKCharacteristicTypeIdentifierDateOfBirth": "1995-01-01",
    },
)

# ECG record — NOTE: no voltage data here, this is the documented limitation
ecg_record = ET.SubElement(
    root,
    "ElectrocardiogramRecord",
    attrib={
        "type": "ElectrocardiogramType",
        "sourceName": "Apple Watch",
        "sourceVersion": "10.4",
        "device": "<<HKDevice: 0x...>, name:Apple Watch, manufacturer:Apple Inc., "
        "model:Watch, hardware:Watch7,1, software:10.4>",
        "creationDate": hk_date(recording_end),
        "startDate": hk_date(recording_start),
        "endDate": hk_date(recording_end),
        "classification": "SinusRhythm",
        "averageHeartRate": f"{avg_hr:.1f}",
        "samplingFrequency": str(SAMPLING_RATE),
        "numberOfVoltageMeasurements": str(len(ecg_uv)),
        "symptomsStatus": "None",
    },
)
# Health.app XML embeds an explanatory MetadataEntry rather than samples
ET.SubElement(
    ecg_record,
    "MetadataEntry",
    attrib={
        "key": "HKMetadataKeyAlgorithmVersion",
        "value": "1",
    },
)

# Companion HR sample (this DOES appear in real exports for the same window)
ET.SubElement(
    root,
    "Record",
    attrib={
        "type": "HKQuantityTypeIdentifierHeartRate",
        "sourceName": "Apple Watch",
        "unit": "count/min",
        "creationDate": hk_date(recording_end),
        "startDate": hk_date(recording_start),
        "endDate": hk_date(recording_end),
        "value": f"{avg_hr:.0f}",
    },
)

# Companion HRV SDNN sample (compute a quick SDNN proxy from the synthesized signal)
rr_intervals_s = np.diff(np.where(peaks["ECG_R_Peaks"] == 1)[0]) / SAMPLING_RATE
sdnn_ms = float(np.std(rr_intervals_s) * 1000.0)
ET.SubElement(
    root,
    "Record",
    attrib={
        "type": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
        "sourceName": "Apple Watch",
        "unit": "ms",
        "creationDate": hk_date(recording_end),
        "startDate": hk_date(recording_start),
        "endDate": hk_date(recording_end),
        "value": f"{sdnn_ms:.1f}",
    },
)

# Pretty print
xml_str = ET.tostring(root, encoding="unicode")
xml_pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
with open("/home/claude/apple_watch_export.xml", "w") as f:
    f.write(xml_pretty)

# ---------------------------------------------------------------------------
# 4. Print summary
# ---------------------------------------------------------------------------
print("=" * 62)
print("ECG generation complete")
print("=" * 62)
print(f"Source method      : NeuroKit2 ecgsyn (McSharry model)")
print(f"Duration           : {DURATION_S} s")
print(f"Sampling rate      : {SAMPLING_RATE} Hz")
print(f"Total samples      : {len(ecg_uv):,}")
print(f"Voltage range      : {ecg_uv.min():.1f} to {ecg_uv.max():.1f} uV")
print(f"Avg HR (computed)  : {avg_hr:.1f} bpm")
print(f"HRV SDNN           : {sdnn_ms:.1f} ms")
print(f"Classification     : sinusRhythm (healthy)")
print()
print("Files written:")
print("  /home/claude/apple_watch_ecg_api.json   (developer / HKElectrocardiogram)")
print("  /home/claude/apple_watch_ecg_api.npz    (compact NumPy for algorithm)")
print("  /home/claude/apple_watch_export.xml     (consumer / Health.app export)")

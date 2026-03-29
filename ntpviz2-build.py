#!/usr/bin/env python3
"""ntpviz2-build.py - Generate JSON data for ntpviz 2.0 interactive dashboard.

Reads NTPsec log files and produces downsampled JSON data files
suitable for rendering with uPlot in the browser.
"""

import argparse
import json
import os
import sys
import time

import numpy as np
from scipy import stats as scipy_stats

# NTPsec's own log parser
import ntp.statfiles


def parse_args():
    p = argparse.ArgumentParser(description="Generate ntpviz 2.0 JSON data")
    p.add_argument("-d", "--logdir", default="/var/log/ntpsec",
                   help="NTPsec stats directory")
    p.add_argument("-o", "--outdir", default="./ntpviz",
                   help="Output directory")
    p.add_argument("-p", "--periods", default="1,7",
                   help="Comma-separated periods in days (default: 1,7)")
    p.add_argument("-c", "--clip", action="store_true",
                   help="Clip stats to 1%%-99%% range")
    p.add_argument("-n", "--name", default=None,
                   help="Site name (default: hostname)")
    return p.parse_args()


def downsample(times, values, bucket_secs):
    """Downsample time series into fixed-width bucket averages.

    Args:
        times: numpy array of unix timestamps (seconds)
        values: numpy array of float values
        bucket_secs: bucket width in seconds

    Returns:
        (bucket_times, bucket_means) as numpy arrays
    """
    if len(times) == 0:
        return np.array([]), np.array([])

    bucket_ids = (times // bucket_secs).astype(np.int64)
    unique_buckets = np.unique(bucket_ids)

    out_times = np.empty(len(unique_buckets))
    out_values = np.empty(len(unique_buckets))

    for i, bid in enumerate(unique_buckets):
        mask = bucket_ids == bid
        out_times[i] = times[mask].mean()
        out_values[i] = values[mask].mean()

    return out_times, out_values


def auto_unit(values, is_freq=False):
    """Choose the best display unit and multiplier for a set of values.

    Returns (unit_label, multiplier) where display_value = raw_value * multiplier.
    """
    if len(values) == 0:
        return ("s", 1) if not is_freq else ("ppm", 1)

    p99 = np.percentile(np.abs(values), 99)

    if is_freq:
        # frequency: ppm or ppb
        if p99 < 0.001:
            return "ppb", 1e3  # ppm -> ppb
        return "ppm", 1
    else:
        # time: s, ms, us, ns
        if p99 < 1e-6:
            return "ns", 1e9
        if p99 < 1e-3:
            return "\u00b5s", 1e6
        if p99 < 1:
            return "ms", 1e3
        return "s", 1


def compute_stats(values, name, is_freq=False):
    """Compute summary statistics matching ntpviz summary.csv format."""
    if len(values) == 0:
        return None

    unit, mult = auto_unit(values, is_freq)
    v = values * mult

    percentiles = np.percentile(v, [0, 1, 5, 50, 95, 99, 100])
    result = {
        "name": name,
        "min": round(float(percentiles[0]), 4),
        "p1": round(float(percentiles[1]), 4),
        "p5": round(float(percentiles[2]), 4),
        "p50": round(float(percentiles[3]), 4),
        "p95": round(float(percentiles[4]), 4),
        "p99": round(float(percentiles[5]), 4),
        "max": round(float(percentiles[6]), 4),
        "range_90": round(float(percentiles[4] - percentiles[2]), 4),
        "range_98": round(float(percentiles[5] - percentiles[1]), 4),
        "stddev": round(float(np.std(v)), 4),
        "mean": round(float(np.mean(v)), 4),
        "unit": unit,
    }

    if len(values) > 2:
        result["skewness"] = round(float(scipy_stats.skew(v)), 4)
        result["kurtosis"] = round(float(scipy_stats.kurtosis(v, fisher=False)), 4)
    else:
        result["skewness"] = 0
        result["kurtosis"] = 0

    return result


def compute_histogram(values, n_bins=60):
    """Compute histogram bin edges and counts."""
    if len(values) == 0:
        return {"bin_edges": [], "counts": []}

    # Clip to 1%-99% for histogram range, but use all data for counts
    p1, p99 = np.percentile(values, [1, 99])
    counts, edges = np.histogram(values, bins=n_bins, range=(p1, p99))
    return {
        "bin_edges": [round(float(e), 10) for e in edges],
        "counts": [int(c) for c in counts],
    }


def round_list(arr, decimals=6):
    """Convert numpy array to list of rounded floats."""
    return [round(float(x), decimals) for x in arr]


def build_loopstats(stats, bucket_secs):
    """Build loopstats JSON from NTPStats object.

    loopstats row format after unixize:
        [ms_int, unix_str, offset, frequency, jitter, stability, ...]
    """
    if not stats.loopstats:
        return None

    rows = stats.loopstats
    times = np.array([float(r[1]) for r in rows])
    offset = np.array([float(r[2]) for r in rows])
    freq = np.array([float(r[3]) for r in rows])
    jitter = np.array([float(r[4]) for r in rows])
    stability = np.array([float(r[5]) for r in rows])

    t_o, d_offset = downsample(times, offset, bucket_secs)
    t_f, d_freq = downsample(times, freq, bucket_secs)
    t_j, d_jitter = downsample(times, jitter, bucket_secs)
    t_s, d_stab = downsample(times, stability, bucket_secs)

    return {
        "data": {
            "time": round_list(t_o, 1),
            "offset": round_list(d_offset, 10),
            "frequency": round_list(d_freq, 6),
            "jitter": round_list(d_jitter, 10),
            "stability": round_list(d_stab, 10),
        },
        "raw_count": len(rows),
    }


def build_peerstats(stats):
    """Build peerstats JSON from NTPStats object.

    Emits raw (non-downsampled) data so that short-lived spikes and noise
    remain visible — matching what the legacy gnuplot ntpviz shows.

    peerstats row format after unixize:
        [ms_int, unix_str, peer, status, offset, delay, dispersion, jitter]
    """
    peermap = stats.peersplit()
    if not peermap:
        return None

    peers = {}
    for peer_name, rows in peermap.items():
        label = ntp.statfiles.NTPStats.ip_label(peer_name)
        times = np.array([float(r[1]) for r in rows])
        offset = np.array([float(r[4]) for r in rows])
        jitter = np.array([float(r[7]) for r in rows])

        peers[label] = {
            "time": round_list(times, 1),
            "offset": round_list(offset, 10),
            "jitter": round_list(jitter, 10),
        }

    return {
        "peers": list(peers.keys()),
        "data": peers,
    }


def build_gpsd(stats):
    """Build GPS JSON from NTPStats object.

    gpsd row format after processing:
        [ms_int, unix_str, device, TDOP, nSat]
    """
    if not stats.gpsd:
        return None

    rows = stats.gpsd
    times = [round(float(r[1]), 1) for r in rows]
    tdop = [round(float(r[3]), 2) for r in rows]
    nsat = [int(float(r[4])) for r in rows]

    return {
        "time": times,
        "tdop": tdop,
        "nsat": nsat,
    }


def build_temps(stats):
    """Build temperature JSON from NTPStats object.

    temps row format after processing:
        [ms_int, unix_str, sensor, value]
    """
    tempsmap = stats.tempssplit()
    if not tempsmap:
        return None

    sensors = {}
    for sensor_name, rows in tempsmap.items():
        times = [round(float(r[1]), 1) for r in rows]
        values = [round(float(r[3]), 2) for r in rows]
        sensors[sensor_name] = {
            "time": times,
            "temperature": values,
        }

    return {
        "sensors": list(sensors.keys()),
        "data": sensors,
    }


def build_summary(stats, clip=False):
    """Build summary statistics JSON."""
    metrics = []

    # Loopstats metrics
    if stats.loopstats:
        rows = stats.loopstats
        offset = np.array([float(r[2]) for r in rows])
        freq = np.array([float(r[3]) for r in rows])
        jitter = np.array([float(r[4]) for r in rows])
        stability = np.array([float(r[5]) for r in rows])

        s = compute_stats(offset, "Local Clock Time Offset")
        if s:
            metrics.append(s)
        s = compute_stats(freq, "Local Clock Frequency Offset", is_freq=True)
        if s:
            metrics.append(s)
        s = compute_stats(jitter, "Local RMS Time Jitter")
        if s:
            metrics.append(s)
        s = compute_stats(stability, "Local RMS Frequency Jitter", is_freq=True)
        if s:
            metrics.append(s)

        # Histogram of time offset
        offset_unit, offset_mult = auto_unit(offset)
        histogram = compute_histogram(offset * offset_mult)
        histogram["unit"] = offset_unit
    else:
        histogram = {"bin_edges": [], "counts": [], "unit": "s"}

    # Peerstats metrics
    peermap = stats.peersplit()
    for peer_name, rows in peermap.items():
        label = ntp.statfiles.NTPStats.ip_label(peer_name)
        offset = np.array([float(r[4]) for r in rows])
        jitter = np.array([float(r[7]) for r in rows])

        s = compute_stats(offset, "Server Offset %s" % label)
        if s:
            metrics.append(s)
        s = compute_stats(jitter, "Server Jitter %s" % label)
        if s:
            metrics.append(s)

    # GPS metrics
    if stats.gpsd:
        rows = stats.gpsd
        tdop = np.array([float(r[3]) for r in rows])
        nsat = np.array([float(r[4]) for r in rows])

        s = compute_stats(tdop, "TDOP")
        if s:
            s["unit"] = " "
            metrics.append(s)
        s = compute_stats(nsat, "nSats")
        if s:
            s["unit"] = "nSat"
            metrics.append(s)

    # Temperature metrics
    tempsmap = stats.tempssplit()
    for sensor_name, rows in tempsmap.items():
        values = np.array([float(r[3]) for r in rows])
        s = compute_stats(values, "Temp %s" % sensor_name)
        if s:
            s["unit"] = "\u00b0C"
            metrics.append(s)

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics": metrics,
        "histogram": histogram,
    }


def write_json(filepath, data):
    """Write JSON with compact formatting."""
    with open(filepath, "w") as f:
        json.dump(data, f, separators=(",", ":"))


def main():
    args = parse_args()
    periods = [float(p) for p in args.periods.split(",")]

    for period in periods:
        period_secs = int(period * 86400)
        label = "%dd" % int(period) if period == int(period) else "%.1fd" % period

        sys.stderr.write("Building %s data...\n" % label)

        stats = ntp.statfiles.NTPStats(
            statsdir=args.logdir,
            sitename=args.name,
            period=period_secs,
        )

        # Choose bucket size: 30s for 1 day, 120s for 7 days
        bucket_secs = 30 if period <= 1 else 120

        outdir = os.path.join(args.outdir, "data", label)
        os.makedirs(outdir, exist_ok=True)

        # Build and write each JSON file
        loopdata = build_loopstats(stats, bucket_secs)
        if loopdata:
            write_json(os.path.join(outdir, "loopstats.json"), loopdata)

        peerdata = build_peerstats(stats)
        if peerdata:
            write_json(os.path.join(outdir, "peerstats.json"), peerdata)

        gpsdata = build_gpsd(stats)
        if gpsdata:
            write_json(os.path.join(outdir, "gpsd.json"), gpsdata)

        tempsdata = build_temps(stats)
        if tempsdata:
            write_json(os.path.join(outdir, "temps.json"), tempsdata)

        summary = build_summary(stats, clip=args.clip)
        summary["period_days"] = period
        summary["sitename"] = stats.sitename
        write_json(os.path.join(outdir, "summary.json"), summary)

        sys.stderr.write("  -> %s (%d files)\n" % (outdir, len(os.listdir(outdir))))

    sys.stderr.write("Done.\n")


if __name__ == "__main__":
    main()

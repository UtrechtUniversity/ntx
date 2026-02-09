"""
Static metadata for Axion neural metrics.

This maps Axion CSV headers to standardized parameter names, groups parameters into
high-level sections for use in the UI, and lists the Axion columns we intentionally
ignore during ingestion. Network burst ratio lives in the Network Burst section;
the baseline count used for QC masking is tracked separately.
"""

from __future__ import annotations

AXION_METRICS_MAP: dict[str, str] = {
    # Activity
    "Number of Spikes": "number_of_spikes",
    "Mean Firing Rate (Hz)": "mean_firing_rate",
    "ISI Coefficient of Variation - Avg": "isi_coefficient_of_variation",
    "ISI Coefficient of Variation": "isi_coefficient_of_variation",
    "Number of Active Electrodes": "number_of_active_electrodes",
    "Weighted Mean Firing Rate (Hz)": "weighted_mean_firing_rate",
    "Burst Peak (Max Spikes per sec)": "burst_peak",
    "Time to Burst Peak (ms)": "time_to_burst_peak",
    # Electrode Burst
    "Number of Bursts": "number_of_bursts",
    "Number of Bursting Electrodes": "number_of_bursting_electrodes",
    "Burst Duration - Avg (sec)": "burst_duration",
    "Number of Spikes per Burst - Avg": "spikes_per_burst",
    "Mean ISI within Burst - Avg (sec)": "mean_isi_within_burst",
    "Median ISI within Burst - Avg (sec)": "median_isi_within_burst",
    "Median/Mean ISI within Burst - Avg": "median_mean_isi_ratio",
    "Median/Mean ISI within Burst": "median_mean_isi_ratio",
    "Inter-Burst Interval - Avg (sec)": "inter_burst_interval",
    "Burst Frequency - Avg (Hz)": "burst_frequency",
    "IBI Coefficient of Variation - Avg": "ibi_coefficient_of_variation",
    "IBI Coefficient of Variation": "ibi_coefficient_of_variation",
    "Burst Percentage - Avg": "burst_percentage",
    "Burst Percentage": "burst_percentage",
    "Burst Frequency (Hz)": "burst_frequency",
    # Network Burst
    "Number of Network Bursts": "number_of_network_bursts",
    "Network Burst Frequency": "network_burst_frequency",
    "Network Burst Duration - Avg (sec)": "network_burst_duration",
    "Number of Spikes per Network Burst - Avg": "spikes_per_network_burst",
    "Mean ISI within Network Burst - Avg (sec)": "mean_isi_within_network_burst",
    "Median ISI within Network Burst - Avg (sec)": "median_isi_within_network_burst",
    "Median/Mean ISI within Network Burst - Avg": "network_median_mean_isi_ratio",
    "Number of Elecs Participating in Burst - Avg": "electrodes_per_network_burst",
    "Number of Spikes per Network Burst per Channel - Avg": "spikes_per_network_burst_per_channel",
    "Network Burst Percentage": "network_burst_percentage",
    "Network IBI Coefficient of Variation": "network_ibi_coefficient_of_variation",
    "Network Normalized Duration IQR": "network_normalized_duration_iqr",
    # Synchrony
    "Area Under Normalized Cross-Correlation": "area_under_normalized_cross_correlation",
    "Area Under Cross-Correlation": "area_under_cross_correlation",
    "Full Width at Half Height of Normalized Cross-Correlation": "fwhh_normalized_cross_correlation",  # noqa: E501
    "Full Width at Half Height of Cross-Correlation": "fwhh_cross_correlation",
}

# Baseline QC metrics stored in `qc_json` (absolute counts).
QC_BASELINE_METRICS: set[str] = {
    "number_of_active_electrodes",
    "number_of_bursting_electrodes",
    "number_network_bursts_baseline",
}

# Used by UI/reporting to group parameters together.
METRIC_SECTIONS: dict[str, list[str]] = {
    "Activity": [
        "number_of_spikes",
        "mean_firing_rate",
        "isi_coefficient_of_variation",
        "weighted_mean_firing_rate",
        "burst_peak",
        "time_to_burst_peak",
    ],
    "Electrode Burst": [
        "number_of_bursts",
        "burst_duration",
        "spikes_per_burst",
        "mean_isi_within_burst",
        "median_isi_within_burst",
        "median_mean_isi_ratio",
        "inter_burst_interval",
        "burst_frequency",
        "ibi_coefficient_of_variation",
        "burst_percentage",
    ],
    "Network Burst": [
        "number_of_network_bursts",
        "network_burst_frequency",
        "network_burst_duration",
        "spikes_per_network_burst",
        "mean_isi_within_network_burst",
        "median_isi_within_network_burst",
        "network_median_mean_isi_ratio",
        "electrodes_per_network_burst",
        "spikes_per_network_burst_per_channel",
        "network_burst_percentage",
        "network_ibi_coefficient_of_variation",
        "network_normalized_duration_iqr",
    ],
    "Synchrony": [
        "area_under_normalized_cross_correlation",
        "area_under_cross_correlation",
        "fwhh_normalized_cross_correlation",
        "fwhh_cross_correlation",
    ],
    "QC": [
        "number_of_active_electrodes",
        "number_of_bursting_electrodes",
        "number_network_bursts_baseline",
    ],
}

# Columns present in Axion exports which are dropped.
IGNORED_METRICS: set[str] = {
    "Treatment/ID",
    "Start Electrode",
    "Percent Bursts with Start Electrode",
    # Standard deviation columns we do not store
    "Burst Duration - Std (sec)",
    "Number of Spikes per Burst - Std",
    "Mean ISI within Burst - Std (sec)",
    "Median ISI within Burst - Std (sec)",
    "Median/Mean ISI within Burst - Std",
    "Inter-Burst Interval - Std (sec)",
    "Burst Frequency - Std (Hz)",
    "IBI Coefficient of Variation - Std",
    "Burst Percentage - Std",
    "Network Burst Duration - Std (sec)",
    "Number of Spikes per Network Burst - Std",
    "Mean ISI within Network Burst - Std (sec)",
    "Median ISI within Network Burst - Std (sec)",
    "Median/Mean ISI within Network Burst - Std",
    "Number of Elecs Participating in Burst - Std",
    "Number of Spikes per Network Burst per Channel - Std",
}

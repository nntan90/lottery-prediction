"""
model_cyclic.py — Model G: Cyclic Pattern Detector via FFT (XSMB v4)

Concept:
  - Fourier analysis: FFT trên chuỗi appearance (0/1) mỗi pair
    Tìm dominant frequency → chu kỳ lặp lại
    VD: Pair 42 xuất hiện mỗi ~12 kỳ (1 lần/2 tuần)
  - Autocorrelation: ACF tại lag 1..30
    Peak at lag=7 → pair có weekly pattern
    Peak at lag=14 → biweekly pattern
  - Phase estimation: pair đang ở pha nào trong chu kỳ
    Phase approaching peak → score cao
  - Multi-harmonic: kết hợp top-3 dominant frequencies

Dependencies: numpy (FFT built-in), scipy optional
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional

from src.xsmb_ensemble.data_utils import (
    _load_tails_by_draws,
    compute_pair_appeared_matrix,
)


def _find_dominant_cycles(
    signal: np.ndarray,
    min_period: int = 3,
    max_period: int = 60,
    top_k: int = 3,
) -> list[tuple[int, float]]:
    """
    Tìm top-K dominant cycles trong signal bằng FFT.

    Args:
        signal: 1D array (appearance 0/1 per draw)
        min_period: chu kỳ tối thiểu (3 kỳ)
        max_period: chu kỳ tối đa (60 kỳ = ~2 tháng)
        top_k: số chu kỳ mạnh nhất

    Returns:
        List of (period, power) sorted by power descending
        period = số kỳ cho 1 chu kỳ (VD: 7 = weekly)
    """
    n = len(signal)
    if n < min_period * 2:
        return []

    # Remove mean (detrend)
    signal_centered = signal - signal.mean()

    # FFT
    fft_result = np.fft.rfft(signal_centered)
    power = np.abs(fft_result) ** 2
    freqs = np.fft.rfftfreq(n)

    # Convert to periods
    results = []
    for i in range(1, len(freqs)):
        if freqs[i] > 0:
            period = int(round(1.0 / freqs[i]))
            if min_period <= period <= max_period:
                results.append((period, float(power[i])))

    # Deduplicate by period (keep strongest)
    period_power = {}
    for period, pwr in results:
        if period not in period_power or pwr > period_power[period]:
            period_power[period] = pwr

    sorted_cycles = sorted(period_power.items(), key=lambda x: x[1], reverse=True)
    return sorted_cycles[:top_k]


def _compute_autocorrelation(
    signal: np.ndarray,
    max_lag: int = 30,
) -> np.ndarray:
    """
    Compute autocorrelation function (ACF) cho signal.

    Returns:
        np.ndarray shape (max_lag+1,) — ACF at lag 0..max_lag
    """
    n = len(signal)
    if n < max_lag + 1:
        max_lag = n - 1

    mean = signal.mean()
    var = signal.var()
    if var < 1e-10:
        return np.zeros(max_lag + 1)

    acf = np.zeros(max_lag + 1)
    for lag in range(max_lag + 1):
        if lag >= n:
            break
        cov = np.mean((signal[:n - lag] - mean) * (signal[lag:] - mean))
        acf[lag] = cov / var

    return acf


def _estimate_phase_score(
    signal: np.ndarray,
    cycles: list[tuple[int, float]],
) -> float:
    """
    Ước lượng pair đang ở pha nào trong dominant cycle.

    Score cao khi pair đang tiếp cận peak (sắp xuất hiện theo chu kỳ).

    Logic:
      - Với mỗi dominant cycle period T:
        - Tìm vị trí cuối cùng pair xuất hiện
        - Tính phase = (gap_since_last / T) % 1.0
        - Phase ≈ 0.8-1.0 → sắp peak → score cao
        - Phase ≈ 0.0-0.2 → vừa peak → score thấp

    Returns:
        float [0, 1] — phase score, cao = sắp peak
    """
    n = len(signal)
    if not cycles or n < 5:
        return 0.5

    # Find gap since last appearance
    positions = np.where(signal > 0)[0]
    if len(positions) == 0:
        gap_since_last = n
    else:
        gap_since_last = n - 1 - positions[-1]

    total_power = sum(pwr for _, pwr in cycles)
    if total_power < 1e-10:
        return 0.5

    weighted_phase_score = 0.0
    for period, power in cycles:
        weight = power / total_power

        # Phase in cycle: 0.0 = just appeared, 1.0 = about to appear
        phase = (gap_since_last % period) / period

        # Phase scoring: highest score when phase ≈ 0.8-1.0 (approaching peak)
        # Bell curve centered at 0.9
        # Using cos function: cos(2π(phase - 0.9)) mapped to [0, 1]
        phase_score = (np.cos(2 * np.pi * (phase - 0.9)) + 1) / 2

        weighted_phase_score += weight * phase_score

    return float(np.clip(weighted_phase_score, 0.0, 1.0))


def predict_cyclic(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 5,
    region: str = "XSMB",
) -> Dict:
    """
    Model G: Cyclic Pattern Detector cho XSMB.

    Kết hợp 3 signals:
      1. FFT dominant cycle phase (0.40): pair đang ở pha nào
      2. ACF peak strength (0.30): autocorrelation evidence
      3. Gap-to-cycle ratio (0.30): gap / dominant_period → overdue trong cycle

    Args:
        db: LotteryDB instance
        province: None cho XSMB
        target_date: ngày predict
        n_draws: lookback (cần ≥60 kỳ cho FFT meaningful)
        top_n: top-N output
        region: 'XSMB'

    Returns:
        Dict with model_name, top_pairs, status, etc.
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 30:
            return _error_result("cyclic", province, n,
                                 f"Không đủ lịch sử cho FFT: {n} kỳ (cần ≥ 30)", start_ms)

        appeared = compute_pair_appeared_matrix(history)  # (n, 100)

        scores = np.zeros(100, dtype=float)

        for pair in range(100):
            signal = appeared[:, pair]

            # Skip pair chưa bao giờ xuất hiện
            if signal.sum() < 2:
                scores[pair] = 0.0
                continue

            # ── 1. FFT: dominant cycles + phase estimation ──
            cycles = _find_dominant_cycles(signal, min_period=3, max_period=min(60, n // 2), top_k=3)
            phase_score = _estimate_phase_score(signal, cycles)

            # ── 2. ACF: autocorrelation peak strength ──
            acf = _compute_autocorrelation(signal, max_lag=min(30, n // 3))
            # Find strongest ACF peak (excluding lag=0)
            if len(acf) > 1:
                acf_peaks = acf[1:]  # exclude lag 0
                acf_max = float(np.max(acf_peaks))
                # Normalize to [0, 1]: ACF > 0.3 is strong
                acf_score = min(max(acf_max, 0.0) / 0.3, 1.0)
            else:
                acf_score = 0.0

            # ── 3. Gap-to-cycle ratio ──
            # Nếu gap hiện tại ≈ dominant period → sắp tái xuất hiện
            positions = np.where(signal > 0)[0]
            if len(positions) > 0 and cycles:
                gap = n - 1 - positions[-1]
                dominant_period = cycles[0][0]  # strongest cycle period
                # Ratio ≈ 1.0 → gap vừa đúng 1 chu kỳ → high score
                ratio = gap / (dominant_period + 1e-6)
                # Score highest when ratio ∈ [0.8, 1.2]
                if 0.8 <= ratio <= 1.2:
                    gap_cycle_score = 1.0
                elif 0.6 <= ratio <= 1.5:
                    gap_cycle_score = 0.7
                elif ratio > 1.5:
                    gap_cycle_score = 0.5  # overdue beyond cycle
                else:
                    gap_cycle_score = 0.3  # too recent
            else:
                gap_cycle_score = 0.3

            # ── Composite ──
            scores[pair] = (
                phase_score      * 0.40 +
                acf_score        * 0.30 +
                gap_cycle_score  * 0.30
            )

        # Min-max normalize to [0, 1]
        s_min, s_max = scores.min(), scores.max()
        if s_max - s_min > 1e-8:
            scores = (scores - s_min) / (s_max - s_min)

        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "cyclic",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("cyclic", province, 0, str(e), start_ms)


def _error_result(model_name: str, province: Optional[str],
                  n_draws: int, error_msg: str, start_ms: float) -> Dict:
    """Helper tạo error result dict."""
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }

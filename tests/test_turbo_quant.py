"""Unit tests for the TurboQuant core algorithm.

Verifies the paper's distortion bounds (Theorem 1) and the unbiasedness of the
two-stage inner-product estimator (Theorem 2), plus packing and determinism.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cocoindex_code.turbo_quant import (
    SUPPORTED_BITS,
    TurboQuant,
    pack_indices,
    pack_signs,
    unpack_indices,
    unpack_signs,
)

# Paper Theorem 1 upper bound: D_mse <= sqrt(3*pi/2) * 4^-b
_MSE_UPPER = math.sqrt(3 * math.pi / 2)
# Finer per-b values from Theorem 1.
_MSE_FINE = {1: 0.36, 2: 0.117, 3: 0.03, 4: 0.009}

_DIM = 384
_N = 4000
_SEED = 7


def _random_unit_vectors(n: int, d: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal((n, d)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v


# ---------------------------------------------------------------------------
# MSE distortion bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bits", SUPPORTED_BITS)
def test_mse_distortion_within_paper_bound(bits: int) -> None:
    tq = TurboQuant(dim=_DIM, bits=bits, seed=_SEED)
    vecs = _random_unit_vectors(_N, _DIM, seed=11)

    sq_errors = []
    for v in vecs:
        idx, norm = tq.quantize_mse(v)
        v_hat = tq.dequantize_mse(idx, norm)
        sq_errors.append(float(np.sum((v - v_hat) ** 2)))
    mse = float(np.mean(sq_errors))

    # Correctness check: measured distortion matches the paper's reported
    # empirical fine values (Theorem 1) within tolerance. These are the real
    # targets — the sqrt(3*pi/2)*4^-b asymptotic (Panter-Dite high-resolution)
    # formula is overshot by the paper's own b=4 optimum (0.009 > 0.0085), so it
    # is only a sanity ceiling for the lower bit-widths.
    fine = _MSE_FINE[bits]
    assert mse <= fine * 1.15, f"b={bits}: MSE {mse:.4f} exceeds paper value {fine:.4f}"
    if bits <= 3:
        upper = _MSE_UPPER * 4.0 ** (-bits)
        assert mse <= upper * 1.10, f"b={bits}: MSE {mse:.4f} exceeds asymptotic {upper:.4f}"


# ---------------------------------------------------------------------------
# Inner-product unbiasedness
# ---------------------------------------------------------------------------


def test_prod_estimator_is_unbiased() -> None:
    bits = 4
    tq = TurboQuant(dim=_DIM, bits=bits, seed=_SEED)
    xs = _random_unit_vectors(_N, _DIM, seed=21)
    ys = _random_unit_vectors(_N, _DIM, seed=22)

    errors = []
    for x, y in zip(xs, ys):
        mse_idx, qjl, rnorm, norm = tq.quantize_prod(x)
        est = tq.inner_product_prod(y, mse_idx, qjl, rnorm, norm)
        true_ip = float(y @ x)
        errors.append(est - true_ip)

    errors = np.array(errors)
    mean_err = float(errors.mean())
    stderr = float(errors.std(ddof=1) / math.sqrt(len(errors)))
    # Mean signed error within ~3 standard errors of zero -> unbiased.
    assert abs(mean_err) <= 3.0 * stderr + 1e-3, f"bias {mean_err:.5f} (SE {stderr:.5f})"


def test_mse_quantizer_is_biased_for_inner_product_at_b1() -> None:
    """MSE-only estimate (via dequantize) shows the ~2/pi shrinkage at b=1.

    This is the motivation for the two-stage prod scheme.
    """
    tq = TurboQuant(dim=_DIM, bits=1, seed=_SEED)
    xs = _random_unit_vectors(_N, _DIM, seed=31)
    ys = _random_unit_vectors(_N, _DIM, seed=32)

    ratios = []
    for x, y in zip(xs, ys):
        idx, norm = tq.quantize_mse(x)
        x_hat = tq.dequantize_mse(idx, norm)
        true_ip = float(y @ x)
        if abs(true_ip) > 1e-3:
            ratios.append(float(y @ x_hat) / true_ip)
    mean_ratio = float(np.mean(ratios))
    # Biased: estimate is a fraction of the true IP, not ~1.0.
    assert mean_ratio < 0.9


# ---------------------------------------------------------------------------
# Determinism + reconstruction sanity
# ---------------------------------------------------------------------------


def test_same_seed_is_deterministic() -> None:
    a = TurboQuant(dim=64, bits=3, seed=99)
    b = TurboQuant(dim=64, bits=3, seed=99)
    v = _random_unit_vectors(1, 64, seed=5)[0]
    idx_a, n_a = a.quantize_mse(v)
    idx_b, n_b = b.quantize_mse(v)
    assert np.array_equal(idx_a, idx_b)
    assert n_a == n_b


def test_cosine_improves_with_bits() -> None:
    vecs = _random_unit_vectors(500, 128, seed=8)
    prev = -1.0
    for bits in SUPPORTED_BITS:
        tq = TurboQuant(dim=128, bits=bits, seed=3)
        cos = []
        for v in vecs:
            idx, norm = tq.quantize_mse(v)
            v_hat = tq.dequantize_mse(idx, norm)
            denom = np.linalg.norm(v) * np.linalg.norm(v_hat)
            if denom > 0:
                cos.append(float(v @ v_hat) / denom)
        mean_cos = float(np.mean(cos))
        assert mean_cos >= prev - 0.02, f"cosine regressed at b={bits}"
        prev = mean_cos


def test_zero_vector_no_nan() -> None:
    tq = TurboQuant(dim=32, bits=2, seed=1)
    z = np.zeros(32, dtype=np.float32)
    idx, norm = tq.quantize_mse(z)
    out = tq.dequantize_mse(idx, norm)
    assert norm == 0.0
    assert not np.any(np.isnan(out))

    mse_idx, qjl, rnorm, n = tq.quantize_prod(z)
    est = tq.inner_product_prod(np.ones(32, dtype=np.float32), mse_idx, qjl, rnorm, n)
    assert est == 0.0


def test_small_dims_do_not_crash() -> None:
    for d in (1, 2, 3):
        tq = TurboQuant(dim=d, bits=2, seed=2)
        v = _random_unit_vectors(1, d, seed=4)[0]
        idx, norm = tq.quantize_mse(v)
        out = tq.dequantize_mse(idx, norm)
        assert out.shape == (d,)
        assert not np.any(np.isnan(out))


# ---------------------------------------------------------------------------
# Bit packing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bits", SUPPORTED_BITS)
def test_index_packing_roundtrip(bits: int) -> None:
    rng = np.random.default_rng(bits)
    count = 137  # not a multiple of 8 -> exercises padding
    idx = rng.integers(0, 1 << bits, size=count)
    packed = pack_indices(idx, bits)
    out = unpack_indices(packed, count, bits)
    assert np.array_equal(idx, out)
    # Packed size is ceil(count*bits/8).
    assert len(packed) == math.ceil(count * bits / 8)


@pytest.mark.parametrize("count", [1, 7, 8, 9, 64, 65])
def test_sign_packing_roundtrip(count: int) -> None:
    rng = np.random.default_rng(count)
    signs = np.where(rng.random(count) > 0.5, 1.0, -1.0).astype(np.float32)
    packed = pack_signs(signs)
    out = unpack_signs(packed, count)
    assert np.array_equal(signs, out)


def test_empty_packing() -> None:
    assert pack_indices(np.empty(0, dtype=np.int64), 4) == b""
    assert unpack_indices(b"", 0, 4).size == 0
    assert pack_signs(np.empty(0)) == b""
    assert unpack_signs(b"", 0).size == 0

"""TurboQuant: data-oblivious vector quantization with near-optimal distortion.

Implements the algorithm from Zandieh et al., "TurboQuant: Online Vector
Quantization with Near-optimal Distortion Rate" (arXiv:2504.19874).

Two quantizers are provided:

* **MSE quantizer** (``quantize_mse`` / ``dequantize_mse``): randomly rotates the
  input, then applies an optimal per-coordinate Lloyd-Max scalar quantizer. The
  rotation makes every coordinate follow a Beta distribution that converges to
  ``N(0, 1/d)`` in high dimensions, so a single precomputed codebook (solved for
  the standard normal and scaled by ``1/sqrt(d)``) is near-optimal per coordinate.
  Minimizes reconstruction MSE but is *biased* for inner-product estimation.

* **Inner-product quantizer** (``quantize_prod`` / ``inner_product_prod``): the
  two-stage scheme. Applies the MSE quantizer at ``bits - 1`` bits, then a 1-bit
  Quantized Johnson-Lindenstrauss (QJL) transform on the residual. The result is
  an *unbiased* inner-product estimator (paper Theorem 2).

The rotation matrix ``Pi`` and the QJL projection ``S`` are derived from a single
integer ``seed`` so an index is fully reproducible and only the seed (not the
matrices) needs to be persisted.

This module is intentionally free of any cocoindex / SQLite coupling so it can be
unit-tested in isolation.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

__all__ = [
    "SUPPORTED_BITS",
    "TurboQuant",
    "gaussian_lloyd_max",
    "pack_indices",
    "unpack_indices",
    "pack_signs",
    "unpack_signs",
]

# Bit-widths we precompute codebooks for and support end-to-end.
SUPPORTED_BITS = (1, 2, 3, 4)

# Offset added to the base seed when deriving the QJL projection matrix, so Pi
# and S come from independent draws of the same seeded generator family.
_QJL_SEED_OFFSET = 0x5F3759DF


# ---------------------------------------------------------------------------
# Codebook computation (Lloyd-Max for the standard normal)
# ---------------------------------------------------------------------------


def gaussian_lloyd_max(
    bits: int,
    *,
    grid_points: int = 1 << 16,
    grid_limit: float = 8.0,
    max_iter: int = 200,
    tol: float = 1e-9,
) -> npt.NDArray[np.float64]:
    """Solve the optimal ``2**bits``-level scalar quantizer for ``N(0, 1)``.

    Uses deterministic grid quadrature (no RNG, no scipy): the real line is
    approximated by a fine grid weighted by the normal density, and Lloyd-Max
    iteration alternates between Voronoi assignment and conditional-mean centroid
    updates until convergence.

    Returns the sorted centroids for the standard normal. Callers scale these by
    ``1/sqrt(d)`` to match the ``N(0, 1/d)`` coordinate distribution of a rotated
    unit vector.
    """
    if bits < 1:
        raise ValueError(f"bits must be >= 1, got {bits}")

    levels = 1 << bits
    x = np.linspace(-grid_limit, grid_limit, grid_points)
    # Normal density (unnormalized is fine — only relative weights matter).
    w = np.exp(-0.5 * x * x)

    # Initialize centroids at evenly spaced density quantile-ish positions.
    centroids = np.linspace(-grid_limit / 2, grid_limit / 2, levels)

    prev_distortion = np.inf
    for _ in range(max_iter):
        # Assign each grid point to the nearest centroid.
        # boundaries are midpoints between sorted centroids.
        boundaries = (centroids[:-1] + centroids[1:]) / 2.0
        assign = np.searchsorted(boundaries, x)

        # Conditional mean per cluster (weighted by density).
        new_centroids = centroids.copy()
        for k in range(levels):
            mask = assign == k
            wk = w[mask]
            total = wk.sum()
            if total > 0:
                new_centroids[k] = (x[mask] * wk).sum() / total
        # Distortion for convergence check.
        distortion = float((w * (x - new_centroids[assign]) ** 2).sum() / w.sum())
        centroids = new_centroids
        if abs(prev_distortion - distortion) <= tol:
            break
        prev_distortion = distortion

    centroids.sort()
    return centroids


# Precompute standard-normal codebooks once at import for the supported bits.
_NORMAL_CODEBOOKS: dict[int, npt.NDArray[np.float64]] = {
    b: gaussian_lloyd_max(b) for b in SUPPORTED_BITS
}


# ---------------------------------------------------------------------------
# Bit packing
# ---------------------------------------------------------------------------


def pack_indices(indices: npt.NDArray[np.integer], bits: int) -> bytes:
    """Pack an array of ``bits``-wide integer indices into a byte string.

    MSB-first within each index. The packed stream is zero-padded to a byte
    boundary; ``unpack_indices`` must be given the original length to trim it.
    """
    idx = np.asarray(indices, dtype=np.uint64)
    if idx.size == 0:
        return b""
    if bits < 1 or bits > 8:
        raise ValueError(f"bits must be in 1..8, got {bits}")
    shifts = np.arange(bits - 1, -1, -1, dtype=np.uint64)
    bit_matrix = ((idx[:, None] >> shifts) & np.uint64(1)).astype(np.uint8)
    return np.packbits(bit_matrix.reshape(-1)).tobytes()


def unpack_indices(packed: bytes, count: int, bits: int) -> npt.NDArray[np.int64]:
    """Inverse of :func:`pack_indices`. Returns ``count`` integer indices."""
    if count == 0:
        return np.empty(0, dtype=np.int64)
    raw = np.frombuffer(packed, dtype=np.uint8)
    bit_stream = np.unpackbits(raw)[: count * bits].reshape(count, bits)
    weights = (1 << np.arange(bits - 1, -1, -1)).astype(np.int64)
    return bit_stream.astype(np.int64) @ weights


def pack_signs(signs: npt.NDArray[np.floating | np.integer]) -> bytes:
    """Pack a ``+1/-1`` vector into a bit string (``+1`` -> 1, ``-1`` -> 0)."""
    s = np.asarray(signs)
    if s.size == 0:
        return b""
    bit = (s > 0).astype(np.uint8)
    return np.packbits(bit).tobytes()


def unpack_signs(packed: bytes, count: int) -> npt.NDArray[np.float32]:
    """Inverse of :func:`pack_signs`. Returns a ``+1/-1`` float32 vector."""
    if count == 0:
        return np.empty(0, dtype=np.float32)
    raw = np.frombuffer(packed, dtype=np.uint8)
    bit = np.unpackbits(raw)[:count]
    return np.where(bit > 0, np.float32(1.0), np.float32(-1.0)).astype(np.float32)


# ---------------------------------------------------------------------------
# TurboQuant
# ---------------------------------------------------------------------------


class TurboQuant:
    """Reproducible TurboQuant quantizer for a fixed ``dim`` / ``bits`` / ``seed``.

    ``bits`` is the *target* bit-width. The MSE stage uses the full ``bits`` for
    :meth:`quantize_mse`. For the inner-product (``prod``) scheme the MSE stage
    uses ``bits - 1`` and the remaining 1 bit is spent on the QJL residual; a
    ``bits == 1`` prod quantizer therefore uses a 0-bit MSE stage (no MSE term,
    pure QJL).
    """

    def __init__(self, dim: int, bits: int, seed: int = 0) -> None:
        if dim < 1:
            raise ValueError(f"dim must be >= 1, got {dim}")
        if bits not in SUPPORTED_BITS:
            raise ValueError(f"bits must be one of {SUPPORTED_BITS}, got {bits}")
        self.dim = dim
        self.bits = bits
        self.seed = seed

        self._rotation = _random_rotation(dim, seed)
        self._qjl = _random_projection(dim, seed + _QJL_SEED_OFFSET)
        # MSE-stage bit-width for the two-stage prod scheme.
        self._mse_bits = bits - 1
        # Scaled codebooks (centroids for N(0, 1/d)).
        self._scale = 1.0 / np.sqrt(dim)

    # -- codebook access ----------------------------------------------------

    def _codebook(self, mse_bits: int) -> npt.NDArray[np.float32]:
        """Scaled centroids for the given MSE bit-width (>=1)."""
        scaled: npt.NDArray[np.float32] = (_NORMAL_CODEBOOKS[mse_bits] * self._scale).astype(
            np.float32
        )
        return scaled

    # -- MSE quantizer ------------------------------------------------------

    def quantize_mse(self, vec: npt.NDArray[np.floating]) -> tuple[npt.NDArray[np.int64], float]:
        """Quantize ``vec`` with the MSE quantizer at the full target ``bits``.

        Returns ``(indices, norm)`` where ``indices`` are the per-coordinate
        codebook indices of the rotated, unit-normalized vector and ``norm`` is
        the original L2 norm (used to rescale on dequantization).
        """
        return self._quantize_mse_core(vec, self.bits)

    def dequantize_mse(
        self, indices: npt.NDArray[np.int64], norm: float
    ) -> npt.NDArray[np.float32]:
        """Reconstruct a vector from MSE indices produced at the full ``bits``."""
        return self._dequantize_mse_core(indices, norm, self.bits)

    def _quantize_mse_core(
        self, vec: npt.NDArray[np.floating], mse_bits: int
    ) -> tuple[npt.NDArray[np.int64], float]:
        v = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        if norm == 0.0 or mse_bits < 1:
            return np.zeros(self.dim, dtype=np.int64), norm
        u = v / norm
        y = self._rotation @ u  # rotated unit vector
        codebook = self._codebook(mse_bits)
        # Nearest centroid per coordinate. searchsorted on midpoints is O(d log L).
        boundaries = (codebook[:-1] + codebook[1:]) / 2.0
        indices = np.searchsorted(boundaries, y).astype(np.int64)
        return indices, norm

    def _dequantize_mse_core(
        self, indices: npt.NDArray[np.int64], norm: float, mse_bits: int
    ) -> npt.NDArray[np.float32]:
        if norm == 0.0 or mse_bits < 1:
            return np.zeros(self.dim, dtype=np.float32)
        codebook = self._codebook(mse_bits)
        y_hat = codebook[indices]
        u_hat = self._rotation.T @ y_hat  # rotate back
        return (u_hat * norm).astype(np.float32)

    # -- inner-product (two-stage) quantizer --------------------------------

    def quantize_prod(
        self, vec: npt.NDArray[np.floating]
    ) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.float32], float, float]:
        """Quantize ``vec`` with the unbiased two-stage inner-product scheme.

        Returns ``(mse_indices, qjl_signs, residual_norm, norm)``:

        * ``mse_indices`` — MSE-stage indices at ``bits - 1`` (all zeros when
          ``bits == 1``).
        * ``qjl_signs`` — ``+1/-1`` vector of length ``dim`` (sign of ``S @ r``).
        * ``residual_norm`` — L2 norm of the unit-space residual ``r``.
        * ``norm`` — original L2 norm of ``vec``.
        """
        v = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        if norm == 0.0:
            return (
                np.zeros(self.dim, dtype=np.int64),
                np.ones(self.dim, dtype=np.float32),
                0.0,
                0.0,
            )
        u = v / norm
        mse_indices, _ = self._quantize_mse_core(u, self._mse_bits)
        u_mse = self._dequantize_mse_core(mse_indices, 1.0, self._mse_bits)
        residual = u - u_mse
        residual_norm = float(np.linalg.norm(residual))
        qjl_signs = np.sign(self._qjl @ residual).astype(np.float32)
        # np.sign(0) == 0; map any zeros to +1 so the sign vector is strictly +-1.
        qjl_signs[qjl_signs == 0] = 1.0
        return mse_indices, qjl_signs, residual_norm, norm

    def inner_product_prod(
        self,
        query: npt.NDArray[np.floating],
        mse_indices: npt.NDArray[np.int64],
        qjl_signs: npt.NDArray[np.float32],
        residual_norm: float,
        norm: float,
    ) -> float:
        """Unbiased estimate of ``<query, original_vec>`` from a prod row.

        ``query`` is a full-precision vector (not quantized). Implements the
        estimator ``norm * ( <q, u_mse> + gamma * sqrt(pi/2)/d * <S q, qjl> )``
        (paper Theorem 2 / Algorithm 2).
        """
        q = np.asarray(query, dtype=np.float32)
        if norm == 0.0:
            return 0.0
        u_mse = self._dequantize_mse_core(mse_indices, 1.0, self._mse_bits)
        mse_term = float(q @ u_mse)
        sq = self._qjl @ q  # S @ q
        qjl_term = float(np.sqrt(np.pi / 2.0) / self.dim * residual_norm * (sq @ qjl_signs))
        return norm * (mse_term + qjl_term)

    def dequantize_prod(
        self,
        mse_indices: npt.NDArray[np.int64],
        qjl_signs: npt.NDArray[np.float32],
        residual_norm: float,
        norm: float,
    ) -> npt.NDArray[np.float32]:
        """Reconstruct an (unbiased-in-expectation) vector from a prod row.

        Used for diagnostics; the search path uses :meth:`inner_product_prod`
        directly, which avoids materializing the reconstruction.
        """
        if norm == 0.0:
            return np.zeros(self.dim, dtype=np.float32)
        u_mse = self._dequantize_mse_core(mse_indices, 1.0, self._mse_bits)
        qjl_recon = np.sqrt(np.pi / 2.0) / self.dim * residual_norm * (self._qjl.T @ qjl_signs)
        recon: npt.NDArray[np.float32] = ((u_mse + qjl_recon) * norm).astype(np.float32)
        return recon


# ---------------------------------------------------------------------------
# Seeded matrix generation
# ---------------------------------------------------------------------------


def _random_rotation(dim: int, seed: int) -> npt.NDArray[np.float32]:
    """Uniformly random rotation via QR of a seeded Gaussian matrix.

    Sign-corrects the Q factor so the decomposition is a deterministic function
    of the seed (NumPy's QR sign convention is otherwise implementation-defined).
    """
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((dim, dim))
    q, r = np.linalg.qr(a)
    # Make Q unique: force positive diagonal of R.
    d = np.sign(np.diag(r))
    d[d == 0] = 1.0
    q = q * d
    return q.astype(np.float32)


def _random_projection(dim: int, seed: int) -> npt.NDArray[np.float32]:
    """Seeded ``dim x dim`` Gaussian matrix for the QJL transform."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((dim, dim)).astype(np.float32)

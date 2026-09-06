"""Scalable sklearn baselines for causal sequence-fusion comparisons."""

from __future__ import annotations

from dskit.pipeline.libs.torch_ts import CategoricalRecurrentFusionRegressor

__all__ = ["CategoricalSequenceRidgeRegressor"]


class CategoricalSequenceRidgeRegressor(CategoricalRecurrentFusionRegressor):
    """Ridge on flattened OHLCV, static features, and one-hot entities.

    The input parsing and train-only transformations exactly match the Torch
    fusion estimators. ``lsqr`` keeps the linear baseline bounded for tall,
    wide pooled panels without treating integer entity codes as ordinal.
    """

    def __init__(
        self,
        context_length=60,
        alpha=1.0,
        fit_intercept=True,
        max_iter=2000,
        tol=1e-4,
        standardize=True,
    ):
        super().__init__(
            context_length=context_length,
            standardize=standardize,
            device="cpu",
        )
        self.alpha = alpha
        self.fit_intercept = fit_intercept
        self.max_iter = max_iter
        self.tol = tol
        self._estimator = None

    @staticmethod
    def _design(sequence, static, category, n_categories):
        import numpy as np

        one_hot = np.zeros((len(category), n_categories), dtype=np.float32)
        one_hot[np.arange(len(category)), category] = 1.0
        return np.concatenate(
            [sequence.reshape(len(sequence), -1), static, one_hot], axis=1
        )

    def fit(self, X, y, categorical_feature=None, feature_names=None):
        """Fit Ridge after the shared causal sequence transformation."""
        import numpy as np
        from sklearn.linear_model import Ridge

        x = np.asarray(X)
        target = np.asarray(y, dtype=np.float64).reshape(-1)
        categorical = list(categorical_feature or [])
        if x.ndim != 2 or x.shape[0] != target.size or x.shape[0] < 1:
            raise ValueError("X and y must contain the same non-empty row count")
        if len(categorical) != 1 or isinstance(categorical[0], bool):
            raise ValueError("exactly one categorical feature index is required")
        self._category_index = int(categorical[0])
        if self._category_index < 0 or self._category_index >= x.shape[1]:
            raise ValueError("categorical feature index is outside the matrix")
        self._sequence_indices, self._static_indices = self._layout(
            feature_names, x.shape[1]
        )
        sequence, static, category = self._parts(x, fitting=True)
        design = self._design(sequence, static, category, len(self._categories))
        self._estimator = Ridge(
            alpha=float(self.alpha),
            fit_intercept=bool(self.fit_intercept),
            solver="lsqr",
            max_iter=int(self.max_iter),
            tol=float(self.tol),
        ).fit(design, target)
        return self

    def predict(self, X):
        """Return one forecast per row, refusing pre-fit inference."""
        if self._estimator is None:
            raise RuntimeError("CategoricalSequenceRidgeRegressor: predict before fit")
        sequence, static, category = self._parts(X, fitting=False)
        design = self._design(sequence, static, category, len(self._categories))
        return self._estimator.predict(design)

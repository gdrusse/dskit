import numpy as np
import pytest

from dskit.pipeline.libs.sklearn_ts import CategoricalSequenceRidgeRegressor


def _rows():
    names = [
        f"ohlcv_t{step:03d}_{field}"
        for step in range(3)
        for field in ("open", "high", "low", "close", "volume")
    ] + ["tod_sin", "symbol_code"]
    rows = []
    for index in range(12):
        path = []
        for step in range(3):
            price = 100.0 + index + step
            path.extend([price, price + 1.0, price - 1.0, price + 0.5, 10 + step])
        rows.append(path + [index / 12.0, index % 3])
    return np.asarray(rows, dtype=np.float32), names


def test_sequence_ridge_one_hots_entity_and_predicts():
    x, names = _rows()
    model = CategoricalSequenceRidgeRegressor(context_length=2, alpha=1.0).fit(
        x,
        np.linspace(-0.1, 0.1, len(x)),
        categorical_feature=[len(names) - 1],
        feature_names=names,
    )
    prediction = model.predict(x)
    assert prediction.shape == (len(x),)
    assert np.isfinite(prediction).all()
    assert model._estimator.n_features_in_ == 2 * 5 + 1 + 3


def test_sequence_ridge_refuses_unseen_entity():
    x, names = _rows()
    model = CategoricalSequenceRidgeRegressor(context_length=2).fit(
        x[:-1],
        np.linspace(-0.1, 0.1, len(x) - 1),
        categorical_feature=[len(names) - 1],
        feature_names=names,
    )
    unseen = x[-1:].copy()
    unseen[:, -1] = 9
    with pytest.raises(ValueError, match="unseen category"):
        model.predict(unseen)

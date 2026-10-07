"""NeuralForecast library adapter; DS Kit owns training, scaling and scoring."""
from dskit.pipeline.libs.torch_ts import SequenceEncoderAdapter

__all__ = ["NFSequenceEncoder"]
NODE_KINDS = ()


class NFSequenceEncoder(SequenceEncoderAdapter):
    """Use a native point-model forward as a learned latent vector.

    h is the latent width, not a change to the supervised target horizon.
    No future observations or library Trainer/loss enter this adapter.
    """

    def __init__(self, model, settings, output_size=8):
        import copy
        if (not isinstance(model, str) or not model.isidentifier()
                or model.startswith("_") or not isinstance(settings, dict)
                or type(output_size) is not int or output_size < 1):
            raise ValueError("invalid NeuralForecast adapter")
        reserved = {"h", "input_size", "loss", "valid_loss", "random_seed",
                    "scaler_type", "futr_exog_list", "hist_exog_list",
                    "stat_exog_list", "max_steps"}
        if reserved & set(settings):
            raise ValueError("adapter owns NeuralForecast input/loss/training contract")
        self.model, self.settings = model, copy.deepcopy(settings)
        self.output_size = output_size

    def output_width(self, channels):
        """Return the latent width, refusing multichannel input."""
        if channels != 1:
            raise ValueError("this native univariate adapter requires one channel")
        return self.output_size

    def build_module(self, sequence_length, channels):
        """Build a native forward adapter without starting a library trainer."""
        import inspect
        import random
        import numpy as np
        import torch
        from neuralforecast import models
        self.output_width(channels)
        cls = getattr(models, self.model, None)
        if cls is None or not inspect.isclass(cls):
            raise ValueError("unknown NeuralForecast model")
        allowed = set(inspect.signature(cls.__init__).parameters)
        if set(self.settings) - allowed:
            raise ValueError("unknown NeuralForecast architecture settings")
        py_state, np_state = random.getstate(), np.random.get_state()
        try:
            with torch.random.fork_rng(devices=[]):
                backbone = cls(h=self.output_size, input_size=sequence_length,
                               scaler_type="identity", max_steps=1,
                               random_seed=torch.initial_seed() % (2**32),
                               **self.settings)
        finally:
            random.setstate(py_state)
            np.random.set_state(np_state)
        width = self.output_size

        class Encoder(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.backbone = backbone

            def forward(self, sequence):
                result = self.backbone({
                    "insample_y": sequence,
                    "insample_mask": torch.ones_like(sequence),
                    "hist_exog": None, "futr_exog": None, "stat_exog": None})
                result = result.reshape(len(sequence), -1)
                if result.shape[1] != width:
                    raise ValueError("native forward changed its latent output shape")
                return result
        return Encoder()

from src.scripts.train_lstm import train_xsmn_lstm_with_full_refit


def test_lstm_validation_artifact_is_discarded_and_final_fit_uses_all_sequences():
    class FakeModel:
        instances = []

        def __init__(self):
            self.calls = []
            self.__class__.instances.append(self)

        def train_model(self, **kwargs):
            self.calls.append(kwargs)
            return 7

    production, best_epoch = train_xsmn_lstm_with_full_refit(
        FakeModel,
        sequences=[1] * 50,
        labels=[0] * 50,
        epochs=100,
        lr=0.002,
        seed=123,
        verbose=False,
    )

    assert best_epoch == 7
    assert production is FakeModel.instances[1]
    assert FakeModel.instances[0].calls[0]["val_split"] == 0.2
    final_call = production.calls[0]
    assert final_call["val_split"] == 0.0
    assert final_call["epochs"] == 7
    assert final_call["sequences"] == [1] * 50
    assert final_call["seed"] == 123

from torch import nn

from contrastyou.hooks.base import TrainerHook, EpocherHook
from contrastyou.meters import AverageValueMeter, MeterInterface


class ConsistencyTrainerHook(TrainerHook):
    def __init__(self, hook_name: str, weight: float):
        super().__init__(hook_name)
        self._weight = weight
        self._criterion = nn.MSELoss()

    def __call__(self):
        return _ConsistencyEpocherHook(name=self._hook_name, weight=self._weight, criterion=self._criterion)


class _ConsistencyEpocherHook(EpocherHook):
    def __init__(self, name: str, weight: float, criterion) -> None:
        super().__init__(name)
        self._weight = weight
        self._criterion = criterion

    def configure_meters(self, meters: MeterInterface):
        with self.meters.focus_on(self._name):
            self.meters.register_meter("loss", AverageValueMeter())

    def __call__(self, *, unlabeled_tf_logits, unlabeled_logits_tf, seed, affine_transformer, **kwargs):
        unlabeled_tf_prob = unlabeled_tf_logits.softmax(1)
        unlabeled_prob_tf = unlabeled_logits_tf.softmax(1)
        loss = self._criterion(unlabeled_prob_tf.detach(), unlabeled_tf_prob)
        with self.meters.focus_on(self._name):
            self.meters["loss"].add(loss.item())
        return self._weight * loss

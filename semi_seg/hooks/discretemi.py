from typing import List

import torch
from torch import nn, Tensor

from contrastyou.arch import UNet
from contrastyou.arch.utils import SingleFeatureExtractor
from contrastyou.hooks.base import TrainerHook, EpocherHook
from contrastyou.losses.discreteMI import IMSATLoss
from contrastyou.meters import AverageValueMeter

decoder_names = UNet.decoder_names
encoder_names = UNet.encoder_names


class DiscreteMITrainHook(TrainerHook):
    """
    You have a feature exacter, a projector and discrete Mutual information loss in side the loss.
    """

    @property
    def learnable_modules(self) -> List[nn.Module]:
        return [self._projector, ]

    def __init__(self, *, name, model: nn.Module, feature_name: str, weight: float = 1.0, num_clusters=20,
                 num_subheads=5, padding=None) -> None:
        super().__init__(hook_name=name)
        assert feature_name in encoder_names + decoder_names, feature_name
        self._feature_name = feature_name
        self._weight = weight

        self._extractor = SingleFeatureExtractor(model, feature_name=feature_name)  # noqa

        input_dim = model.get_channel_dim(feature_name)
        self._projector = self.init_projector(input_dim=input_dim, num_clusters=num_clusters, num_subheads=num_subheads)
        self._criterion = self.init_criterion(padding=padding)

    def __call__(self):
        return _DiscreteMIEpochHook(name=self._hook_name, weight=self._weight, extractor=self._extractor,
                                    projector=self._projector, criterion=self._criterion)

    def init_projector(self, *, input_dim, num_clusters, num_subheads=5):
        projector = self.projector_class(input_dim=input_dim, num_clusters=num_clusters,
                                         num_subheads=num_subheads, head_type="linear", T=1, normalize=False)
        return projector

    def init_criterion(self, padding: int = None):
        if self._feature_name in encoder_names:
            return self._init_criterion()
        return self._init_dense_criterion(padding=padding or 0)

    def _init_dense_criterion(self, padding: int = 0):
        criterion = self.criterion_class(padding=padding)
        return criterion

    def _init_criterion(self):
        criterion = self.criterion_class()

        def criterion_wrapper(*args, **kwargs):
            return criterion(*args, **kwargs)[0]

        return criterion_wrapper

    @property
    def projector_class(self):
        from contrastyou.projectors.heads import DenseClusterHead, ClusterHead
        if self._feature_name in encoder_names:
            return ClusterHead
        return DenseClusterHead

    @property
    def criterion_class(self):
        from contrastyou.losses.discreteMI import IIDLoss, IIDSegmentationLoss
        if self._feature_name in encoder_names:
            return IIDLoss
        return IIDSegmentationLoss


class _DiscreteMIEpochHook(EpocherHook):

    def __init__(self, *, name: str, weight: float, extractor, projector, criterion) -> None:
        super().__init__(name=name)
        self._extractor = extractor
        self._extractor.bind()
        self._weight = weight
        self._projector = projector
        self._criterion = criterion

    def configure_meters_given_epocher(self, meters):
        meters.register_meter("mi", AverageValueMeter())

    def before_forward_pass(self, **kwargs):
        self._extractor.clear()
        self._extractor.set_enable(True)

    def after_forward_pass(self, **kwargs):
        self._extractor.set_enable(False)

    def _call_implementation(self, *, unlabeled_image, unlabeled_image_tf, affine_transformer, **kwargs):
        n_unl = len(unlabeled_image)
        feature_ = self._extractor.feature()[-n_unl * 2:]
        proj_feature, proj_tf_feature = torch.chunk(feature_, 2, dim=0)
        assert proj_feature.shape == proj_tf_feature.shape
        proj_feature_tf = affine_transformer(proj_feature)

        prob1, prob2 = list(
            zip(*[torch.chunk(x, 2, 0) for x in self._projector(
                torch.cat([proj_feature_tf, proj_tf_feature], dim=0)
            )])
        )
        loss = sum([self._criterion(x1, x2) for x1, x2 in zip(prob1, prob2)]) / len(prob1)
        self.meters["mi"].add(loss.item())
        return loss * self._weight

    def close(self):
        self._extractor.remove()


class DiscreteIMSATTrainHook(DiscreteMITrainHook):

    def __init__(self, *, name, model: nn.Module, feature_name: str, weight: float = 1.0, num_clusters=20,
                 num_subheads=5, cons_weight: float) -> None:
        super().__init__(name=name, model=model, feature_name=feature_name, weight=weight, num_clusters=num_clusters,
                         num_subheads=num_subheads)
        self._criterion = IMSATLoss(lamda=1.0)
        self._consistency_criterion = nn.MSELoss()
        self._consistency_weight = float(cons_weight)

    def __call__(self):
        return _DiscreteIMSATEpochHook(name=self._hook_name, weight=self._weight, extractor=self._extractor,
                                       projector=self._projector, criterion=self._criterion,
                                       cons_criterion=self._consistency_criterion,
                                       cons_weight=self._consistency_weight)


class _DiscreteIMSATEpochHook(_DiscreteMIEpochHook):

    def __init__(self, *, name: str, weight: float, extractor, projector, criterion, cons_criterion,
                 cons_weight) -> None:
        super().__init__(name=name, weight=weight, extractor=extractor, projector=projector, criterion=criterion)
        self._cons_criterion = cons_criterion
        self._cons_weight = cons_weight

    def configure_meters_given_epocher(self, meters):
        super(_DiscreteIMSATEpochHook, self).configure_meters_given_epocher(meters)
        meters.register_meter("cons", AverageValueMeter())

    def _call_implementation(self, *, unlabeled_image, unlabeled_image_tf, affine_transformer, **kwargs):
        n_unl = len(unlabeled_image)
        feature_ = self._extractor.feature()[-n_unl * 2:]
        proj_feature, proj_tf_feature = torch.chunk(feature_, 2, dim=0)
        assert proj_feature.shape == proj_tf_feature.shape
        proj_feature_tf = affine_transformer(proj_feature)

        prob1, prob2 = list(
            zip(*[torch.chunk(x, 2, 0) for x in self._projector(
                torch.cat([proj_feature_tf, proj_tf_feature], dim=0)
            )])
        )
        loss = sum([self._criterion(self.flatten_predict(x1), self.flatten_predict(x2)) for x1, x2 in
                    zip(prob1, prob2)]) / len(prob1)
        cons_loss = sum([self._cons_criterion(x1, x2) for x1, x2 in zip(prob1, prob2)]) / len(prob1)

        self.meters["mi"].add(loss.item())
        self.meters["cons"].add(cons_loss.item())

        return loss * self._weight + cons_loss * self._cons_weight

    @staticmethod
    def flatten_predict(prediction: Tensor):
        assert prediction.dim() == 4
        b, c, h, w = prediction.shape
        prediction = torch.swapaxes(prediction, 0, 1)
        prediction = prediction.reshape(c, -1)
        prediction = torch.swapaxes(prediction, 0, 1)
        return prediction

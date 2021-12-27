from functools import partial
from typing import Type, Union, TYPE_CHECKING

from loguru import logger

from contrastyou.arch import UNet
from semi_seg.epochers.pretrain import PretrainEncoderEpocher, PretrainDecoderEpocher
from semi_seg.trainers._helper import _get_contrastive_dataloader
from semi_seg.trainers.trainer import SemiTrainer

if TYPE_CHECKING:
    from semi_seg.epochers.epocher import EpocherBase

    _Base = SemiTrainer
else:
    _Base = object


class _PretrainTrainerMixin(_Base):

    def __init__(self, **kwargs):
        super(_PretrainTrainerMixin, self).__init__(**kwargs)
        if "ContrastiveLoaderParams" not in self._config:
            raise RuntimeError(
                f"`ContrastiveLoaderParams` should be found in config, given \n`{', '.join(self._config.keys())}`"
            )

        self._contrastive_loader, self._monitor_loader = _get_contrastive_dataloader(
            self._unlabeled_loader, self._config["ContrastiveLoaderParams"]
        )
        self._inference_until = None

    @property
    def forward_until(self) -> str:
        if self._inference_until is None:
            return list(UNet.decoder_names)[-1]
        return self._inference_until

    @forward_until.setter
    def forward_until(self, forward_until: Union[str, None]):
        if isinstance(forward_until, str):
            if forward_until == "all":
                self._inference_until = None
                logger.opt(depth=1).debug(f"{self.__class__.__name__} set forward pass to {self.forward_until}")
                return
            assert forward_until in UNet.arch_elements, forward_until
        self._inference_until = forward_until
        logger.opt(depth=1).debug(f"{self.__class__.__name__} set forward pass to {self.forward_until}")

    def _run_epoch(self, epocher, *args, **kwargs):
        epocher.init = partial(epocher.init, chain_dataloader=self._contrastive_loader,
                               monitor_dataloader=self._monitor_loader)
        return super(_PretrainTrainerMixin, self)._run_epoch(epocher, *args, **kwargs)  # noqa

    def _start_training(self: Union['SemiTrainer', '_PretrainTrainerMixin'], **kwargs):
        start_epoch = max(self._cur_epoch + 1, self._start_epoch)
        self._cur_score: float

        for self._cur_epoch in range(start_epoch, self._max_epoch + 1):
            with self._storage:  # save csv each epoch

                train_metrics = self.tra_epoch()

                if self.on_master():
                    self._storage.add_from_meter_interface(
                        pre_tra=train_metrics, epoch=self._cur_epoch)
                    assert self._writer
                    self._writer.add_scalars_from_meter_interface(
                        pre_tra=train_metrics, epoch=self._cur_epoch)

                if hasattr(self, "_scheduler"):
                    assert self._scheduler
                    self._scheduler.step()

            if self.on_master():
                self.save_to(save_name="last.pth")

    def _create_initialized_tra_epoch(self, **kwargs) -> 'EpocherBase':
        epocher = self.train_epocher(
            model=self._model, optimizer=self._optimizer, labeled_loader=self._labeled_loader,
            unlabeled_loader=self._unlabeled_loader, sup_criterion=self._criterion, num_batches=self._num_batches,
            cur_epoch=self._cur_epoch, device=self._device, two_stage=False, disable_bn=False,
            chain_dataloader=self._contrastive_loader, inference_until=self._inference_until, scaler=self.scaler,
            accumulate_iter=1
        )
        epocher.set_trainer(self)
        epocher.init()
        return epocher

    def inference_demo(self):
        pass


class PretrainEncoderTrainer(_PretrainTrainerMixin, SemiTrainer):
    @property
    def train_epocher(self) -> Type['EpocherBase']:
        return PretrainEncoderEpocher


class PretrainDecoderTrainer(_PretrainTrainerMixin, SemiTrainer):
    @property
    def train_epocher(self) -> Type['EpocherBase']:
        return PretrainDecoderEpocher

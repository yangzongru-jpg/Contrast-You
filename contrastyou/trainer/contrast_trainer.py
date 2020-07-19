import itertools
import os
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader

from contrastyou import PROJECT_PATH
from contrastyou.epocher.base_epocher import FSEpocher
from contrastyou.epocher.contrast_epocher import PretrainEncoderEpoch, PretrainDecoderEpoch, FineTuneEpoch
from contrastyou.losses.contrast_loss import SupConLoss
from contrastyou.trainer._utils import Flatten
from deepclustering2.loss import KL_div
from deepclustering2.meters2 import Storage, StorageIncomeDict
from deepclustering2.schedulers import GradualWarmupScheduler
from deepclustering2.trainer.trainer import Trainer, T_loader
from deepclustering2.writer import SummaryWriter


class ContrastTrainer(Trainer):
    RUN_PATH = Path(PROJECT_PATH) / "runs"

    def __init__(self, model: nn.Module, pretrain_loader: T_loader, fine_tune_loader: T_loader, val_loader: DataLoader,
                 save_dir: str = "base", max_epoch_train_encoder: int = 100, max_epoch_train_decoder: int = 100,
                 max_epoch_train_finetune: int = 100, num_batches: int = 256, device: str = "cpu", configuration=None,
                 train_encoder: bool = True, train_decoder: bool = True):
        """
        ContrastTraining Trainer
        :param model: nn.module network to be pretrained
        :param pretrain_loader: all unlabeled data under ContrastiveBatchSampler
        :param fine_tune_loader: a fraction of labeled data for finetuning
        :param val_loader: validation data
        :param save_dir: main save_die
        :param max_epoch_train_encoder: max_epoch to be trained for encoder training
        :param max_epoch_train_decoder: max_epoch to be trained for decoder training
        :param max_epoch_train_finetune: max_epoch to be trained for finetuning
        :param num_batches:  num_batches used in training
        :param device: cpu or cuda
        :param configuration: configuration dict
        """
        super().__init__(model, save_dir, None, num_batches, device, configuration)  # noqa
        self._pretrain_loader = pretrain_loader
        self._fine_tune_loader = fine_tune_loader
        self._val_loader = val_loader

        self._max_epoch_train_encoder = max_epoch_train_encoder
        self._max_epoch_train_decoder = max_epoch_train_decoder
        self._max_epoch_train_finetune = max_epoch_train_finetune

        self._register_buffer("train_encoder", train_encoder)
        self._register_buffer("train_decoder", train_decoder)
        self._register_buffer("train_encoder_done", False)
        self._register_buffer("train_decoder_done", False)

        self._pretrain_encoder_storage = Storage()
        self._pretrain_decoder_storage = Storage()
        self._finetune_storage = Storage()

    def pretrain_encoder_init(self):
        # adding optimizer and scheduler
        self._projector = nn.Sequential(
            nn.AdaptiveAvgPool2d((1, 1)),
            Flatten(),
            nn.Linear(256, 256),
            nn.LeakyReLU(0.01, inplace=True),
            nn.Linear(256, 128),
        )
        self._optimizer = torch.optim.Adam(itertools.chain(self._model.parameters(), self._projector.parameters()),
                                           lr=1e-6, weight_decay=1e-5)  # noqa
        self._scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(self._optimizer,
                                                                     self._max_epoch_train_encoder - 10, 0)
        self._scheduler = GradualWarmupScheduler(self._optimizer, 300, 10, self._scheduler)  # noqa

    def pretrain_encoder_run(self):
        self.to(self._device)
        self._model.enable_grad_encoder()  # noqa
        self._model.disable_grad_decoder()  # noqa

        for self._cur_epoch in range(self._start_epoch, self._max_epoch_train_encoder):
            pretrain_encoder_dict = PretrainEncoderEpoch(
                model=self._model, projection_head=self._projector,
                optimizer=self._optimizer,
                pretrain_encoder_loader=self._pretrain_loader,
                contrastive_criterion=SupConLoss(), num_batches=self._num_batches,
                cur_epoch=self._cur_epoch, device=self._device
            ).run()
            self._scheduler.step()
            storage_dict = StorageIncomeDict(PRETRAIN_ENCODER=pretrain_encoder_dict, )
            self._pretrain_encoder_storage.put_from_dict(storage_dict, epoch=self._cur_epoch)
            self._writer.add_scalar_with_StorageDict(storage_dict, self._cur_epoch)
            self._save_to("last.pth", path=os.path.join(self._save_dir, "pretrain_encoder"))
        self.train_encoder_done = True

    def pretrain_decoder_init(self):
        # adding optimizer and scheduler
        self._projector = nn.Sequential(
            nn.Conv2d(64, 64, 3, 1, 1),
            nn.BatchNorm2d(64),
            nn.LeakyReLU(0.01, inplace=True),
            nn.Conv2d(64, 32, 3, 1, 1)
        )
        self._optimizer = torch.optim.Adam(itertools.chain(self._model.parameters(), self._projector.parameters()),
                                           lr=1e-6, weight_decay=0)
        self._scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(self._optimizer,
                                                                     self._max_epoch_train_decoder - 10, 0)
        self._scheduler = GradualWarmupScheduler(self._optimizer, 300, 10, self._scheduler)

    def pretrain_decoder_run(self):
        self.to(self._device)
        self._projector.to(self._device)

        self._model.enable_grad_decoder()  # noqa
        self._model.disable_grad_encoder()  # noqa

        for self._cur_epoch in range(self._start_epoch, self._max_epoch_train_decoder):
            pretrain_decoder_dict = PretrainDecoderEpoch(
                model=self._model, projection_head=self._projector,
                optimizer=self._optimizer,
                pretrain_decoder_loader=self._pretrain_loader,
                contrastive_criterion=SupConLoss(), num_batches=self._num_batches,
                cur_epoch=self._cur_epoch, device=self._device
            ).run()
            self._scheduler.step()
            storage_dict = StorageIncomeDict(PRETRAIN_DECODER=pretrain_decoder_dict, )
            self._pretrain_encoder_storage.put_from_dict(storage_dict, epoch=self._cur_epoch)
            self._writer.add_scalar_with_StorageDict(storage_dict, self._cur_epoch)
            self._save_to("last.pth", path=os.path.join(self._save_dir, "pretrain_decoder"))
        self.train_decoder_done = True

    def finetune_network_init(self):

        self._optimizer = torch.optim.Adam(self._model.parameters(), lr=1e-6, weight_decay=1e-5)
        self._scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(self._optimizer,
                                                                     self._max_epoch_train_finetune - 10, 0)
        self._scheduler = GradualWarmupScheduler(self._optimizer, 200, 10, self._scheduler)

    def finetune_network_run(self):
        self.to(self._device)
        self._model.enable_grad_decoder()  # noqa
        self._model.enable_grad_encoder()  # noqa

        for self._cur_epoch in range(self._start_epoch, self._max_epoch_train_finetune):
            finetune_dict = FineTuneEpoch(
                model=self._model, optimizer=self._optimizer,
                labeled_loader=self._fine_tune_loader, num_batches=self._num_batches,
                cur_epoch=self._cur_epoch, device=self._device
            ).run()
            val_dict, cur_score = FSEpocher.EvalEpoch(self._model, val_data_loader=self._val_loader,
                                                      sup_criterion=KL_div(),
                                                      cur_epoch=self._cur_epoch, device=self._device).run()
            self._scheduler.step()
            storage_dict = StorageIncomeDict(finetune=finetune_dict, val=val_dict)
            self._finetune_storage.put_from_dict(storage_dict, epoch=self._cur_epoch)
            self._writer.add_scalar_with_StorageDict(storage_dict, self._cur_epoch)
            self.save(cur_score, os.path.join(self._save_dir, "finetune"))

    def start_training(self, checkpoint: str = None):

        with SummaryWriter(str(self._save_dir)) as self._writer:  # noqa
            if self.train_encoder:
                self.pretrain_encoder_init()
                if checkpoint is not None:
                    try:
                        self.load_state_dict_from_path(os.path.join(checkpoint, "pretrain_encoder"))
                    except Exception as e:
                        raise RuntimeError(f"loading pretrain_encoder_checkpoint failed with {e}, ")

                if not self.train_encoder_done:
                    self.pretrain_encoder_run()
            if self.train_decoder:
                self.pretrain_decoder_init()
                if checkpoint is not None:
                    try:
                        self.load_state_dict_from_path(os.path.join(checkpoint, "pretrain_decoder"))
                        checkpoint_already_load = True
                    except Exception as e:
                        print(f"loading pretrain_decoder_checkpoint failed with {e}, ")
                if not self.train_decoder_done:
                    self.pretrain_decoder_run()
            self.finetune_network_init()
            if checkpoint is not None and not checkpoint_already_load:
                try:
                    self.load_state_dict_from_path(os.path.join(checkpoint, "finetune"))
                except Exception as e:
                    print(f"loading finetune_checkpoint failed with {e}, ")
            self.finetune_network_run()

"""
Author: Yonglong Tian (yonglong@mit.edu)
Date: May 07, 2020
"""
from __future__ import print_function

import torch
import torch.nn as nn
from torch import Tensor


def is_normalized(feature: Tensor):
    norms = feature.norm(dim=1)
    return torch.allclose(norms, torch.ones_like(norms))


class SupConLoss(nn.Module):
    """Supervised Contrastive Learning: https://arxiv.org/pdf/2004.11362.pdf.
    It also supports the unsupervised contrastive loss in SimCLR"""

    def __init__(self, temperature=0.07, contrast_mode='all',
                 base_temperature=0.07):
        super(SupConLoss, self).__init__()
        self.temperature = temperature
        self.contrast_mode = contrast_mode
        self.base_temperature = base_temperature

    def forward(self, features, labels=None, mask=None):
        """Compute loss for model. If both `labels` and `mask` are None,
        it degenerates to SimCLR unsupervised loss:
        https://arxiv.org/pdf/2002.05709.pdf

        Args:
            features: hidden vector of shape [bsz, n_views, ...].
            labels: ground truth of shape [bsz].
            mask: contrastive mask of shape [bsz, bsz], mask_{i,j}=1 if sample j
                has the same class as sample i. Can be asymmetric.
        Returns:
            A loss scalar.
        """
        device = (torch.device('cuda')
                  if features.is_cuda
                  else torch.device('cpu'))

        if len(features.shape) < 3:
            raise ValueError('`features` needs to be [bsz, n_views, ...],'
                             'at least 3 dimensions are required')
        if len(features.shape) > 3:
            features = features.view(features.shape[0], features.shape[1], -1)

        batch_size = features.shape[0]
        if labels is not None and mask is not None:
            raise ValueError('Cannot define both `labels` and `mask`')
        elif labels is None and mask is None:
            mask = torch.eye(batch_size, dtype=torch.float32).to(device)  # SIMCLR
        elif labels is not None:
            if isinstance(labels, list):
                labels = torch.Tensor(labels).long()
            labels = labels.contiguous().view(-1, 1)
            if labels.shape[0] != batch_size:
                raise ValueError('Num of labels does not match num of features')
            mask = torch.eq(labels, labels.t()).float().to(device)
        else:
            mask = mask.float().to(device)

        contrast_count = features.shape[1]
        contrast_feature = torch.cat(torch.unbind(features, dim=1), dim=0)  # 32 128
        if self.contrast_mode == 'one':
            anchor_feature = features[:, 0]
            anchor_count = 1
        elif self.contrast_mode == 'all':
            anchor_feature = contrast_feature
            anchor_count = contrast_count
        else:
            raise ValueError('Unknown mode: {}'.format(self.contrast_mode))

        # compute logits
        anchor_dot_contrast = torch.div(
            torch.matmul(anchor_feature, contrast_feature.t()),
            self.temperature)
        # for numerical stability
        logits_max, _ = torch.max(anchor_dot_contrast, dim=1, keepdim=True)
        logits = anchor_dot_contrast - logits_max.detach()

        # tile mask
        mask = mask.repeat(anchor_count, contrast_count)
        # mask-out self-contrast cases
        logits_mask = torch.scatter(
            torch.ones_like(mask),
            1,
            torch.arange(batch_size * anchor_count).view(-1, 1).to(device),
            0
        )
        mask = mask * logits_mask

        # compute log_prob
        exp_logits = torch.exp(logits) * logits_mask
        log_prob = logits - torch.log(exp_logits.sum(1, keepdim=True) + 1e-16)

        # compute mean of log-likelihood over positive
        mean_log_prob_pos = (mask * log_prob).sum(1) / mask.sum(1)

        # loss
        loss = - (self.temperature / self.base_temperature) * mean_log_prob_pos
        loss = loss.view(anchor_count, batch_size).mean()

        return loss


class SupConLoss2(nn.Module):
    def __init__(self, temperature=0.07, out_mode=True):
        super().__init__()
        self._t = temperature
        self._out_mode = out_mode

    def forward(self, proj_feat1, proj_feat2, target=None, mask: Tensor = None):
        assert is_normalized(proj_feat1) and is_normalized(proj_feat2), f"features need to be normalized first"
        assert proj_feat1.shape == proj_feat2.shape, (proj_feat1.shape, proj_feat2.shape)

        batch_size = len(proj_feat1)
        projections = torch.cat([proj_feat1, proj_feat2], dim=0)
        sim_logits = torch.mm(projections, projections.t().contiguous()) / self._t
        max_value = sim_logits.max().detach()
        sim_logits -= max_value

        sim_exp = torch.exp(sim_logits)

        unselect_diganal_mask = 1 - torch.eye(
            batch_size * 2, batch_size * 2, dtype=torch.float, device=proj_feat2.device)

        # build negative examples
        if mask is not None:
            assert mask.shape == torch.Size([batch_size, batch_size])
            mask = mask.repeat(2, 2)
            pos_mask = mask == 1
            neg_mask = mask == 0

        elif target is not None:
            if isinstance(target, list):
                target = torch.Tensor(target).to(device=proj_feat2.device)
            mask = torch.eq(target[..., None], target[None, ...])
            mask = mask.repeat(2, 2)

            pos_mask = mask == True
            neg_mask = mask == False
        else:
            # only postive masks are diagnal of the sim_matrix
            pos_mask = torch.eye(batch_size, dtype=torch.float, device=proj_feat2.device)  # SIMCLR
            pos_mask = pos_mask.repeat(2, 2)
            neg_mask = 1 - pos_mask

        pos_mask = pos_mask * unselect_diganal_mask
        neg_mask = neg_mask * unselect_diganal_mask
        pos = sim_exp * pos_mask
        negs = sim_exp * neg_mask
        pos_count = pos_mask.sum(1)
        if not self._out_mode:
            # this is the in mode
            loss = (- torch.log(pos.sum(1) / (pos.sum(1) + negs.sum(1))) / pos_count).mean()
        # this is the out mode
        else:
            log_pos_div_sum_pos_neg = (sim_logits - torch.log((pos + negs).sum(1, keepdim=True))) * pos_mask
            log_pos_div_sum_pos_neg = log_pos_div_sum_pos_neg.sum(1) / pos_count
            loss = -log_pos_div_sum_pos_neg.mean()
        if torch.isnan(loss):
            raise RuntimeError(loss)
        return loss


class SupConLoss3(SupConLoss2):
    """
    This loss takes two similarity matrix, one for positive one for negative
    """

    def forward(self, proj_feat1, proj_feat2, pos_weight: Tensor = None, neg_weight: Tensor = None, **kwargs):
        assert is_normalized(proj_feat1) and is_normalized(proj_feat2), f"features need to be normalized first"
        assert proj_feat1.shape == proj_feat2.shape, (proj_feat1.shape, proj_feat2.shape)
        pos_weight: Tensor
        neg_weight: Tensor
        assert pos_weight is not None and neg_weight is not None, (pos_weight, neg_weight)
        batch_size = len(proj_feat1)

        assert pos_weight.shape == torch.Size([batch_size, batch_size])
        assert neg_weight.shape == torch.Size([batch_size, batch_size])
        assert pos_weight.max() <= 1 and pos_weight.min() >= 0
        assert neg_weight.max() <= 1 and neg_weight.min() >= 0
        [pos_weight, neg_weight] = list(map(lambda x: x.repeat(2, 2), [pos_weight, neg_weight]))

        projections = torch.cat([proj_feat1, proj_feat2], dim=0)
        sim_logits = torch.mm(projections, projections.t().contiguous()) / self._t
        max_value = sim_logits.max().detach()
        sim_logits -= max_value

        sim_exp = torch.exp(sim_logits)

        unselect_diganal_mask = 1 - torch.eye(
            batch_size * 2, batch_size * 2, dtype=torch.float, device=proj_feat2.device)

        pos_weight = pos_weight * unselect_diganal_mask
        neg_weight = neg_weight * unselect_diganal_mask
        pos = sim_exp * pos_weight
        negs = sim_exp * neg_weight
        pos_sum_weight = pos_weight.sum(1)
        if not self._out_mode:
            # this is the in mode
            loss = (- torch.log(pos.sum(1) / (pos.sum(1) + negs.sum(1))) / pos_sum_weight).mean()
        # this is the out mode
        else:
            log_pos_div_sum_pos_neg = (sim_logits - torch.log((pos + negs).sum(1, keepdim=True))) * pos
            log_pos_div_sum_pos_neg = log_pos_div_sum_pos_neg.sum(1) / pos_sum_weight
            loss = -log_pos_div_sum_pos_neg.mean()

        return loss


if __name__ == '__main__':
    import random

    feature1 = torch.randn(100, 256, device="cuda")
    feature2 = torch.randn(100, 256, device="cuda")
    criterion1 = SupConLoss(temperature=0.07, base_temperature=0.07)
    criterion2 = SupConLoss2(temperature=0.07, out_mode=False)
    criterion3 = SupConLoss2(temperature=0.07, out_mode=True)

    target = [random.randint(0, 5) for i in range(100)]
    from torch.cuda import Event

    start = Event(enable_timing=True, blocking=True)
    end = Event(enable_timing=True, blocking=True)
    start.record()
    loss1 = criterion1(torch.stack(
        [nn.functional.normalize(feature1, dim=1),
         nn.functional.normalize(feature2, dim=1), ], dim=1
    ), labels=target)
    end.record()
    print(start.elapsed_time(end))

    start = Event(enable_timing=True, blocking=True)
    end = Event(enable_timing=True, blocking=True)
    start.record()
    loss2 = criterion2(
        nn.functional.normalize(feature1, dim=1),
        nn.functional.normalize(feature2, dim=1),
        target=target
    )
    end.record()
    print(start.elapsed_time(end))

    start = Event(enable_timing=True, blocking=True)
    end = Event(enable_timing=True, blocking=True)
    start.record()
    loss3 = criterion3(
        nn.functional.normalize(feature1, dim=1),
        nn.functional.normalize(feature2, dim=1),
        target=target
    )
    end.record()
    print(start.elapsed_time(end))

    assert torch.allclose(loss1, loss2) and torch.allclose(loss3, loss1), (loss1, loss2, loss3)

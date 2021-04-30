from typing import Tuple

import numpy as np
from torch.utils.data import DataLoader

from contrastyou import get_cc_data_path
from contrastyou.data import DatasetBase, extract_sub_dataset_based_on_scan_names, InfiniteRandomSampler, ScanSampler
from contrastyou.utils import fix_all_seed_within_context
from semi_seg.augment import ACDCStrongTransforms, SpleenStrongTransforms, ProstateStrongTransforms
from semi_seg.data import ACDCDataset, ProstateDataset, mmWHSCTDataset, mmWHSMRDataset

__all__ = ["create_dataset", "create_val_loader", "get_data_loaders"]

data_zoo = {"acdc": ACDCDataset, "prostate": ProstateDataset, "mmwhsct": mmWHSCTDataset, "mmwhsmr": mmWHSMRDataset}
augment_zoo = {
    "acdc": ACDCStrongTransforms, "spleen": SpleenStrongTransforms,
    "prostate": ProstateStrongTransforms, "mmwhsct": ACDCStrongTransforms, "mmwhsmr": ACDCStrongTransforms
}


def create_dataset(name: str):
    data_class = data_zoo[name]
    aug_transform = augment_zoo[name]
    tra_set = data_class(root_dir=get_cc_data_path(), mode="train", transforms=aug_transform.pretrain)
    test_set = data_class(root_dir=get_cc_data_path(), mode="val", transforms=aug_transform.val)
    return tra_set, test_set


def split_dataset(dataset: DatasetBase, *ratios: float, seed: int = 1, ):
    assert sum(ratios) <= 1, ratios
    scan_list = sorted(set(dataset.get_scan_list()))
    with fix_all_seed_within_context(seed):
        scan_list_permuted = np.random.permutation(scan_list).tolist()

    def _sum_iter(ratio_list):
        sum = 0
        for i in ratio_list:
            yield sum + i
            sum += i

    def _two_element_iter(cut_list):
        previous = 0
        for r in cut_list:
            yield previous, r
            previous = r
        yield previous, len(scan_list)

    cutting_points = [int(len(scan_list) * x) for x in _sum_iter(ratios)]

    sub_datasets = [extract_sub_dataset_based_on_scan_names(dataset, scan_list_permuted[x:y]) for x, y in
                    _two_element_iter(cutting_points)]
    assert sum([len(set(x.get_scan_list())) for x in sub_datasets]) == len(scan_list)
    return sub_datasets


def get_data_loaders(data_params, labeled_loader_params, unlabeled_loader_params, pretrain=False, group_test=True):
    data_name = data_params["name"]
    tra_set, test_set = create_dataset(data_name)
    labeled_data_ratio = data_params["labeled_data_ratio"]
    if pretrain:
        labeled_data_ratio = 0.5
    label_set, unlabeled_set = split_dataset(tra_set, labeled_data_ratio)

    shuffle_l = labeled_loader_params["shuffle"]
    shuffle_u = unlabeled_loader_params["shuffle"]

    batch_size_l = labeled_loader_params["batch_size"]
    batch_size_u = unlabeled_loader_params["batch_size"]

    n_workers_l = labeled_loader_params["num_workers"]
    n_workers_u = labeled_loader_params["num_workers"]

    labeled_sampler = InfiniteRandomSampler(label_set, shuffle=shuffle_l)
    unlabeled_sampler = InfiniteRandomSampler(unlabeled_set, shuffle=shuffle_u)

    labeled_loader = DataLoader(
        label_set, sampler=labeled_sampler, batch_size=batch_size_l, num_workers=n_workers_l, pin_memory=True)
    unlabeled_loader = DataLoader(
        unlabeled_set, sampler=unlabeled_sampler, batch_size=batch_size_u, num_workers=n_workers_u, pin_memory=True)
    group_test = group_test if data_name not in ("spleen", "mmwhsct", "mmwhsmr") else False
    test_loader = DataLoader(
        test_set,
        batch_size=1 if group_test else 4,
        batch_sampler=ScanSampler(
            test_set,
            shuffle=False
        ) if group_test else None,
    )
    return labeled_loader, unlabeled_loader, test_loader


def create_val_loader(*, test_loader) -> Tuple[DataLoader, DataLoader]:
    test_dataset: DatasetBase = test_loader.dataset
    batch_sampler = test_loader.batch_sampler
    is_group_scan = isinstance(batch_sampler, ScanSampler)

    ratio = 0.35 if not isinstance(test_dataset, (mmWHSCTDataset, mmWHSMRDataset)) else 0.45
    val_set, test_set = split_dataset(test_dataset, ratio)
    val_batch_sampler = ScanSampler(val_set) if is_group_scan else None

    val_dataloader = DataLoader(val_set, batch_sampler=val_batch_sampler)

    test_batch_sampler = ScanSampler(test_set) if is_group_scan else None
    test_dataloader = DataLoader(test_set, batch_sampler=test_batch_sampler)
    return val_dataloader, test_dataloader

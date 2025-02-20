# dictionary helper functions
import collections.abc as container_abcs
import functools
import os
import random
import warnings
from contextlib import contextmanager
from itertools import repeat
from pathlib import Path
from typing import List

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import torch
from loguru import logger
from torch import nn
from torch.optim import Optimizer
from torch.utils.data.dataloader import DataLoader, _BaseDataLoaderIter  # noqa

from script.utils import T_path


def flatten_dict(d, parent_key="", sep="_"):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, container_abcs.MutableMapping):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def get_dataset(dataloader):
    if isinstance(dataloader, _BaseDataLoaderIter):
        return dataloader._dataset  # noqa
    elif isinstance(dataloader, DataLoader):
        return dataloader.dataset
    else:
        raise NotImplementedError(type(dataloader))


def average_iter(a_list):
    return sum(a_list) / float(len(a_list))


def multiply_iter(iter_a, iter_b):
    return [x * y for x, y in zip(iter_a, iter_b)]


def weighted_average_iter(a_list, weight_list):
    sum_weight = sum(weight_list) + 1e-16
    return sum(multiply_iter(a_list, weight_list)) / sum_weight


def pairwise_distances(x, y=None, recall_func=None):
    '''
    Input: x is a Nxd matrix
           y is an optional Mxd matirx
           recall function is a function to manipulate the distance.
    Output: dist is a NxM matrix where dist[i,j] is the square norm between x[i,:] and y[j,:]
            if y is not given then use 'y=x'.
    i.e. dist[i,j] = ||x[i,:]-y[j,:]||^2
    '''
    x_norm = (x ** 2).sum(1).view(-1, 1)
    if y is not None:
        y_norm = (y ** 2).sum(1).view(1, -1)
    else:
        y = x
        y_norm = x_norm.view(1, -1)

    dist = x_norm + y_norm - 2.0 * torch.mm(x, torch.transpose(y, 0, 1))
    if recall_func:
        return recall_func(dist)
    return dist


@contextmanager
def plt_interactive():
    plt.ion()
    try:
        yield
    finally:
        plt.ioff()


def extract_model_state_dict(trainer_checkpoint_path: str, *, keyword="_model"):
    trainer_state = torch.load(trainer_checkpoint_path, map_location="cpu")
    return {k[7:]: v for k, v in trainer_state["module_state"].items() if k.startswith("_model.")}


def deprecated(func):
    """This is a decorator which can be used to mark functions
    as deprecated. It will result in a warning being emitted
    when the function is used."""

    @functools.wraps(func)
    def new_func(*args, **kwargs):
        warnings.simplefilter('always', DeprecationWarning)  # turn off filter
        warnings.warn(f"Call to deprecated function {func.__name__}.", category=DeprecationWarning, stacklevel=2)

        warnings.simplefilter('default', DeprecationWarning)  # reset filter
        return func(*args, **kwargs)

    return new_func


# reproducibility
def set_deterministic(enable=True):
    torch.backends.cudnn.benchmark = not enable  # noqa
    try:
        torch.use_deterministic_algorithms(enable)
    except:
        try:
            torch.set_deterministic(enable)
        finally:
            return


def fix_all_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


@contextmanager
def fix_all_seed_for_transforms(seed):
    random_state = random.getstate()
    np_state = np.random.get_state()
    torch_state = torch.random.get_rng_state()
    fix_all_seed(seed)
    try:
        yield
    finally:
        random.setstate(random_state)
        np.random.set_state(np_state)  # noqa
        torch.random.set_rng_state(torch_state)  # noqa


@contextmanager
def fix_all_seed_within_context(seed):
    random_state = random.getstate()
    np_state = np.random.get_state()
    torch_state = torch.random.get_rng_state()
    cuda_support = torch.cuda.is_available()
    if cuda_support:
        torch_cuda_state = torch.cuda.get_rng_state()
        torch_cuda_state_all = torch.cuda.get_rng_state_all()
    fix_all_seed(seed)
    try:
        yield
    finally:
        random.setstate(random_state)
        np.random.set_state(np_state)  # noqa
        torch.random.set_rng_state(torch_state)  # noqa
        if cuda_support:
            torch.cuda.set_rng_state(torch_cuda_state)  # noqa
            torch.cuda.set_rng_state_all(torch_cuda_state_all)  # noqa


def ntuple(n):
    def parse(x):
        if isinstance(x, str):
            return tuple(repeat(x, n))
        if isinstance(x, container_abcs.Iterable):
            x = list(x)
            if len(x) == 1:
                return tuple(repeat(x[0], n))
            if len(x) != n:
                raise RuntimeError(f"inconsistent shape between {x} and {n}")
            return x

        return tuple(repeat(x, n))

    return parse


_single = ntuple(1)
_pair = ntuple(2)
_triple = ntuple(3)
_quadruple = ntuple(4)


def adding_writable_sink(save_dir):
    from contrastyou import logger_format
    abs_save_dir = os.path.abspath(save_dir)
    from loguru import logger
    logger.add(os.path.join(abs_save_dir, "loguru.log"), level="TRACE", backtrace=False, diagnose=True,
               format=logger_format)


def fix_seed(func):
    functools.wraps(func)

    def func_wrapper(*args, **kwargs):
        with fix_all_seed_within_context(1):
            return func(*args, **kwargs)

    return func_wrapper


def path2Path(path: T_path) -> Path:
    assert isinstance(path, (Path, str)), type(path)
    return Path(path) if isinstance(path, str) else path


def path2str(path: T_path) -> str:
    assert isinstance(path, (Path, str)), type(path)
    return str(path)


def class_name(class_) -> str:
    return class_.__class__.__name__


def get_lrs_from_optimizer(optimizer: Optimizer) -> List[float]:
    return [p["lr"] for p in optimizer.param_groups]


@contextmanager
def disable_tracking_bn_stats(model):
    def switch_attr(m):
        if hasattr(m, "track_running_stats"):
            m.track_running_stats ^= True

    # let the track_running_stats to be inverse
    model.apply(switch_attr)
    # return the model
    try:
        yield
    # let the track_running_stats to be inverse
    finally:
        model.apply(switch_attr)


def get_model(model):
    if isinstance(model, (nn.parallel.DistributedDataParallel, nn.parallel.DataParallel)):
        return model.module
    elif isinstance(model, nn.Module):
        return model
    raise TypeError(type(model))


class switch_plt_backend:

    def __init__(self, env="agg") -> None:
        super().__init__()
        self.env = env

    def __enter__(self):
        self.prev = matplotlib.get_backend()
        matplotlib.use(self.env, force=True)

    def __exit__(self, exc_type, exc_val, exc_tb):
        matplotlib.use(self.prev, force=True)

    def __call__(self, func):
        functools.wraps(func)

        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return wrapper


@contextmanager
def ignore_exception(*exceptions, log=True):
    if len(exceptions) == 0:
        exceptions = (Exception,)
    try:
        yield
    except exceptions as e:
        if log:
            logger.warning(e)
        else:
            pass

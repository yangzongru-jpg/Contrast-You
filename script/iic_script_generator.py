import argparse
import os
from itertools import cycle

from deepclustering2.cchelper import JobSubmiter

from contrastyou import PROJECT_PATH, on_cc
from contrastyou.config import dictionary_merge_by_hierachy
from script import utils
from script.utils import TEMP_DIR, yaml_load, write_yaml, grid_search, ScriptGenerator, \
    move_dataset
from semi_seg import __accounts, num_batches_zoo, pre_max_epoch_zoo, ft_max_epoch_zoo

account = cycle(__accounts)
opt_hook_path = {
    "udaiic": "config/hooks/udaiic.yaml"
}


class DiscreteMIScriptGenerator(ScriptGenerator):

    def __init__(self, *, data_name, num_batches, save_dir, max_epoch: int) -> None:
        super().__init__(data_name=data_name, num_batches=num_batches, save_dir=save_dir)
        self.hook_config = yaml_load(PROJECT_PATH + "/" + opt_hook_path[self.get_hook_name()])
        self._max_epoch = max_epoch
        self.conditions.append(f" Trainer.max_epoch={max_epoch} ")

    def get_hook_name(self):
        return "udaiic"

    def get_hook_params(self, feature_names, mi_weights, consistency_weight):
        return {"DiscreteMIConsistencyParams":
                    {"feature_names": feature_names, "mi_weights": mi_weights,
                     "consistency_weight": consistency_weight}
                }

    def generate_single_script(self, save_dir, seed, hook_path):
        from semi_seg import ft_lr_zooms
        ft_lr = ft_lr_zooms[self._data_name]

        return f"python main.py Trainer.name=semi Trainer.save_dir={save_dir} " \
               f" Optim.lr={ft_lr:.7f} RandomSeed={str(seed)} " \
               f" {' '.join(self.conditions)} " \
               f" --opt-path config/pretrain.yaml {hook_path}"

    def grid_search_on(self, *, seed, **kwargs):
        jobs = []
        for param in grid_search(**{**kwargs, **{"seed": seed}}):
            random_seed = param.pop("seed")
            hook_params = self.get_hook_params(**param)
            sub_save_dir = self.get_hyparam_string(**param)
            merged_config = dictionary_merge_by_hierachy(self.hook_config, hook_params)
            config_path = write_yaml(merged_config, save_dir=TEMP_DIR, save_name=utils.random_string() + ".yaml")
            true_save_dir = os.path.join(self._save_dir, "Seed_" + str(random_seed), sub_save_dir)
            job = self.generate_single_script(save_dir=true_save_dir,
                                              seed=random_seed, hook_path=config_path)
            jobs.append(job)
        return jobs


if __name__ == '__main__':
    parser = argparse.ArgumentParser("udaiic method")
    parser.add_argument("--data-name", required=True, type=str, help="dataset_name",
                        choices=["acdc", "prostate", "mmwhsct"])
    parser.add_argument("--save_dir", required=True, type=str, help="save_dir")
    args = parser.parse_args()

    submittor = JobSubmiter(on_local=not on_cc(), project_path="../", time=4)
    submittor.prepare_env([
        "module load python/3.8.2 ",
        f"source ~/venv/bin/activate ",
        'if [ $(which python) == "/usr/bin/python" ]',
        "then",
        "exit 1314520",
        "fi",

        "export OMP_NUM_THREADS=1",
        "export PYTHONOPTIMIZE=1",
        "export PYTHONWARNINGS=ignore ",
        "export CUBLAS_WORKSPACE_CONFIG=:16:8 ",
        move_dataset()
    ])
    seed = [10, 20, 30]
    data_name = args.data_name
    save_dir = f"{args.save_dir}/udaiic/{data_name}"
    num_batches = num_batches_zoo[data_name]
    pre_max_epoch = pre_max_epoch_zoo[data_name]
    ft_max_epoch = ft_max_epoch_zoo[data_name]

    script_generator = DiscreteMIScriptGenerator(data_name=data_name, save_dir=save_dir, num_batches=num_batches,
                                                 max_epoch=ft_max_epoch)

    jobs = script_generator.grid_search_on(feature_names=[["Conv5", "Up_conv3", "Up_conv2"]],
                                           mi_weights=[[0.1, 0.05, 0.05], [0.25, 0.1, 0.1]],
                                           consistency_weight=[1, 5, 10], seed=seed)

    jobs2 = script_generator.grid_search_on(feature_names=["Conv5"],
                                            mi_weights=[0.1, 0.5, 0.1],
                                            consistency_weight=[1, 5, 10], seed=seed)
    for j in [*jobs, *jobs2]:
        print(j)
        submittor.account = next(account)
        submittor.run(j)
        # print(j)

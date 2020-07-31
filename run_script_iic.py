import argparse
from itertools import cycle

from deepclustering2.cchelper import JobSubmiter

parser = argparse.ArgumentParser()

parser.add_argument("-l", "--label_ratio", default=0.05, type=float)
parser.add_argument("-n", "--trainer_name", required=True, type=str)
parser.add_argument("-b", "--num_batches", default=100, type=int)
parser.add_argument("-s", "--random_seed", default=1, type=int)
parser.add_argument("-o", "--contrast_on", default="partition", type=str)
parser.add_argument("-c", "--num_clusters", default=40, type=int)
parser.add_argument("-t", "--temperature", default=1, type=float)
parser.add_argument("-g", "--group_sample_num", default=6, type=int)
parser.add_argument("--contrast_shuffle", default=False, action="store_true")
parser.add_argument("--save_dir", default=None, type=str)
parser.add_argument("--time", default=4, type=int)

args = parser.parse_args()

num_batches = args.num_batches
random_seed = args.random_seed

labeled_data_ratio = args.label_ratio
unlabeled_data_ratio = 1 - labeled_data_ratio

trainer_name = args.trainer_name
assert trainer_name == "iiccontrast"
contrast_on = args.contrast_on
save_dir_main = "iic_contrast_shuffle_and_largeN" if not args.save_dir else args.save_dir

save_dir = f"{save_dir_main}/label_data_ration_{labeled_data_ratio}/{trainer_name}/" \
           f"contrast_on_{contrast_on}/group_sample_num_{args.group_sample_num}/cluster_num_{args.num_clusters}/" \
           f"using_shuffle_{args.contrast_shuffle}/t_{args.temperature}"

common_opts = f" Trainer.name={trainer_name} PretrainEncoder.group_option={contrast_on} " \
              f" PretrainEncoder.num_clusters={args.num_clusters} RandomSeed={random_seed} " \
              f" Data.labeled_data_ratio={labeled_data_ratio} Data.unlabeled_data_ratio={unlabeled_data_ratio} " \
              f" Trainer.num_batches={num_batches} PretrainEncoder.temperature={args.temperature}  " \
              f" ContrastData.group_sample_num={args.group_sample_num} ContrastData.shuffule={args.contrast_shuffle}"
if trainer_name == "contrastMT":
    common_opts += f" FineTune.reg_weight={args.reg_weight} "

jobs = [
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/without_pretrain  Trainer.train_encoder=False Trainer.train_decoder=False ",
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/contrast_iic_0.0  Trainer.train_encoder=True Trainer.train_decoder=False PretrainEncoder.iic_weight=0.0",
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/contrast_iic_1.0  Trainer.train_encoder=True Trainer.train_decoder=False PretrainEncoder.iic_weight=1.0",
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/contrast_iic_3.0  Trainer.train_encoder=True Trainer.train_decoder=False PretrainEncoder.iic_weight=3.0",
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/contrast_iic_5.0  Trainer.train_encoder=True Trainer.train_decoder=False PretrainEncoder.iic_weight=5.0",
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/contrast_iic_10.0  Trainer.train_encoder=True Trainer.train_decoder=False PretrainEncoder.iic_weight=10.0",
    f"python -O main_contrast.py {common_opts} Trainer.save_dir={save_dir}/only_iic  Trainer.train_encoder=True Trainer.train_decoder=False PretrainEncoder.iic_weight=1 "
    f" PretrainEncoder.disable_contrastive=True ",
]

# CC things
accounts = cycle(["def-chdesa", "def-mpederso", "rrg-mpederso"])

jobsubmiter = JobSubmiter(project_path="./", on_local=False, time=args.time)
for j in jobs:
    jobsubmiter.prepare_env(["source ./venv/bin/activate ", "export OMP_NUM_THREADS=1", ])
    jobsubmiter.account = next(accounts)
    jobsubmiter.run(j)


conda_env=/path/to/user/SWE/miniconda3

source $conda_env/bin/activate
python -m swesmith.train.traj_mgr.collect_trajs \
    --traj_dir /home/smith/out \
    --out_dir /home/smith/data
#!/bin/bash
# Wait for spark_build.sh, then start the Living Room trial.
cd ~/ithaca-recon
until grep -q BUILD_DONE build.log; do sleep 15; done
if ! grep -q 'imports ok' build.log; then echo 'build failed; not training' > train_living.log; exit 1; fi
bash spark_train.sh onXQLXhfyQv_cdz3fkt3 living_2k --data_factor 2 --no-normalize-world-space --normal_loss --dist_loss \
  --test_every 8 --max_steps 30000 --save_steps 7000 30000 --eval_steps 7000 30000 > train_living.log 2>&1

local_folder=./runs

rsync -azP  --exclude "*/*.png"  --exclude "*/*.pth" \
  beluga:/lustre04/scratch/jizong/Contrast-You/semi_seg/runs/0304_new/ \
  "${local_folder}/0304_new/"

#rsync -azP  --exclude "*/*.png"  --exclude "*/*.pth" \
#  beluga:/lustre04/scratch/jizong/Contrast-You/semi_seg/runs/0303_semi/ \
#  "${local_folder}/0303_semi/"


#rsync -azP  --exclude "*/*.png"  --exclude "*/tra/*/*.pth"\
#  shanxi1:/home/jizong/Contrast-You/semi_seg/runs/0303 \
#  "${local_folder}/shanxi1/"

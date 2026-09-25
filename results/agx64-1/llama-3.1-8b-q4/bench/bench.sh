#!/bin/bash
set -u
O=/tmp/hm-bench; mkdir -p $O; cd $O
IMG=ghcr.io/fyberlabs/hypermesh-test@sha256:2d55427abb5fa0c5013985a7ef27a8821ae9046df165603219187c81923b2674
log(){ echo "[$(date -Is)] $*" | tee -a $O/progress.log; }
if [ -n "$(docker ps -q)" ]; then log "ABORT: containers running"; docker ps | tee -a $O/progress.log; exit 2; fi
date -Is > start_iso.txt; TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z' > start_et.txt
sudo nvpmodel -q > nvpmodel.txt 2>&1
sudo jetson_clocks --show > jetson_clocks.txt 2>&1
cat /etc/nv_tegra_release > l4t.txt
uname -a > uname.txt
docker image inspect $IMG --format '{{.Id}} {{json .RepoDigests}}' > image.txt
docker run --rm --runtime nvidia --entrypoint /bin/sh $IMG -c "llama-server --version" > version.txt 2>&1
RUNCMD="docker run -d --name hm-bench-probe --runtime nvidia --network host -v /home/chris/hypermesh/workload/model.gguf:/models/model.gguf:ro -e LLAMA_ARG_JINJA=1 -e LLAMA_ARG_CTX_SIZE=16384 -e LLAMA_ARG_TIMEOUT=1800 --entrypoint /bin/sh $IMG -c 'exec llama-server -m /models/model.gguf --host 127.0.0.1 --port 18080 --alias llama-3.1-8b-q4'"
echo "$RUNCMD" > run_cmd.txt
log "starting container"
eval "$RUNCMD" > cid.txt 2>&1 || { log "docker run failed"; cat cid.txt | tee -a progress.log; exit 3; }
ok=0
for i in $(seq 1 180); do
  if curl -s http://127.0.0.1:18080/health | grep -q '"ok"'; then ok=1; break; fi
  if [ -z "$(docker ps -q -f name=hm-bench-probe)" ]; then break; fi
  sleep 2
done
curl -s http://127.0.0.1:18080/health > health.txt
if [ $ok = 1 ]; then
  log "healthy after ~$((i*2))s; benching"
  python3 /tmp/bench.py 7 >> progress.log 2>&1 || log "bench.py failed"
else
  log "not healthy"
fi
curl -s http://127.0.0.1:18080/v1/models > models.json
sudo nvpmodel -q > nvpmodel_after.txt 2>&1
docker logs hm-bench-probe > server_full.log 2>&1
grep -iE "offload|CUDA|n_ctx|build|system_info|model type|model params|file type|ngl|gpu" server_full.log | head -80 > server_log_excerpt.txt
docker rm -f hm-bench-probe >> progress.log 2>&1
log "container removed; ps: $(docker ps -a -q -f name=hm-bench-probe | wc -l) remaining"
docker ps > docker_ps_after.txt
log "hashing model"
sha256sum /home/chris/hypermesh/workload/model.gguf > model_sha256.txt
date -Is > end_iso.txt
log "DONE"

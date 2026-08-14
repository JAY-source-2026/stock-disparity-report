#!/usr/bin/env bash
# 오라클 Ubuntu 22.04 VM 에서 대시보드 배포 셋업 (여러 번 실행해도 안전).
# 사용법:  cd ~/stock-disparity-report && bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
echo "▶ 앱 디렉터리: $APP_DIR"

echo "▶ 1/4 시스템 패키지 설치"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip git

echo "▶ 2/4 스왑 2GB (RAM 부족 대비; 이미 있으면 건너뜀)"
if ! sudo swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "▶ 3/4 파이썬 가상환경 + 의존성 설치"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip wheel
./.venv/bin/pip install -r requirements.txt

echo "▶ 4/4 내부 방화벽(iptables) 포트 열기: 8899, 80, 443"
# 오라클 우분투 이미지는 SSH 외 전부 차단 → 명시적으로 열어야 함.
for p in 8899 80 443; do
  sudo iptables -C INPUT -m state --state NEW -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$p" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save

echo ""
echo "✅ 셋업 완료."
echo "다음 순서:"
echo "  1) .env 작성        (nano .env  — TOSS_CLIENT_ID/SECRET 등)"
echo "  2) systemd 등록:"
echo "       sudo cp deploy/dashboard.service /etc/systemd/system/"
echo "       sudo systemctl daemon-reload"
echo "       sudo systemctl enable --now dashboard"
echo "  3) 접속 확인:  http://<서버_공인IP>:8899"

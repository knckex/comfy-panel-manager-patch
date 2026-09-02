# comfy-panel専用: ComfyUI-Managerを経由しない、自前のモデルダウンロードAPIを追加するだけの薄い派生イメージ。
# モデル本体は一切焼き込まない（今まで通りgenerate.php側で配置する）。
#
# 背景: ComfyUI-Managerの/manager/queue/install_modelは、自分の配布DBに完全一致するURLしか
# 受け付けない仕様（実機検証済み。security_level/channel_urlの変更では回避できないことも確認済み）。
# そこでComfyUI自体のWebサーバーに、自前の /comfy_panel/download エンドポイントを1本追加する。
#
# 配置先は /opt/comfyui-baked/custom_nodes/ 。ここはstart.shの初回セットアップ時（および
# Network Volume無しで毎回コンテナが作り直される場合は毎回）に /workspace/.../ComfyUI にコピーされる
# "焼き込み済みComfyUI"の実体なので、Network Volumeの有無に関わらずこのノードが確実に反映される。
FROM runpod/comfyui:latest

COPY comfy_panel_downloader /opt/comfyui-baked/custom_nodes/comfy_panel_downloader
